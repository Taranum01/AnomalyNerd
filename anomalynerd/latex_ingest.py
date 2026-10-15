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
from pathlib import Path

from .ingest import read_csv, read_tables, _parse_number
from .model import MISSING, TidyTable
from .pdf_ingest import ExtractedTable   # reuse the same status container



def expand_project(path: str, max_depth=20, max_bytes=5_000_000):
    """Expand local input/include only; never execute TeX or escape the project root."""
    root = Path(path).resolve().parent
    included, issues, segments = [], [], []
    budget = [0]
    def visit(file, stack):
        if not file.is_relative_to(root):
            issues.append(f"outside project root: {file.name}"); return ""
        if file in stack:
            issues.append(f"include cycle: {file.relative_to(root)}"); return ""
        if len(stack) >= max_depth:
            issues.append(f"include depth exceeded: {file.relative_to(root)}"); return ""
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            issues.append(f"unreadable include: {file.relative_to(root)}"); return ""
        budget[0] += len(text.encode())
        if budget[0] > max_bytes:
            issues.append("expanded project byte limit exceeded"); return ""
        included.append(str(file.relative_to(root)))
        text = _strip_comments(text)
        def sub(m):
            name = m.group(1) or m.group(2)
            child = (file.parent / name).resolve()
            if not child.suffix: child = child.with_suffix(".tex")
            return visit(child, stack + [file])
        return re.sub(r"\\(?:input|include)\s*(?:\{([^{}]+)\}|([^\s%{}]+))", sub, text)
    expanded = visit(Path(path).resolve(), [])
    return expanded, included, issues


def _strip_rules(text):
    # cmidrule's optional trim (lr) must be removed together with its range.
    return re.sub(r"\\(?:cmidrule|cline)\s*(?:\[[^\]]*\])?\s*(?:\([^)]*\))?\s*\{[^}]*\}|\\(?:toprule|midrule|bottomrule)\s*(?:\[[^\]]*\])?|\\hline", "", text)


def _split_cells(row):
    cells, current, depth = [], [], 0
    for i, char in enumerate(row):
        escaped = i > 0 and row[i-1] == "\\"
        if char == "{" and not escaped: depth += 1
        elif char == "}" and not escaped: depth -= 1
        if char == "&" and not escaped and depth == 0:
            cells.append("".join(current)); current = []
        else: current.append(char)
    cells.append("".join(current))
    return cells

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
    s = _strip_rules(c)
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
    # Uncertainty / plus-minus -> a normalized sentinel, handled AFTER brace/macro removal.
    # The spread is written many ways: \pm, {\pm}, ±, ∓, ascii "+-". Normalize them all now,
    # BEFORE the generic macro strip (which would otherwise delete \pm and fuse the numbers).
    s = re.sub(r"\{\s*\\pm\s*\}", " \u00b1 ", s)   # brace-wrapped {\pm}
    s = re.sub(r"\\pm(?![a-zA-Z])", " \u00b1 ", s)             # bare \pm
    s = re.sub(r"[\u00b1\u2213]", " \u00b1 ", s)     # unicode ± / ∓
    s = re.sub(r"(?<=\d)\s*\+\s*-\s*(?=\d)", " \u00b1 ", s)  # ascii "+-" between digits
    # strip any remaining simple commands (keep their trailing text)
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)
    s = s.replace("{", "").replace("}", "")
    # unescape common escaped characters
    s = s.replace("\\_", "_").replace("\\#", "#").replace("\\$", "$").replace("\\^", "^")
    s = re.sub(r"\s+", " ", s).strip()
    # FINAL: if the cell is "<central value> ± <spread> ...", keep only the central value.
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*\u00b1", s)
    if m:
        return m.group(1)
    s = s.replace("\u00b1", " ")
    # Also collapse a residual "<num> <num>" (central + spread with the sign already lost in
    # parsing) to the first number, so a mean/std pair does not read as non-numeric.
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s+-?\d+(?:\.\d+)?\s*$", s)
    if m:
        return m.group(1)
    return re.sub(r"\s+", " ", s).strip()


def _parse_tabular(body: str):
    """Parse a conservative rectangular grid, preserving multicolumn offsets."""
    b = body.lstrip()
    if b.startswith("["):
        b = b[b.find("]")+1:].lstrip()
    if b.startswith("{"):
        _, end = _balanced_braces(b, 0); b = b[end+1:].lstrip()
        # tabularx has both a width argument and a column specification.
        if b.startswith("{"):
            _, end = _balanced_braces(b, 0); b = b[end+1:]
    rows, active = [], {}
    for rr in re.split(r"\\\\(?:\[[^\]]*\])?", _strip_rules(b)):
        if not rr.strip(): continue
        values, next_active, col = [], {}, 0
        for raw in _split_cells(rr):
            # TeX multirow placeholders are normally explicit empty cells.
            span = re.search(r"\\multicolumn\s*\{(\d+)\}", raw)
            n = int(span.group(1)) if span else 1
            if n < 1 or n > 100: raise ValueError("unsupported column span")
            multi = re.search(r"\\multirow\s*\{(\d+)\}", raw)
            value = _clean_cell(raw)
            if col in active:
                prior, remain = active[col]
                if value: raise ValueError("multirow overlap; extraction ambiguous")
                value = prior
                if remain > 1: next_active[col] = (prior, remain-1)
            if multi:
                height = int(multi.group(1))
                if height < 1 or height > 100: raise ValueError("unsupported row span")
                if height > 1: next_active[col] = (value, height-1)
            values.extend([value] + [""]*(n-1)); col += n
        active = next_active
        if any(values): rows.append(values)
    if not rows: return [], []
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise ValueError("ragged structural grid; extraction ambiguous")
    # Combine leading all-text header rows into original compound labels.
    header = rows.pop(0)
    while rows and not any(_parse_number(c) is not MISSING for c in rows[0]):
        header = [" / ".join(v for v in (a,b) if v) for a,b in zip(header, rows.pop(0))]
    return header, rows


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
    included_files: list = field(default_factory=list)
    include_issues: list = field(default_factory=list)


def parse_latex(path_or_text: str) -> LatexParse:
    # accept a path to a .tex file, or raw tex text
    if os.path.exists(path_or_text) and path_or_text.endswith((".tex", ".txt")):
        tex, included, include_issues = expand_project(path_or_text)
        name = os.path.basename(path_or_text)
    else:
        tex = path_or_text
        included, include_issues = [], []
        name = "source"
    tex = _strip_comments(tex)
    stat_strings = _extract_stat_strings(tex)

    tables = []
    notes = []
    idx = 0
    for m in _TABULAR.finditer(tex):
        body = m.group(2)
        extraction_error = None
        try:
            header, rows = _parse_tabular(body)
        except ValueError as exc:
            header, rows = [], []
            extraction_error = str(exc)
        caption = _caption_before(tex, m.start())
        et = ExtractedTable(page=0, index_on_page=idx,
                            n_rows=len(rows), n_cols=len(header) if header else 0,
                            status="skipped")
        idx += 1
        et.source_text = m.group(0)
        et.source_path = path_or_text if included else "inline"
        et.source_locations = []
        if included:
            project_root = Path(path_or_text).resolve().parent
            for relative in sorted(set(included)):
                original = _strip_comments((project_root / relative).read_text(encoding="utf-8"))
                for match in re.finditer(re.escape(m.group(0)), original):
                    et.source_locations.append({"file":relative,"line":original[:match.start()].count("\n")+1})
        et.source_location_status = "exact" if len(et.source_locations)==1 else "ambiguous_or_unmapped"
        et.source_line = tex[:m.start()].count("\n") + 1
        et.source_line_basis = "expanded project text; inspect source_text and included_files for original file mapping"
        et.header = header
        et.rows = rows
        if extraction_error:
            et.reason = "extraction failure: " + extraction_error
            tables.append(et); continue
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
    return LatexParse(path=name, tables=tables, stat_strings=uniq, notes=notes + include_issues, included_files=included, include_issues=include_issues)
