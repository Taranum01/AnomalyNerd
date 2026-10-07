"""LaTeX-source ingest for AnomalyNerd.

The anomalies live in the *source* table, not the rendered PDF. A LaTeX ``tabular`` block
already delimits its cells with ``&`` and its rows with ``\\\\``, so parsing the source gives
us clean columns with no column-boundary guessing. For arXiv papers whose e-print source is
available, this is far more reliable than extracting tables from the compiled PDF.

parse_latex(path_or_text) finds every tabular/array environment (including those in the
appendix), converts each to an AnomalyNerd TidyTable, and also pulls reported-statistics
strings from the prose for statcheck/GRIM. Each table is reported with the same honest
status vocabulary as the PDF path (ok / partial / skipped + reason).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import csv, os, re, tempfile

from .ingest import read_csv, read_tables, _parse_number
from .model import MISSING, TidyTable
from .pdf_ingest import ExtractedTable   # reuse the same status container


# ----------------------------------------------------------------- latex cleaning
_COMMENT = re.compile(r"(?<!\\)%.*")
_TABULAR = re.compile(r"\\begin\{(tabular|tabularx|array|longtable)\}(.*?)\\end\{\1\}", re.S)
_CAPTION = re.compile(r"\\caption\*?\{", re.S)


def _strip_comments(tex: str) -> str:
    return "\n".join(_COMMENT.sub("", ln) for ln in tex.split("\n"))


def _balanced_braces(s: str, start: int) -> tuple:
    """Return (content, end_index) for a {...} group whose opening brace is at s[start]."""
    assert s[start] == "{"
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i
    return s[start + 1:], len(s)


def _clean_cell(c: str) -> str:
    s = c
    # \multicolumn{n}{align}{content} -> content
    while "\\multicolumn" in s:
        m = re.search(r"\\multicolumn\s*\{[^}]*\}\s*\{[^}]*\}\s*", s)
        if not m:
            break
        # take the following {content}
        j = m.end()
        if j < len(s) and s[j] == "{":
            content, end = _balanced_braces(s, j)
            s = s[:m.start()] + content + s[end + 1:]
        else:
            s = s[:m.start()] + s[j:]
    # \multirow{n}{w}{content} -> content
    while "\\multirow" in s:
        m = re.search(r"\\multirow\s*\{[^}]*\}\s*\{[^}]*\}\s*", s)
        if not m:
            break
        j = m.end()
        if j < len(s) and s[j] == "{":
            content, end = _balanced_braces(s, j)
            s = s[:m.start()] + content + s[end + 1:]
        else:
            s = s[:m.start()] + s[j:]
    # formatting commands with one brace arg: \textbf{x}\emph{x}\mathbf{x}\text{x} -> x
    s = re.sub(r"\\(textbf|textit|emph|mathbf|mathrm|text|textsc|underline|bm)\s*\{([^{}]*)\}",
               r"\2", s)
    # math delimiters and spacing
    s = s.replace("$", "").replace("\\,", "").replace("\\;", "").replace("~", " ")
    s = s.replace("\\%", "%").replace("\\&", "&")
    # cell color / rule commands
    s = re.sub(r"\\cellcolor\s*(\[[^\]]*\])?\s*\{[^}]*\}", "", s)
    s = re.sub(r"\\(rowcolor|hline|toprule|midrule|bottomrule|cline|cmidrule)\b(\s*\{[^}]*\}|\[[^\]]*\])*", "", s)
    # strip any remaining simple commands (keep their trailing text)
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)
    s = s.replace("{", "").replace("}", "")
    # unescape common escaped characters
    s = s.replace("\\_", "_").replace("\\#", "#").replace("\\$", "$").replace("\\^", "^")
    # +/- and uncertainty: keep the first number ("12.3 \pm 0.4" -> "12.3")
    s = s.replace("\\pm", " ").replace("±", " ")
    return re.sub(r"\s+", " ", s).strip()


def _parse_tabular(body: str):
    """Return (header, rows) of cleaned string cells from a tabular body."""
    # drop the column-spec argument that follows \begin{tabular}{...}
    b = body
    # the regex captured group 2 begins right after the environment name; the first {...}
    # (or [..]{..} for tabularx) is the column spec — remove a leading {...}.
    b = b.lstrip()
    if b.startswith("{"):
        _, end = _balanced_braces(b, 0)
        b = b[end + 1:]
    elif b.startswith("["):   # tabularx width arg then colspec
        rb = b.find("]")
        b = b[rb + 1:].lstrip()
        if b.startswith("{"):
            _, end = _balanced_braces(b, 0)
            b = b[end + 1:]
    # split rows on \\ (row break)
    raw_rows = re.split(r"\\\\", b)
    rows = []
    for rr in raw_rows:
        rr = rr.strip()
        if not rr:
            continue
        # a line that is only a rule command contributes no data row
        cells = [_clean_cell(c) for c in rr.split("&")]
        if all(c == "" for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return [], []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return rows[0], rows[1:]


def _numeric_fraction(values) -> float:
    vals = [v for v in values if v.strip() != ""]
    if not vals:
        return 0.0
    return sum(1 for v in vals if _parse_number(v) is not MISSING) / len(vals)


def _caption_before(tex: str, pos: int) -> str:
    """Find the nearest \\caption{...} within the enclosing table environment before pos."""
    window = tex[max(0, pos - 1500):pos]
    m = None
    for m in _CAPTION.finditer(window):
        pass
    if not m:
        return ""
    start = m.end() - 1  # the '{'
    content, _ = _balanced_braces(window, start)
    return _clean_cell(content)[:80]


# reuse the PDF path's reported-stat extraction
from .pdf_ingest import _extract_stat_strings


@dataclass
class LatexParse:
    path: str
    tables: list                 # list[ExtractedTable]
    stat_strings: list
    notes: list = field(default_factory=list)


def parse_latex(path_or_text: str) -> LatexParse:
    # accept a path to a .tex file, or raw tex text
    if os.path.exists(path_or_text) and path_or_text.endswith((".tex", ".txt")):
        tex = open(path_or_text, encoding="utf-8", errors="ignore").read()
        name = os.path.basename(path_or_text)
    else:
        tex = path_or_text
        name = "source"
    tex = _strip_comments(tex)
    stat_strings = _extract_stat_strings(tex)

    tables = []
    notes = []
    idx = 0
    for m in _TABULAR.finditer(tex):
        body = m.group(2)
        header, rows = _parse_tabular(body)
        caption = _caption_before(tex, m.start())
        et = ExtractedTable(page=0, index_on_page=idx,
                            n_rows=len(rows), n_cols=len(header) if header else 0,
                            status="skipped")
        idx += 1
        et.header = header
        et.rows = rows
        if len(rows) < 2 or len(header) < 2:
            et.reason = f"too small to analyze ({len(rows)+1}x{len(header)})"
            tables.append(et); continue
        # numeric content check
        col_vals = {header[i] if i < len(header) else f"col{i}":
                    [r[i] for r in rows if i < len(r)] for i in range(len(header))}
        numeric_cols = [h for h, vs in col_vals.items() if _numeric_fraction(vs) >= 0.6]
        if not numeric_cols:
            et.reason = "no numeric column found; nothing to check for value anomalies"
            tables.append(et); continue
        # de-dup/clean header names
        seen = {}; clean = []
        for i, h in enumerate(header):
            h = h or f"col{i}"
            if h in seen:
                seen[h] += 1; h = f"{h}_{seen[h]}"
            else:
                seen[h] = 0
            clean.append(h)
        try:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
            w = csv.writer(tmp); w.writerow(clean)
            for r in rows:
                w.writerow((r + [""] * len(clean))[:len(clean)])
            tmp.close()
            tidies = read_tables(tmp.name, name=caption or f"table{et.index_on_page}")
            os.unlink(tmp.name)
            et.tidies = tidies
            et.tidy = tidies[0] if tidies else None
            et.status = "ok"
            note = f" ('{caption}')" if caption else ""
            if len(tidies) > 1:
                note += f"; split into {len(tidies)} single-metric tables"
            et.reason = "parsed from LaTeX source" + note
        except Exception as e:
            et.status = "skipped"
            et.reason = f"could not convert to a results table: {type(e).__name__}: {e}"
        tables.append(et)

    if not tables:
        notes.append("no tabular environments found in the LaTeX source")
    # dedupe stat strings
    seen = set(); uniq = []
    for s in stat_strings:
        if s not in seen:
            seen.add(s); uniq.append(s)
    return LatexParse(path=name, tables=tables, stat_strings=uniq, notes=notes)
