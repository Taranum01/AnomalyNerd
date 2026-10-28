"""PDF ingest for AnomalyNerd.

Given a PDF article, extract the tables (on every page, including appendices) and
convert the usable ones into AnomalyNerd TidyTables, and pull the reported-statistics
strings out of the body text for the statistical/digit tests.

The guiding requirement is to be honest about what we CANNOT check. Every table
the PDF contains is reported with an extraction STATUS:
    - "ok"       : parsed into a numeric results table the detectors can run on
    - "partial"  : parsed but with caveats (few numeric columns, ragged rows, ...)
    - "skipped"  : detected but not analyzable (too small, no numeric content, ...)
    - "non_table": clear metadata/prose mis-detection, omitted from the uncheckable-table list
We never silently drop a plausible table; a skipped table is reported with the reason.
Only blocks with strong non-table evidence are omitted from that honesty section.

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
    status: str                      # "ok" | "partial" | "skipped" | "non_table"
    reason: str = ""                 # why partial/skipped (human readable)
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)   # raw cell rows (list[list[str]])
    tidy: Optional[TidyTable] = None # first metric table (back-compat)
    tidies: list = field(default_factory=list)  # one tidy table per metric
    caveats: list = field(default_factory=list)
    original_header: list = field(default_factory=list)
    source_path: str = ""
    backend: str = "pdfplumber"  # which extractor produced this table


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
    """Collapse whitespace in a cell. None becomes an empty string."""
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", str(cell)).strip()


def _numeric_fraction(values) -> float:
    """Share of the non-empty values that parse as numbers."""
    vals = [v for v in values if _clean(v) != ""]
    if not vals:
        return 0.0
    got = sum(1 for v in vals if _parse_number(_clean(v)) is not MISSING)
    return got / len(vals)


def _longest_numeric_run(values) -> int:
    """Longest run of consecutive numeric cells in a column, ignoring blanks; a non-numeric
    non-blank cell breaks the run. A real results column shows a solid block of numbers even
    when the extractor merged prose fragments into the same column."""
    best = cur = 0
    for v in values:
        c = _clean(v)
        if c == "":
            continue
        if _parse_number(c) is not MISSING:
            cur += 1; best = max(best, cur)
        else:
            cur = 0
    return best


def _numeric_multiset(rows) -> dict:
    """Multiset of the parsed numeric cell values across table body rows."""
    counts = {}
    for row in rows:
        for cell in row:
            v = _parse_number(_clean(cell))
            if v is not MISSING:
                counts[v] = counts.get(v, 0) + 1
    return counts


def _shares_numeric_core(docling_et, merged_et) -> bool:
    """True when a Docling-isolated table carries the numeric core of a merged stand-in:
    at least 4 shared numeric cell values, the same run length that marked the stand-in
    as merged. Used to replace only the stand-ins the layout model actually recovered."""
    a = _numeric_multiset(merged_et.rows)
    b = _numeric_multiset(docling_et.rows)
    shared = sum(min(a[v], b[v]) for v in a.keys() & b.keys())
    return shared >= 4


def _looks_like_header(row) -> bool:
    """A header row is mostly non-numeric labels."""
    cells = [_clean(c) for c in row if _clean(c) != ""]
    if not cells:
        return False
    numeric = sum(1 for c in cells if _parse_number(c) is not MISSING)
    return numeric <= len(cells) * 0.4


# metadata labels found on journal submission cover sheets / front matter -- a strong signal
# that a detected "table" is really a form or prose block, not a results table.
_METADATA_LABELS = (
    "manuscript number", "article type", "keywords", "corresponding author",
    "order of authors", "abstract", "--manuscript draft--", "submission", "cover letter",
    "funding information", "classifications", "suggested reviewers", "opposed reviewers",
)


def _looks_like_non_table(rows) -> bool:
    """True only when a detected block is clearly NOT a results table.

    Silent dropping needs stronger evidence than "mostly words": qualitative results tables
    can legitimately contain no numbers. The safe cases are (1) front-matter metadata forms
    and (2) prose-shaped blocks: rows with one or two long sentence-like cells, such as an
    abstract or body paragraph split into lines. Ambiguous wordy blocks remain ordinary
    skipped tables so the report still says they could not be checked.
    """
    cells = [_clean(c) for r in rows for c in r if _clean(c) != ""]
    if not cells:
        return True
    joined = " ".join(cells).lower()
    # metadata/cover-sheet labels
    if sum(1 for lab in _METADATA_LABELS if lab in joined) >= 2:
        return True

    numeric = sum(1 for c in cells if _parse_number(c) is not MISSING)
    if numeric > 0.05 * len(cells):
        return False

    nonempty_rows = [[_clean(c) for c in r if _clean(c) != ""] for r in rows]
    nonempty_rows = [r for r in nonempty_rows if r]
    if len(nonempty_rows) < 3:
        return False
    prose_rows = sum(1 for r in nonempty_rows if len(r) <= 2)
    sentence_like = sum(1 for c in cells if _looks_sentence_like(c))
    return prose_rows >= 0.8 * len(nonempty_rows) and sentence_like >= max(2, 0.6 * len(cells))


def _looks_sentence_like(c: str) -> bool:
    """A long natural-language cell, usually ending like a sentence."""
    words = c.split()
    if len(c) < 50 or len(words) < 7:
        return False
    letters = sum(ch.isalpha() for ch in c)
    if letters < 0.6 * len(c):
        return False
    return bool(re.search(r"[.!?](?:\s|$)", c)) or len(words) >= 10


def _looks_like_shredded_caption(row) -> bool:
    """True when a header row is explicitly caption-like AND shredded into fragments.

    Short cells alone are not enough: legitimate results tables often use A/B/C/D or other
    compact column codes. Require a caption marker such as 'Table 6' (or the common
    extraction-truncated 'able 6 ...') before replacing the row's names.
    """
    cells = [_clean(c) for c in row]
    nonempty = [c for c in cells if c != ""]
    if len(nonempty) < 3:
        return False
    first = (nonempty[0] or "").strip()
    caption_marker = bool(re.match(r"(?i)^(table|fig(ure)?|able|ig(ure)?)\b.*\d", first)) \
        or bool(re.match(r"(?i)^(table|figure)\s*\d", first))
    if not caption_marker:
        return False
    return caption_marker

_SIZE_CELL = re.compile(r"^\d+(?:\.\d+)?\s?[kKMB]$")


def _merge_size_subheader(header, body):
    """Fold a second header row of model sizes into the header.

    Comparison tables often put the model size under each model name ("GPT-3" over "175B",
    "Gopher" over "280B"). That row has no row label, and every value in it is a size; the rows
    below hold plain scores. Read as data, its sizes (1.75e11) look like huge outliers. When the
    first body row matches that shape, append each size to its column name ("GPT-3 175B") and
    drop the row. A size row that is real data (a column of sizes with a label) is left alone."""
    if len(body) < 3:
        return header, body
    first = body[0]
    filled = [i for i, c in enumerate(first) if c.strip()]
    if len(filled) < 2 or not all(_SIZE_CELL.match(first[i].strip()) for i in filled):
        return header, body
    if first[0].strip():                     # a row label means this row is data
        return header, body
    # below it, those columns must hold plain values, not more sizes
    below = [r[i].strip() for r in body[1:] for i in filled if i < len(r) and r[i].strip()]
    if not below or sum(1 for c in below if _SIZE_CELL.match(c)) > 0.2 * len(below):
        return header, body
    new_header = list(header)
    for i in filled:
        if i < len(new_header):
            name = new_header[i].strip()
            new_header[i] = f"{name} {first[i].strip()}" if name else first[i].strip()
    return new_header, body[1:]


def _assess_and_tidy(tbl, page, idx, structured=False) -> ExtractedTable:
    """Turn a raw table into an ExtractedTable with an honest status.

    structured=True means the grid came from a format that stores real cells (Word, Excel), not
    from PDF layout extraction. Such a grid cannot be a figure or prose that an extractor misread
    as a table, so the two PDF-fragmentation heuristics (mostly 1-2 character cells; mostly empty
    grid) are skipped: they would wrongly reject clean tables of short values like '28' or '-4'.
    Every other gate (too small, no numeric column, too little numeric content) still applies."""
    rows = [[_clean(c) for c in r] for r in (tbl or [])]
    rows = [r for r in rows if any(c != "" for c in r)]
    n_rows = len(rows)
    n_cols = max((len(r) for r in rows), default=0)

    et = ExtractedTable(page=page, index_on_page=idx, n_rows=n_rows, n_cols=n_cols,
                        status="skipped")
    if n_rows < 3 or n_cols < 2:
        et.reason = f"too small to analyze ({n_rows}x{n_cols}); need >=3 rows and >=2 columns"
        return et

    # Drop obvious non-tables (submission cover sheets, metadata forms, prose split into fake
    # columns) silently: they are not results tables, so reporting them as "could not check"
    # is just noise that hides the true coverage.
    if _looks_like_non_table(rows):
        et.status = "non_table"
        et.reason = "not a results table (metadata/cover sheet or prose); skipped"
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
    et.original_header = list(header)
    body = norm[1:]
    header, body = _merge_size_subheader(header, body)
    # Numeric metric words split across boundaries are evidence of a shredded grid.
    fragmented_metric = any(re.match(r"(?i)^(?:[a-z]\s*)?\((?:ms|s|%|kg|g)\)", c.strip()) for c in header)
    if fragmented_metric:
        et.header = list(header); et.rows = body
        et.reason = "extraction failure: metric header split across columns; coordinates unverified"
        return et
    # Never run numerical detectors on unidentified or caption-shredded coordinates.
    if not _looks_like_header(header) or _looks_like_shredded_caption(header):
        et.header = list(header)
        et.rows = body
        et.reason = "extraction failure: unidentified or shredded original header; coordinates unverified"
        return et
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
        # A column with a solid numeric RUN but low overall numeric fraction is a real results
        # table merged with surrounding prose text. Mark it so (status "merged") so the layout
        # recovery step can try to isolate the real table via the layout model.
        if any(_longest_numeric_run(vs) >= 4 for vs in col_vals.values()):
            et.status = "merged"
            et.reason = ("a results table was detected but it is merged with surrounding text; "
                         "attempting layout-model isolation")
        else:
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
    if tiny_frac > 0.6 and not structured:
        et.reason = (f"{tiny_frac:.0%} of cells are 1-2 characters; looks like fragmented "
                     f"figure/text rather than a results table")
        return et
    # A chart whose axis tick labels and legend were read as a grid: the cells are sparse, and
    # many rows hold a single lone value (one tick label per line). A real results table puts a
    # label and its numbers on the same row. Both conditions are required: on the 56 real
    # tables in our test papers, the sparsest (42% filled) has no single-value rows, and the one
    # with the most single-value rows (a checklist, 33%) is 78% filled.
    single_row_frac = (sum(1 for r in body if sum(1 for c in r if c != "") == 1)
                       / float(max(1, sum(1 for r in body if any(c != "" for c in r)))))
    if grid_fill < 0.5 and single_row_frac >= 0.25 and not structured:
        et.reason = (f"looks like a chart read as a table (only {grid_fill:.0%} of the grid is "
                     f"filled and {single_row_frac:.0%} of rows hold a single value, as axis "
                     f"labels do); not analyzed")
        return et
    if grid_fill < 0.4 and not structured:
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
from .stat_patterns import TEST_PATTERNS

_STAT_PATTERNS = [*TEST_PATTERNS,
    re.compile(r"\bM\s*=\s*(-?\d+\.?\d*)\s*,?\s*SD\s*=\s*(\d+\.?\d*)\s*,?\s*N\s*=\s*(\d+)", re.I),
]


def _extract_stat_strings(text: str) -> list:
    """Find reported statistics in text: test results with a p-value (t, F, r, chi-square, z) and "M, SD, N" triples."""
    out = []
    for pat in _STAT_PATTERNS:
        for m in pat.finditer(text):
            out.append(m.group(0).strip())
    return out


# ----------------------------------------------------------------- main entry
def parse_pdf(path: str) -> PdfParse:
    """Extract tables and reported statistic strings from a PDF."""
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
                    sig = (tuple(et.header), tuple(tuple(r) for r in et.rows))
                    if sig in seen_sig:
                        continue
                    seen_sig.add(sig); usable.append(et)
                else:
                    skipped.append(et)
            # Keep skipped extraction attempts visible alongside usable alternatives.
            kept = usable + _dedupe_skipped(skipped)
            for idx, et in enumerate(kept):
                et.index_on_page = idx
                et.source_path = path
                tables.append(et)

    # Fallback: if pdfplumber found no usable results table, try Docling, a layout-model
    # extractor (DocLayNet + TableFormer) that reads the borderless tables common in research
    # papers which the coordinate-based strategies above miss. Docling is an OPTIONAL heavy
    # dependency; if it is not installed we simply skip this step and report honestly.
    usable_so_far = sum(1 for t in tables if t.status in ("ok", "partial"))
    merged_tables = [t for t in tables if t.status == "merged"]
    # Run the layout model (Docling) when pdfplumber found NO usable table at all, OR when it
    # found tables that are merged with surrounding text. Docling's layout model isolates the
    # true table region, so it can recover a clean results table that pdfplumber merged with
    # prose. Clean Docling tables are ADDED; the merged pdfplumber stand-ins they replace are
    # dropped (we keep them only if Docling recovers nothing, so the gap is still reported).
    if usable_so_far == 0 or merged_tables:
        docling_tables, docling_note = _try_docling(path)
        if docling_note:
            notes.append(docling_note)
        if docling_tables:
            if merged_tables:
                # Docling re-detects EVERY table region on the page, including tables
                # pdfplumber already parsed cleanly. Drop those duplicates (the same
                # signature test as the per-page dedupe above) so no table is analyzed twice.
                kept_sigs = {(tuple(t.header), tuple(tuple(r) for r in t.rows))
                             for t in tables if t.status != "merged"}
                fresh_docling = []
                for dt in docling_tables:
                    sig = (tuple(dt.header), tuple(tuple(r) for r in dt.rows))
                    if sig in kept_sigs:
                        continue
                    kept_sigs.add(sig)
                    fresh_docling.append(dt)
                # Replace only the merged stand-ins the layout model actually recovered. A
                # merged table it could not isolate is downgraded to an honest "could not
                # isolate" skip, so the gap is reported, never silently dropped.
                for t in merged_tables:
                    if not any(_shares_numeric_core(dt, t) for dt in fresh_docling):
                        t.status = "skipped"
                        t.reason = ("a results table was detected but it is merged with "
                                    "surrounding text and the layout model could not isolate "
                                    "it; not analyzed")
                tables = [t for t in tables if t.status != "merged"] + fresh_docling
            else:
                tables = tables + docling_tables
        else:
            # Docling recovered nothing: downgrade the merged markers to an honest "could not
            # separate" skip so they are reported (not silently dropped, not falsely analyzed).
            for t in merged_tables:
                t.status = "skipped"
                t.reason = ("a results table was detected but it is merged with surrounding "
                            "text and the layout model could not isolate it; not analyzed")

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


def _try_docling(path: str):
    """Run Docling (if installed) and convert its tables to ExtractedTables via the same
    _assess_and_tidy path. Returns (list_of_ExtractedTable, note_or_None). Forces CPU because
    Docling's layout model uses float64, which the Mac MPS backend rejects."""
    # PyTorch reads PYTORCH_ENABLE_MPS_FALLBACK when it registers the MPS dispatch fallback,
    # normally when torch is first loaded, not at each operation. Set it before importing
    # Docling so a fresh process can use CPU for unsupported MPS ops. This is process-wide:
    # restoring os.environ does not unregister a fallback that PyTorch already registered.
    import os as _os
    _prev_fallback = _os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK")
    _os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    try:
        try:
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions, AcceleratorOptions, AcceleratorDevice)
        except Exception:
            return [], ("a layout-model fallback (Docling) is not installed, so borderless tables "
                        "that the default extractor cannot read were not recovered; "
                        "`pip install docling` to enable it")
        try:
            opts = PdfPipelineOptions()
            opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
            # Disable OCR. AnomalyNerd analyzes text-based research PDFs, where the text layer
            # is already present, so Docling is used only for table STRUCTURE, not OCR. Leaving
            # OCR on (Docling's default) invokes the RapidOCR/ONNX native stack, which aborts
            # the whole process (SIGABRT / "Abort trap: 6") on some PDFs -- a native crash that
            # a Python try/except cannot catch. Turning OCR off removes that crash path and also
            # speeds up conversion; a genuinely scanned table would not yield trustworthy
            # numbers to analyze anyway.
            opts.do_ocr = False
            conv = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
            doc = conv.convert(path).document
        except Exception as e:
            return [], f"the Docling fallback failed to convert this PDF ({type(e).__name__})"
    finally:
        if _prev_fallback is None:
            _os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
        else:
            _os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = _prev_fallback
    out = []
    skipped_tables = 0
    for tb in getattr(doc, "tables", []):
        raw = _docling_table_to_rows(tb, doc)
        if raw is None or len(raw) < 2:
            skipped_tables += 1
            continue
        et = _assess_and_tidy(raw, 0, len(out))
        et.backend = "docling"
        if et.status in ("ok", "partial"):
            if "recovered by the Docling layout-model fallback" not in (et.reason or ""):
                et.caveats.append("recovered by the Docling layout-model fallback")
            out.append(et)
    note = None
    if not out:
        note = "the Docling fallback ran but still produced no usable results table"
        if skipped_tables:
            note += f" ({skipped_tables} table(s) could not be exported)"
    return out, note


def _docling_table_to_rows(tb, doc):
    """Robustly turn one Docling table into a header+rows list, trying several export paths
    across Docling versions. Returns None if every path fails (the table is skipped without
    failing the whole fallback). Handles the AttributeError/TypeError differences between
    export_to_dataframe(doc), export_to_dataframe(), and the raw grid-cell structure."""
    # 1) dataframe with the doc argument (newer API)
    for call in (lambda: tb.export_to_dataframe(doc), lambda: tb.export_to_dataframe()):
        try:
            df = call()
            header = [str(c) for c in df.columns]
            body = [[("" if v is None else str(v)) for v in row]
                    for row in df.values.tolist()]
            return [header] + body
        except (TypeError, AttributeError):
            continue
        except Exception:
            continue
    # 2) raw grid cells (table_cells / data.grid), tolerant of schema differences
    try:
        data = getattr(tb, "data", None)
        grid = getattr(data, "grid", None) if data is not None else None
        if grid:
            rows = []
            for row in grid:
                rows.append([("" if c is None else str(getattr(c, "text", c))).strip()
                             for c in row])
            rows = [r for r in rows if any(c for c in r)]
            if len(rows) >= 2:
                return rows
    except Exception:
        pass
    return None


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
