"""PDF ingest for AnomalyNerd.

Given a PDF article, extract the tables (on every page, including appendices) and
convert the usable ones into AnomalyNerd TidyTables, and pull the reported-statistics
strings out of the body text for the statistical/digit tests.

The guiding requirement is to be honest about what we CANNOT check. Every table
the PDF contains is reported with an extraction STATUS:
    - "ok"       : parsed into a numeric results table the detectors can run on
    - "partial"  : parsed but with caveats (few numeric columns, ragged rows, ...)
    - "skipped"  : detected but not analyzable (too small, no numeric content, ...)
We never silently drop a table; a skipped table is reported with the reason.

PDF table extraction is inherently unreliable (multi-column layouts, merged cells,
image-based tables). This module reports its own uncertainty rather than hiding it.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import csv, io, os, re, tempfile

import pdfplumber

from .ingest import read_csv, read_tables, _parse_number, _looks_numeric
from .model import MISSING, TidyTable


# ----------------------------------------------------------------- data classes
@dataclass
class ExtractedTable:
    """One table found in the PDF, with its extraction status."""
    page: int
    index_on_page: int
    n_rows: int
    n_cols: int
    status: str                      # "ok" | "partial" | "skipped"
    reason: str = ""                 # why partial/skipped (human readable)
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)   # raw cell rows (list[list[str]])
    tidy: Optional[TidyTable] = None # first metric table (back-compat)
    tidies: list = field(default_factory=list)  # one tidy table per metric
    caveats: list = field(default_factory=list)


@dataclass
class PdfParse:
    """Everything we got out of the PDF."""
    path: str
    n_pages: int
    tables: list                     # list[ExtractedTable]
    stat_strings: list               # reported-statistics snippets from body text
    text_pages: int = 0              # pages we got text from
    notes: list = field(default_factory=list)   # document-level honesty notes


# ----------------------------------------------------------------- helpers
def _clean(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _numeric_fraction(values) -> float:
    vals = [v for v in values if _clean(v) != ""]
    if not vals:
        return 0.0
    got = sum(1 for v in vals if _parse_number(_clean(v)) is not MISSING)
    return got / len(vals)


def _looks_like_header(row) -> bool:
    """A header row is mostly non-numeric labels."""
    cells = [_clean(c) for c in row if _clean(c) != ""]
    if not cells:
        return False
    numeric = sum(1 for c in cells if _parse_number(c) is not MISSING)
    return numeric <= len(cells) * 0.4


def _assess_and_tidy(tbl, page, idx) -> ExtractedTable:
    """Turn a raw pdfplumber table into an ExtractedTable with an honest status."""
    rows = [[_clean(c) for c in r] for r in (tbl or [])]
    rows = [r for r in rows if any(c != "" for c in r)]
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)

    et = ExtractedTable(page=page, index_on_page=idx, n_rows=n_rows, n_cols=n_cols,
                        status="skipped")
    if n_rows < 3 or n_cols < 2:
        et.reason = f"too small to analyze ({n_rows}x{n_cols}); need >=3 rows and >=2 columns"
        return et

    # normalize ragged rows to the modal width
    widths = [len(r) for r in rows]
    width = max(set(widths), key=widths.count)
    ragged = sum(1 for w in widths if w != width)
    norm = []
    for r in rows:
        if len(r) < width:
            r = r + [""] * (width - len(r))
        elif len(r) > width:
            r = r[:width]
        norm.append(r)

    # split header vs body
    header = norm[0]
    body = norm[1:]
    # if the first row isn't label-like, synthesize column names
    if not _looks_like_header(header):
        header = [f"col{i}" for i in range(width)]
        body = norm
    # de-duplicate / fill blank header names
    seen = {}
    clean_header = []
    for i, h in enumerate(header):
        h = h or f"col{i}"
        if h in seen:
            seen[h] += 1; h = f"{h}_{seen[h]}"
        else:
            seen[h] = 0
        clean_header.append(h)

    # how many columns are numeric enough to be a metric/axis?
    col_vals = {clean_header[i]: [r[i] for r in body] for i in range(width)}
    numeric_cols = [h for h, vs in col_vals.items() if _numeric_fraction(vs) >= 0.6]

    et.header = clean_header
    et.rows = body
    if len(numeric_cols) == 0:
        et.reason = "no numeric column found; nothing to check for value anomalies"
        return et

    # --- quality gate: reject fragmented / figure-like "tables" -----------------
    # PDF extractors frequently misread figures, rotated text, or multi-column prose
    # as a table of many narrow columns full of 1-2 character fragments. Such a parse
    # is not a results table; flag it honestly rather than analyzing noise.
    all_cells = [c for r in body for c in r if c != ""]
    if all_cells:
        tiny_frac = sum(1 for c in all_cells if len(c) <= 2) / len(all_cells)
    else:
        tiny_frac = 1.0
    overall_numeric = _numeric_fraction([c for r in body for c in r])
    grid_fill = len(all_cells) / float(len(body) * width)   # non-empty cell fraction

    if width > 12 and len(numeric_cols) < 0.3 * width:
        et.reason = (f"detected as {len(body)}x{width} but only {len(numeric_cols)} of "
                     f"{width} columns are numeric; looks like fragmented figure/text, "
                     f"not a results table")
        return et
    if tiny_frac > 0.6:
        et.reason = (f"{tiny_frac:.0%} of cells are 1-2 characters; looks like fragmented "
                     f"figure/text rather than a results table")
        return et
    if grid_fill < 0.4:
        et.reason = (f"only {grid_fill:.0%} of the grid is filled; too sparse to be a "
                     f"reliable results table")
        return et
    if overall_numeric < 0.2:
        et.reason = (f"only {overall_numeric:.0%} of cells are numeric; not enough numeric "
                     f"content to check for value anomalies")
        return et

    if ragged:
        et.caveats.append(f"{ragged} row(s) had an irregular column count and were "
                          f"padded/truncated to width {width}")
    if len(numeric_cols) < 1:
        et.caveats.append("only one numeric column; limited pattern checks possible")

    # write to a temp CSV and reuse the hardened read_tables schema inference. read_tables
    # splits a wide multi-metric table (different scales => different metrics) into one tidy
    # table per metric, so the detectors never compare unlike quantities.
    try:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        w = csv.writer(tmp); w.writerow(clean_header)
        for r in body:
            w.writerow(r)
        tmp.close()
        tidies = read_tables(tmp.name, name=f"page{page}_table{idx}")
        os.unlink(tmp.name)
        et.tidies = tidies
        et.tidy = tidies[0] if tidies else None
        if len(tidies) > 1:
            et.caveats.append(f"wide table split into {len(tidies)} single-metric tables "
                              f"(columns have different scales)")
        et.status = "partial" if (ragged or et.caveats) else "ok"
        if et.status == "ok" and not et.caveats:
            et.reason = "parsed cleanly"
        else:
            et.reason = "parsed with caveats"
    except Exception as e:   # extraction produced something read_csv can't model
        et.status = "skipped"
        et.reason = f"could not convert to a results table: {type(e).__name__}: {e}"
    return et


# ----------------------------------------------------------------- stat strings
# Capture reported statistics from body text for the statistical/digit tests.
# Examples: "t(38) = 2.10, p = .03"  "F(2, 57) = 4.5, p < .01"  "M = 3.42, SD = 1.1, N = 50"
_STAT_PATTERNS = [
    re.compile(r"\b([tFrχχ2χ²zZ]|chi2|chi-square)\s*\(?\s*([\d,\.\s]*)\)?\s*=\s*(-?\d+\.?\d*)\s*,?\s*p\s*([=<>])\s*\.?(\d+\.?\d*)", re.I),
    re.compile(r"\bM\s*=\s*(-?\d+\.?\d*)\s*,?\s*SD\s*=\s*(\d+\.?\d*)\s*,?\s*N\s*=\s*(\d+)", re.I),
]


def _extract_stat_strings(text: str) -> list:
    out = []
    for pat in _STAT_PATTERNS:
        for m in pat.finditer(text):
            out.append(m.group(0).strip())
    return out


# ----------------------------------------------------------------- main entry
def parse_pdf(path: str) -> PdfParse:
    tables = []
    stat_strings = []
    text_pages = 0
    notes = []
    with pdfplumber.open(path) as pdf:
        n_pages = len(pdf.pages)
        for pi, page in enumerate(pdf.pages, start=1):
            # text (for reported-stats detection)
            try:
                txt = page.extract_text() or ""
                if txt.strip():
                    text_pages += 1
                    stat_strings += _extract_stat_strings(txt)
            except Exception:
                notes.append(f"page {pi}: text extraction failed")

            # tables: try the lines-based strategy first, then a text-alignment strategy as a
            # fallback. Academic papers often use borderless tables that the lines strategy
            # cannot see, so the text strategy recovers real results tables the first misses.
            candidates = []   # list of (raw_table)
            try:
                candidates += (page.extract_tables() or [])
            except Exception as e:
                notes.append(f"page {pi}: table extraction raised {type(e).__name__}")
            try:
                text_settings = {"vertical_strategy": "text", "horizontal_strategy": "text",
                                 "snap_y_tolerance": 5, "min_words_vertical": 2}
                candidates += (page.extract_tables(text_settings) or [])
            except Exception:
                pass

            # assess all candidates; keep usable ones, and keep skipped ones only if we did
            # not already keep a usable table at the same (approx) size on this page.
            page_tables = []
            for rt in candidates:
                et = _assess_and_tidy(rt, pi, len(page_tables))
                page_tables.append(et)
            # prefer usable tables; drop duplicate usable tables with the same shape+metric
            usable, skipped, seen_sig = [], [], set()
            for et in page_tables:
                if et.status in ("ok", "partial") and et.tidy is not None:
                    sig = (et.n_rows, et.n_cols, et.tidy.metric_name)
                    if sig in seen_sig:
                        continue
                    seen_sig.add(sig); usable.append(et)
                else:
                    skipped.append(et)
            # if any usable table was found on this page, only report those (the skipped
            # entries are near-duplicates/noise from the other strategy); otherwise report the
            # skipped ones so the honesty section still explains what was attempted.
            kept = usable if usable else _dedupe_skipped(skipped)
            for idx, et in enumerate(kept):
                et.index_on_page = idx
                tables.append(et)

    if not tables:
        notes.append("no tables were detected in this PDF; if the paper has tables, "
                     "they may be images or use a layout the extractor cannot read")
    # dedupe stat strings, preserve order
    seen = set(); uniq = []
    for s in stat_strings:
        if s not in seen:
            seen.add(s); uniq.append(s)
    return PdfParse(path=path, n_pages=n_pages, tables=tables,
                    stat_strings=uniq, text_pages=text_pages, notes=notes)


def _dedupe_skipped(skipped):
    """Collapse near-identical skipped entries (both strategies produce similar noise) so the
    honesty section is not flooded with duplicate 'too small' lines for the same page."""
    out, seen = [], set()
    for et in skipped:
        sig = (et.n_rows, et.n_cols, et.reason[:40])
        if sig in seen:
            continue
        seen.add(sig); out.append(et)
    return out
