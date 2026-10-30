"""End-to-end PDF analysis for AnomalyNerd.

Ties the pieces together:
    1. parse_pdf()        -> tables (with extraction status) + reported-stat strings
    2. analyze()          -> the 8 scientific-pattern detectors on each usable table
    3. statcheck/grim/digit_tests -> statistical + digit checks
    4. a report that leads with WHAT WAS CHECKED and, crucially, WHAT COULD NOT BE CHECKED.

Design principle: say what you don't know. The report always contains a
"Could not check" section listing every skipped table (with the reason) and every test
that was not applicable (with the reason). A clean PDF with no findings still produces a
report that explains exactly what was and was not examined.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .pdf_ingest import parse_pdf
from .analyze import analyze, is_pathological, score
from .stat_tests import statcheck, grim, grimmer, digit_tests
from .cross_table import find_cross_table_inconsistencies


@dataclass
class PdfAnalysis:
    """Everything found in one article: parsed tables, flags per table, statistical findings, and what could not be checked."""
    path: str
    parse: object                      # PdfParse or LatexParse
    table_flags: dict = field(default_factory=dict)   # (page,idx) -> list[Flag]
    stat_findings: list = field(default_factory=list) # list[StatFinding]
    could_not_check: list = field(default_factory=list)
    non_table_count: int = 0           # clear metadata/prose blocks omitted from that list
    detector_runs: dict = field(default_factory=dict)
    source_kind: str = "pdf"           # "pdf" | "latex" | "docx" | "xlsx"
    cross_table_flags: list = field(default_factory=list)


_UNIT = {"docx": "table", "xlsx": "sheet"}   # what a "page" number means per format
_LABEL = {"latex": "LaTeX source", "docx": "Word document", "xlsx": "Excel workbook"}


import re as _re

_PARAM_COUNT = _re.compile(r"^\s*\d[\d.,]*\s*[MB]\s*$")


def _has_fused_cells(table):
    """True when a cell still holds two separate numbers after repair, so rows are misaligned."""
    from .pdf_ingest import is_fused
    return any(is_fused(c) for r in (table.rows or []) for c in r)


def _is_param_count_column(tidy):
    """True when the metric column is a list of model sizes (7B, 30B, 937M): every number in
    it carries an M or B suffix and the header names parameters or is blank. The largest
    size always stands out, so outlier flags on such a column are not reported."""
    name = (tidy.metric_name or "").lower()
    leaf = name.rsplit(".", 1)[-1]
    if not ("param" in name or _re.fullmatch(r"col\d+", leaf)):
        return False
    from .model import is_num
    raws = [str(r.get("_raw", "")) for r in tidy.rows if is_num(r.get("value"))]
    return len(raws) >= 2 and all(_PARAM_COUNT.match(s) for s in raws)


def _loc(kind, page, idx):
    """Human-readable location of a table: 'page 3 (#0)' for PDFs, 'table 2' for Word,
    'sheet 1' for Excel, '#0' when there is no page (LaTeX)."""
    unit = _UNIT.get(kind)
    if unit and page:
        return f"{unit} {page}"
    return f"page {page} (#{idx})" if page else f"table #{idx}"


def analyze_pdf(path: str, expect_flat=None) -> PdfAnalysis:
    """Parse a PDF and analyze its tables and reported statistics."""
    parse = parse_pdf(path)
    return _analyze_parse(path, parse, expect_flat, source_kind="pdf")


def analyze_source(path: str, expect_flat=None, detector_seconds=4.0, checkpoint_dir=None) -> PdfAnalysis:
    """Analyze a PDF, LaTeX, Word (.docx) or Excel (.xlsx) file, dispatching on extension. LaTeX source gives
    far cleaner table extraction than a compiled PDF, so prefer it when available."""
    low = path.lower()
    if low.endswith((".tex", ".txt")):
        from .latex_ingest import parse_latex
        parse = parse_latex(path)
        # LatexParse has no n_pages; give the shared report a sensible attribute
        if not hasattr(parse, "n_pages"):
            parse.n_pages = 0
        return _analyze_parse(path, parse, expect_flat, source_kind="latex", detector_seconds=detector_seconds, checkpoint_dir=checkpoint_dir)
    if low.endswith(".docx"):
        from .office_ingest import parse_docx
        parse = parse_docx(path)
        return _analyze_parse(path, parse, expect_flat, source_kind="docx", detector_seconds=detector_seconds, checkpoint_dir=checkpoint_dir)
    if low.endswith(".xlsx"):
        from .office_ingest import parse_xlsx
        parse = parse_xlsx(path)
        return _analyze_parse(path, parse, expect_flat, source_kind="xlsx", detector_seconds=detector_seconds, checkpoint_dir=checkpoint_dir)
    parse = parse_pdf(path)
    return _analyze_parse(path, parse, expect_flat, source_kind="pdf", detector_seconds=detector_seconds, checkpoint_dir=checkpoint_dir)


def _analyze_parse(path, parse, expect_flat, source_kind="pdf", detector_seconds=4.0, checkpoint_dir=None) -> PdfAnalysis:
    """Analyze an already parsed article (PDF or LaTeX). Runs the detectors on each usable table within a time limit, then the statistical checks."""
    res = PdfAnalysis(path=path, parse=parse)
    res.source_kind = source_kind

    # 1+2: pattern detectors on each usable table. A wide table may have been split into
    # several single-metric tidy tables (t.tidies); run the detectors on each so unlike
    # quantities are never compared.
    for t in parse.tables:
        page = getattr(t, "page", 0)
        where = _loc(source_kind, page, t.index_on_page)
        tidies = getattr(t, "tidies", None) or ([t.tidy] if t.tidy is not None else [])
        if t.status in ("ok", "partial") and tidies and _has_fused_cells(t):
            res.could_not_check.append(
                f"{where} ({t.n_rows}x{t.n_cols}): some cells hold two numbers, so rows are "
                f"misaligned; not analyzed")
            continue
        if t.status in ("ok", "partial") and tidies:
            for mi, tidy in enumerate(tidies):
                key = (page, t.index_on_page, mi)
                size_column = _is_param_count_column(tidy)
                try:
                    from .bounded import run_bounded
                    flags, runs = run_bounded(tidy, seconds=detector_seconds, checkpoint_dir=checkpoint_dir, expect_flat=expect_flat)
                    res.detector_runs[key] = runs
                    for run in runs:
                        if run["status"] != "completed":
                            res.could_not_check.append(f"{where} metric {tidy.metric_name}: {run['detector']} {run['status']} ({run.get('reason','')}); limit {detector_seconds}s")
                        for note in run.get("notes", []):
                            res.could_not_check.append(f"{where} metric {tidy.metric_name}: {note}")
                except Exception as e:
                    flags = []
                    res.could_not_check.append(
                        f"{where} metric '{tidy.metric_name}': detectors failed "
                        f"({type(e).__name__}); not analyzed")
                if size_column:
                    # model sizes span decades by design, so the largest one always "stands
                    # out"; drop outlier flags but keep duplicate/flatness (copy-paste) checks
                    flags = [f for f in flags if f.type != "point_outlier"]
                res.table_flags[key] = flags
                if not is_pathological(tidy):
                    res.stat_findings += digit_tests(tidy)
        elif t.status == "non_table":
            # metadata/cover-sheet/prose block mis-detected as a table: not a results table,
            # so drop it silently (counted below) rather than cluttering "could not check".
            res.non_table_count += 1
        else:
            res.could_not_check.append(f"{where} ({t.n_rows}x{t.n_cols}): {t.reason}")

    # 2b: cross-table consistency - the same metric at the same coordinates reported in two
    # tables of this source should agree within rounding of the printed values.
    all_tidies = []
    for t in parse.tables:
        if t.status in ("ok", "partial"):
            tidies = getattr(t, "tidies", None) or ([t.tidy] if t.tidy is not None else [])
            all_tidies.extend(tidies)
    try:
        xt_flags, xt_notes = find_cross_table_inconsistencies(all_tidies)
        res.cross_table_flags = score(xt_flags)
        for n in xt_notes:
            res.could_not_check.append(f"cross-table: {n}")
    except Exception as e:
        res.could_not_check.append(f"cross-table consistency check failed ({type(e).__name__}); not run")

    # 3: statcheck + GRIM on extracted supported strings only, never entire manuscript validation
    res.stat_findings += statcheck(parse.stat_strings)
    res.stat_findings += grim(parse.stat_strings)
    res.stat_findings += grimmer(parse.stat_strings)

    for sf in res.stat_findings:
        if sf.status == "not_applicable":
            res.could_not_check.append(f"{sf.kind}: {sf.detail}")

    res.could_not_check.extend(getattr(parse, "notes", []))
    return res


# ----------------------------------------------------------------- report
def format_report(res: PdfAnalysis) -> str:
    """Format a PdfAnalysis as a plain-text report."""
    p = res.parse
    L = []
    kind = getattr(res, "source_kind", "pdf")
    label = _LABEL.get(kind, "PDF")
    L.append(f"AnomalyNerd {label} report for '{p.path.split('/')[-1]}'")
    L.append("=" * 64)
    usable = [t for t in p.tables if t.status in ("ok", "partial")]
    if kind in ("latex", "docx", "xlsx"):
        L.append(f"Tables found: {len(p.tables)}   Tables analyzed: {len(usable)}   "
                 f"Reported statistics found: {len(p.stat_strings)}")
    else:
        L.append(f"Pages: {p.n_pages}   Tables detected: {len(p.tables)}   "
                 f"Tables extraction-usable: {len(usable)}   "
                 f"Reported statistics found: {len(p.stat_strings)}")
    counts = {}
    for runs in res.detector_runs.values():
        for run in runs: counts[run['status']] = counts.get(run['status'],0)+1
    L.append("Detector coverage: " + ", ".join(f"{k}={v}" for k,v in sorted(counts.items())))
    L.append("Extraction coverage: " + ", ".join(f"{status}={sum(t.status == status for t in p.tables)}" for status in ('ok','partial','skipped','non_table')))
    L.append("Raw flags are review suggestions, not research errors. Repeated baselines may be intentional.")
    if getattr(p, 'included_files', None): L.append("Included source files: " + ", ".join(p.included_files))
    L.append("")

    # ---- findings: pattern anomalies ----
    any_flag = False
    L.append("WHAT I CHECKED AND FOUND")
    L.append("-" * 64)
    for t in usable:
        pg = getattr(t, "page", 0)
        tidies = getattr(t, "tidies", None) or ([t.tidy] if t.tidy is not None else [])
        for mi, tidy in enumerate(tidies):
            flags = res.table_flags.get((pg, t.index_on_page, mi), [])
            if pg and kind in _UNIT:
                hdr = f"Table in {_loc(kind, pg, t.index_on_page)}, metric '{tidy.metric_name}'"
            elif pg:
                hdr = f"Table on page {pg} (#{t.index_on_page}), metric '{tidy.metric_name}'"
            else:
                tname = tidy.name if getattr(tidy, "name", "") else f"#{t.index_on_page}"
                hdr = f"Table '{tname}', metric '{tidy.metric_name}'"
            if mi == 0 and t.caveats:
                hdr += "  [caveats: " + "; ".join(t.caveats) + "]"
            L.append(hdr)
            if not flags:
                L.append("    no raw flags from completed detectors; see coverage for incomplete checks")
            for f in flags:
                mark = {"HIGH": "!!", "MEDIUM": "! ", "LOW": "  "}[f.priority]
                L.append(f"  {mark}[{f.priority}] {f.type} along '{f.axis}': {f.suggestion}")
                any_flag = any_flag or f.priority in ("HIGH", "MEDIUM")

    # ---- cross-table consistency findings ----
    if res.cross_table_flags:
        L.append("")
        L.append("Cross-table consistency (same metric, same coordinates, two tables):")
        for f in res.cross_table_flags:
            mark = {"HIGH": "!!", "MEDIUM": "! ", "LOW": "  "}[f.priority]
            L.append(f"  {mark}[{f.priority}] {f.suggestion}")
            any_flag = any_flag or f.priority in ("HIGH", "MEDIUM")

    # ---- statistical + digit findings ----
    stat_hits = [s for s in res.stat_findings if s.status == "finding"]
    stat_ok = [s for s in res.stat_findings if s.status == "ok"]
    if stat_hits or stat_ok:
        L.append("")
        L.append("Statistical / digit checks:")
        for s in stat_hits:
            L.append(f"  !![{s.severity.upper()}] {s.kind}: {s.detail}")
        for s in stat_ok:
            L.append(f"    [ok] {s.kind}: {s.detail}")

    # ---- the honesty section ----
    L.append("")
    L.append("WHAT I COULD NOT CHECK (and why)")
    L.append("-" * 64)
    if not res.could_not_check:
        L.append("  (no recorded extraction/detector gaps; applicability limits still apply)")
    else:
        for c in res.could_not_check:
            L.append(f"  - {c}")

    # ---- bottom line ----
    L.append("")
    if any_flag or stat_hits:
        L.append("Bottom line: some entries are worth a closer look (see above). "
                 "AnomalyNerd points; it does not diagnose — a human should verify each.")
    else:
        L.append("Bottom line: no high- or medium-priority anomalies in the tables I could "
                 "read. Note the 'could not check' list above for coverage limits.")
    return "\n".join(L)
