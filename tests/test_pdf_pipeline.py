"""Tests for the PDF / LaTeX-source analysis pipeline."""
import os
from anomalynerd.latex_ingest import parse_latex
from anomalynerd.analyze import analyze
from anomalynerd.analyze_pdf import analyze_source
from anomalynerd.stat_tests import statcheck, grim


_SAMPLE_TEX = r"""
\documentclass{article}\begin{document}
We report t(38) = 2.10, p = .03 in the main analysis.
\begin{table}\caption{RMSE by horizon.}
\begin{tabular}{lcc}
\toprule
horizon & method\_A & method\_B \\
\midrule
1 & 0.10 & 0.12 \\
2 & 0.14 & 0.15 \\
3 & \textbf{0.17} & 0.19 \\
4 & 0.21 & 0.22 \\
5 & 9.80 & 0.26 \\
6 & 0.28 & 0.30 \\
7 & 0.31 & 0.33 \\
\bottomrule
\end{tabular}\end{table}
\appendix
\begin{table}\caption{Ablation accuracy.}
\begin{tabular}{lc}
setting & accuracy \\ \hline
full & 0.91 \\
no-x & 0.88 \\
no-y & 0.87 \\
no-z & 0.30 \\
no-w & 0.90 \\
\end{tabular}\end{table}
\end{document}
"""


def _write(tmp_path, text):
    p = tmp_path / "sample.tex"
    p.write_text(text)
    return str(p)


def _flags_by_type(flags, typ):
    return [f for f in flags if f.type == typ]


def test_latex_parses_both_tables(tmp_path):
    res = parse_latex(_write(tmp_path, _SAMPLE_TEX))
    usable = [t for t in res.tables if t.status == "ok"]
    assert len(usable) == 2, "both the main and the appendix tabular should parse"
    # captions become table names
    names = {t.tidy.name for t in usable}
    assert any("RMSE" in n for n in names)
    assert any("Ablation" in n for n in names)


def test_latex_catches_planted_jump(tmp_path):
    res = parse_latex(_write(tmp_path, _SAMPLE_TEX))
    main = [t for t in res.tables if t.tidy and "RMSE" in t.tidy.name][0]
    flags = analyze(main.tidy)
    outliers = _flags_by_type(flags, "point_outlier")
    assert any(f.priority == "HIGH" for f in outliers), \
        "the planted 9.80 jump at horizon=5 should be a HIGH point outlier"


def test_latex_catches_ablation_outlier(tmp_path):
    res = parse_latex(_write(tmp_path, _SAMPLE_TEX))
    abl = [t for t in res.tables if t.tidy and "Ablation" in t.tidy.name][0]
    flags = analyze(abl.tidy)
    outliers = _flags_by_type(flags, "point_outlier")
    assert any(f.priority == "HIGH" for f in outliers), \
        "the planted 0.30 ablation value should be a HIGH categorical outlier"


def test_latex_cells_are_clean(tmp_path):
    res = parse_latex(_write(tmp_path, _SAMPLE_TEX))
    main = [t for t in res.tables if t.tidy and "RMSE" in t.tidy.name][0]
    # \textbf and escaped underscore must be stripped/unescaped
    assert "method_A" in main.tidy.levels("method"), "escaped underscore should be unescaped"
    assert not any("\\" in str(lv) for lv in main.tidy.levels("method"))


def test_statcheck_consistent_and_inconsistent():
    ok = statcheck(["t(38) = 2.10, p = .03"])
    assert any(f.status == "ok" for f in ok), "t(38)=2.10,p=.03 is consistent (~.042)"
    bad = statcheck(["t(20) = 0.5, p < .001"])
    assert any(f.status == "finding" and f.severity == "high" for f in bad), \
        "a tiny t with p<.001 must be flagged"


def test_statcheck_chi_square_and_z():
    ok_chi = statcheck(["χ²(2, N = 120) = 5.99, p = .05"])
    assert ok_chi[0].status == "ok"

    bad_chi = statcheck(["chi-square(2) = 0.10, p < .001"])
    assert any(f.status == "finding" for f in bad_chi)

    ok_z = statcheck(["z = 1.96, p = .05"])
    assert ok_z[0].status == "ok"

    bad_z = statcheck(["z = 0.50, p < .001"])
    assert any(f.status == "finding" for f in bad_z)


def test_grim_flags_impossible_mean():
    bad = grim(["M = 3.45, SD = 1.0, N = 7"])
    assert any(f.status == "finding" for f in bad), "3.45 is not achievable for N=7"
    na = grim([])
    assert any(f.status == "not_applicable" for f in na), \
        "GRIM must say not-applicable when no M=..,N=.. is present"


def test_honesty_report_lists_what_could_not_be_checked(tmp_path):
    res = analyze_source(_write(tmp_path, _SAMPLE_TEX))
    # digit tests should be reported as not-applicable (too few values), not silently dropped
    assert any("benford" in c for c in res.could_not_check), \
        "the report must explicitly state the digit test could not be run"


def test_multimetric_table_is_split(tmp_path):
    """A wide table whose columns are DIFFERENT metrics (different scales) must be split
    into one single-metric table per column, so the detectors never compare unlike
    quantities (the cross-metric false-positive fix)."""
    from anomalynerd.ingest import read_tables
    import csv
    p = tmp_path / "multi.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "accuracy", "time_s", "ratio"])
        for row in [["A", 80.1, 2.9, 0.96], ["B", 85.9, 1.2, 0.51],
                    ["C", 79.7, 2.8, 0.84], ["D", 85.2, 1.5, 0.38],
                    ["E", 76.4, 2.8, 0.44], ["F", 82.0, 2.1, 0.60]]:
            w.writerow(row)
    tables = read_tables(str(p))
    metrics = sorted(t.metric_name for t in tables)
    assert metrics == ["accuracy", "ratio", "time_s"], \
        "a 3-different-metric table must split into 3 single-metric tables"


def test_homogeneous_table_not_split(tmp_path):
    """A wide table whose columns are the SAME metric for different entities (similar scale)
    must stay ONE table with an entity axis, not be split."""
    from anomalynerd.ingest import read_tables
    import csv
    p = tmp_path / "homo.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["horizon", "method_A", "method_B"])
        for row in [[1, 0.10, 0.12], [2, 0.14, 0.15], [3, 0.17, 0.19],
                    [4, 0.21, 0.22], [5, 0.28, 0.30], [6, 0.31, 0.33]]:
            w.writerow(row)
    tables = read_tables(str(p))
    assert len(tables) == 1, "same-metric columns should stay one table (entity axis)"


def test_multimetric_split_keeps_ordered_axis(tmp_path):
    """When a wide multi-metric table has an ORDERED numeric axis (a sweep variable like
    0.0,0.1,0.2 or a year), the split must keep that column as the shared axis on every
    metric table, not turn it into its own metric with no axis."""
    from anomalynerd.ingest import read_tables
    import csv
    p = tmp_path / "sweep.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["threshold", "small_metric", "big_count"])
        for i, row in enumerate([[0.0, 0.001, 450], [0.1, 0.004, 449], [0.2, 0.009, 447],
                                 [0.3, 0.02, 443], [0.4, 0.05, 440], [0.5, 0.09, 436]]):
            w.writerow(row)
    tables = read_tables(str(p))
    assert tables, "should produce at least one metric table"
    for t in tables:
        assert "threshold" in t.axes, "the ordered sweep axis must be kept on every metric"
        assert t.metric_name != "threshold", "the axis must not become a metric"


def test_grimmer_flags_impossible_sd():
    """GRIMMER flags an SD that no integer-item dataset can produce for the given mean and N,
    and passes an achievable one."""
    from anomalynerd.stat_tests import grimmer
    ok = grimmer(["M = 3.00, SD = 1.58, N = 5"])      # integers 1..5 give exactly this
    assert any(f.status == "ok" for f in ok), "1.58 is achievable for mean 3, N=5"
    bad = grimmer(["M = 3.00, SD = 1.60, N = 5"])     # no integer set gives this
    assert any(f.status == "finding" for f in bad), "1.60 is not achievable for mean 3, N=5"
    na = grimmer([])
    assert any(f.status == "not_applicable" for f in na), \
        "GRIMMER must say not-applicable when no M/SD/N triple is present"


def test_terminal_digit_runs_on_enough_values(tmp_path):
    """The terminal-digit test reports a result (ok / finding / not_applicable) rather than
    silently skipping, honoring the 'say what you can't check' requirement."""
    from anomalynerd.ingest import read_csv
    from anomalynerd.stat_tests import digit_tests
    import csv
    p = tmp_path / "vals.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["idx", "value"])
        for i in range(8):
            w.writerow([i, round(0.1 * i + 0.01 * i, 3)])
    t = read_csv(str(p), metric_col="value")
    kinds = {d.kind for d in digit_tests(t)}
    assert "terminal_digit" in kinds, "a terminal-digit result must always be reported"


def test_docling_fallback_is_optional_and_graceful():
    """The Docling fallback must be optional: _try_docling returns a list + an honest note,
    and never raises, whether or not docling is installed. When docling is absent it returns
    no tables and a note explaining how to enable it."""
    from anomalynerd.pdf_ingest import _try_docling
    tables, note = _try_docling("/nonexistent/path/to.pdf")
    assert isinstance(tables, list)
    # either docling is missing (note explains install) or the convert failed on the bad path;
    # in both cases we get a note and no crash
    assert note is None or isinstance(note, str)


def test_docling_tables_keep_their_page():
    """A layout-model table reports the PDF page Docling found it on, not page 0."""
    from types import SimpleNamespace as NS
    from anomalynerd.pdf_ingest import _docling_page
    assert _docling_page(NS(prov=[NS(page_no=7)])) == 7
    assert _docling_page(NS(prov=[])) == 0
    assert _docling_page(NS()) == 0


def test_docling_index_does_not_collide_on_same_page():
    """With real pages, a Docling table must not reuse a pdfplumber table's (page, index)."""
    from anomalynerd.pdf_ingest import ExtractedTable, _unique_docling_indices
    a = ExtractedTable(page=3, index_on_page=0, n_rows=3, n_cols=2, status="ok")
    b = ExtractedTable(page=3, index_on_page=0, n_rows=3, n_cols=2, status="ok", backend="docling")
    c = ExtractedTable(page=4, index_on_page=0, n_rows=3, n_cols=2, status="ok", backend="docling")
    _unique_docling_indices([a, b, c])
    assert (a.index_on_page, b.index_on_page, c.index_on_page) == (0, 1, 0)


def test_docling_runs_with_ocr_disabled():
    """The Docling path must set do_ocr=False. Leaving OCR on (Docling's default) invokes the
    RapidOCR/ONNX native stack, which aborts the whole process (SIGABRT) on some PDFs -- a
    native crash a Python try/except cannot catch. AnomalyNerd only needs Docling for table
    structure, not OCR, so OCR must stay off. Guard the source so the setting is not dropped."""
    import inspect
    from anomalynerd import pdf_ingest
    src = inspect.getsource(pdf_ingest._try_docling)
    assert "do_ocr = False" in src or "do_ocr=False" in src, \
        "Docling OCR must be disabled to avoid the native abort-trap crash"

def test_extracted_table_has_backend_field():
    """Every ExtractedTable records which backend produced it (pdfplumber or docling)."""
    from anomalynerd.pdf_ingest import ExtractedTable
    et = ExtractedTable(page=1, index_on_page=0, n_rows=3, n_cols=2, status="skipped")
    assert et.backend == "pdfplumber", "default backend should be pdfplumber"


def test_latex_mean_std_cells_keep_central_value():
    """A bare plus/minus command followed by a digit must not fuse mean and spread."""
    from anomalynerd.latex_ingest import _clean_cell
    for cell in (r"0.689\pm0.089", r"0.689{\pm}0.089", r"0.689\pm 0.089",
                 "0.689±0.089", "0.689∓0.089", "0.689+-0.089"):
        assert _clean_cell(cell) == "0.689", cell
    # A longer LaTeX control word beginning with pm is not the plus/minus command.
    assert _clean_cell(r"0.689\pmod{2}") == "0.6892"


def test_non_table_filter_drops_prose_keeps_data():
    """The non-table filter drops clear metadata/prose blocks, never plausible data tables."""
    from anomalynerd.pdf_ingest import _looks_like_non_table
    # a submission cover-sheet block
    meta = [["Manuscript Number:", "X-123"], ["Article Type:", "Research paper"],
            ["Keywords:", "clustering; graphs"], ["Corresponding Author:", "A. Author"]]
    assert _looks_like_non_table(meta) is True
    # a prose block split into one-cell lines: sentence shape, not merely "no numbers"
    prose = [["The method first represents spatial data as a graph and then compares local neighborhoods."],
             ["We evaluate the approach against published baselines using several public datasets."],
             ["The discussion explains limitations, threats to validity, and directions for future work."]]
    assert _looks_like_non_table(prose) is True
    # a genuine small numeric results table must NOT be dropped
    data = [["Algorithm", "ARI", "NMI"], ["kmeans", "0.81", "0.74"],
            ["dbscan", "0.63", "0.59"], ["optics", "0.70", "0.66"]]
    assert _looks_like_non_table(data) is False
    # a genuine all-text qualitative table must NOT be dropped just because it has no numbers
    qualitative = [["Method", "Assumption", "Strength", "Failure mode"],
                   ["Interview coding", "Participants describe stable themes", "Rich explanation", "Researcher interpretation"],
                   ["Content analysis", "Categories are defined consistently", "Transparent classification", "Ambiguous source wording"],
                   ["Narrative review", "Studies are comparable qualitatively", "Broad synthesis", "Selection emphasis"]]
    assert _looks_like_non_table(qualitative) is False


def test_non_table_blocks_are_not_listed_as_could_not_check():
    """Clear metadata/prose mis-detections are counted but do not clutter honesty coverage."""
    from anomalynerd.analyze_pdf import _analyze_parse
    from anomalynerd.pdf_ingest import ExtractedTable, PdfParse
    et = ExtractedTable(page=1, index_on_page=0, n_rows=4, n_cols=2,
                        status="non_table", reason="not a results table (metadata form); skipped")
    parse = PdfParse(path="x.pdf", n_pages=1, tables=[et], stat_strings=[])
    res = _analyze_parse("x.pdf", parse, None)
    assert res.non_table_count == 1
    assert not any("not a results table" in item for item in res.could_not_check)


def test_short_code_headers_are_not_shredded_captions():
    """Compact real headers such as A/B/C/D stay headers; only caption-marked shrapnel is cleaned."""
    from anomalynerd.pdf_ingest import _assess_and_tidy, _looks_like_shredded_caption
    header = ["A", "B", "C", "D"]
    assert _looks_like_shredded_caption(header) is False
    rows = [header,
            ["0.81", "0.74", "0.63", "0.59"],
            ["0.70", "0.66", "0.72", "0.68"],
            ["0.69", "0.71", "0.64", "0.62"]]
    et = _assess_and_tidy(rows, page=1, idx=1)
    assert et.status == "ok", et.reason
    assert et.tidy is not None


def test_caption_marked_shrapnel_is_not_used_as_headers():
    """A shredded caption is an extraction failure, not invented numerical coordinates."""
    from anomalynerd.pdf_ingest import _assess_and_tidy, _looks_like_shredded_caption
    caption = ["able 6 Co", "mp", "a", "rison results for", "methods"]
    assert _looks_like_shredded_caption(caption) is True
    rows = [caption,
            ["0.81", "0.74", "0.63", "0.59", "0.70"],
            ["0.70", "0.66", "0.72", "0.68", "0.69"],
            ["0.69", "0.71", "0.64", "0.62", "0.65"]]
    et = _assess_and_tidy(rows, page=1, idx=1)
    assert et.header == caption
    assert et.status == "skipped", et.reason
    assert et.tidy is None
    assert "extraction failure" in et.reason


def test_grim_boundary_half_unit_is_not_false_flagged():
    # Achievable means at exact half-unit rounding boundaries must pass:
    # 0.75 reported as 0.8 (N=4, 1dp), 0.125 as 0.13 (N=8, 2dp).
    for s in ["M = 0.8, SD = 1.0, N = 4", "M = 0.13, SD = 1.0, N = 8",
              "M = 1.3, SD = 1.0, N = 4", "M = 0.38, SD = 1.0, N = 8"]:
        out = grim([s])
        assert all(f.status != "finding" for f in out), \
            f"boundary-achievable mean falsely flagged: {s}"
        assert any(f.status == "ok" for f in out)


def test_grim_still_flags_truly_impossible():
    # one past the boundary must still fail: 0.9 with N=4 (nearest 1.00, diff .10)
    out = grim(["M = 0.9, SD = 1.0, N = 4"])
    assert any(f.status == "finding" for f in out)


def test_grimmer_runs_when_mean_exactly_at_boundary():
    # mean 0.8/N=4 passes GRIM at the boundary, so GRIMMER must evaluate the SD
    # rather than skip as "mean already fails GRIM"
    from anomalynerd.stat_tests import grimmer
    out = grimmer(["M = 0.8, SD = 1.0, N = 4"])
    assert any(f.status in ("ok", "finding") for f in out), \
        "GRIMMER skipped a mean that GRIM accepts at the rounding boundary"


def test_detectors_survive_extreme_finite_values():
    # a garbage cell like 1e308 (parseable from scientific notation) must not
    # crash the detectors that fit lines or take SDs; a crash kills the whole report
    from anomalynerd.model import Axis, TidyTable
    from anomalynerd import detectors as D
    vals = [1.0, 2.0, 3.0, 4.0, -1e308, 5.0, 6.0, 7.0, 8.0]
    t = TidyTable(name="T", axes={"x": Axis("x", "numeric")}, entity_axis=None,
                  metric_name="acc", lower_is_better=False,
                  rows=[{"x": i, "value": v} for i, v in enumerate(vals)])
    for d in (D.detect_level_shifts, D.detect_point_outliers, D.detect_trends,
              D.detect_numeric_jumps, D.detect_monotonicity, D.detect_win_reversals,
              D.detect_cat_exceptions, D.detect_duplicates_and_flatness):
        d(t)  # must not raise


def test_lin_fit_scaling_preserves_normal_results():
    from anomalynerd.detectors import _lin_fit, _pearson
    slope, intercept, resid_sd = _lin_fit([1, 2, 3, 4], [2.0, 4.0, 6.0, 8.0])
    assert abs(slope - 2.0) < 1e-9
    assert abs(intercept) < 1e-9
    assert resid_sd < 1e-9
    assert abs(_pearson([1, 2, 3, 4], [2.0, 4.0, 6.0, 8.0]) - 1.0) < 1e-9


def test_extreme_mixed_series_emits_explicit_diagnostics():
    # A series whose magnitudes defeat the fit must SAY so (notes channel),
    # not silently report nothing - and a true outlier must still be flagged.
    from anomalynerd.model import Axis, TidyTable
    from anomalynerd import detectors as D
    t = TidyTable(name="T", axes={"x": Axis("x", "numeric")}, entity_axis=None,
                  metric_name="acc", lower_is_better=False,
                  rows=[{"x": i, "value": v} for i, v in
                        enumerate([1.0, 2.0, 3.0, 4.0, -1e308, 5.0, 6.0, 7.0])])
    D.drain_notes()
    outlier_flags = D.detect_point_outliers(t)
    D.detect_level_shifts(t)
    D.detect_trends(t)
    notes = D.drain_notes()
    assert any(f.type == "point_outlier" for f in outlier_flags)
    assert any("not determinable" in n for n in notes), \
        "degenerate fits must be reported, not silently skipped"


def test_uniformly_huge_series_fits_without_notes():
    # Scaling handles uniformly large values; no diagnostic is due.
    from anomalynerd.model import Axis, TidyTable
    from anomalynerd import detectors as D
    vals = [1e308, 1.5e308, 2e308, 1.2e308, 1.8e308, 2.5e308, 3e308, 2.9e308]
    t = TidyTable(name="T", axes={"x": Axis("x", "numeric")}, entity_axis=None,
                  metric_name="acc", lower_is_better=False,
                  rows=[{"x": i, "value": v} for i, v in enumerate(vals)])
    D.drain_notes()
    D.detect_level_shifts(t); D.detect_trends(t); D.detect_point_outliers(t)
    assert D.drain_notes() == []


def test_bounded_runner_surfaces_notes():
    from anomalynerd.model import Axis, TidyTable
    from anomalynerd.bounded import run_bounded
    t = TidyTable(name="T", axes={"x": Axis("x", "numeric")}, entity_axis=None,
                  metric_name="acc", lower_is_better=False,
                  rows=[{"x": i, "value": v} for i, v in
                        enumerate([1.0, 2.0, 3.0, 4.0, -1e308, 5.0, 6.0, 7.0])])
    _, runs = run_bounded(t, seconds=10)
    notes = [n for r in runs for n in r.get("notes", [])]
    assert any("not determinable" in n for n in notes), \
        "notes must travel through the bounded runner into report diagnostics"


def test_merged_table_gets_marked_merged_status():
    """A results table merged with prose is marked status='merged' (so the layout-recovery step
    tries to isolate it), not silently reported as 'no numeric column'."""
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [["", "Setting", "Score"]]
    rows += [["", f"method-{i}", f"{40+i}.5"] for i in range(6)]
    rows += [["", "prose", frag] for frag in
             ["owninthefe", "ings. Trivia", "fiedthatasign", "erformedinS", "astoryorset"]]
    et = _assess_and_tidy(rows, page=1, idx=0)
    assert et.status == "merged"
    assert "merged with surrounding text" in (et.reason or "")


def test_genuinely_nonnumeric_table_still_says_no_numeric():
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [["Name", "Role"], ["Alice", "lead"], ["Bob", "member"], ["Cara", "member"]]
    et = _assess_and_tidy(rows, page=1, idx=0)
    assert et.status not in ("ok", "partial", "merged")
    assert "no numeric column" in (et.reason or "")


# --- layout-recovery replacement rules -------------------------------------------------
# Regression tests for the Docling fallback branch of parse_pdf: the layout model
# re-detects every table region on the page, so recovery must (a) not duplicate tables
# pdfplumber already parsed cleanly, (b) replace only the merged stand-ins it actually
# recovered, and (c) report the rest as honest "could not isolate" skips.

class _FakePage:
    def __init__(self, tables): self._tables = tables
    def extract_text(self): return "body text"
    def extract_tables(self, settings=None):
        return [] if settings else self._tables


class _FakePDF:
    def __init__(self, pages): self.pages = pages
    def __enter__(self): return self
    def __exit__(self, *args): return False


_GOOD_ROWS = [["Setting", "Score"]] + [[f"m{i}", f"{40 + i}.5"] for i in range(5)]
_MERGED_ROWS = [["", "Setting", "Score"]] \
    + [["", f"method-{i}", f"{40 + i}.5"] for i in range(6)] \
    + [["", "prose", frag] for frag in
       ["owninthefe", "ings. Trivia", "fiedthatasign", "erformedinS", "astoryorset"]]
_RECOVERED_ROWS = [["Setting", "Score"]] + [[f"method-{i}", f"{40 + i}.5"] for i in range(6)]


def _docling_et(rows):
    from anomalynerd.pdf_ingest import _assess_and_tidy
    et = _assess_and_tidy(rows, 0, 0)
    et.backend = "docling"
    return et


def _run_parse(monkeypatch, pages, docling_tables):
    from anomalynerd import pdf_ingest as pi
    monkeypatch.setattr(pi.pdfplumber, "open", lambda path: _FakePDF(pages))
    monkeypatch.setattr(pi, "_try_docling", lambda path: (docling_tables, None))
    return pi.parse_pdf("fake.pdf")


def _sig(et):
    return (tuple(et.header), tuple(tuple(r) for r in et.rows))


def test_docling_recovery_does_not_duplicate_good_tables(monkeypatch):
    """When merged tables trigger the layout model on a paper that also has cleanly parsed
    tables, the Docling copies of those good tables must be dropped, not analyzed twice."""
    res = _run_parse(monkeypatch,
                     [_FakePage([_GOOD_ROWS, _MERGED_ROWS])],
                     [_docling_et(_GOOD_ROWS), _docling_et(_RECOVERED_ROWS)])
    good = [t for t in res.tables if _sig(t) == _sig(_docling_et(_GOOD_ROWS))
            and t.status in ("ok", "partial")]
    assert len(good) == 1, f"good table must appear exactly once, got {len(good)}"
    assert any(t.backend == "docling" and _sig(t) == _sig(_docling_et(_RECOVERED_ROWS))
               for t in res.tables), "recovered Docling table must be kept"
    assert not any(t.status == "merged" for t in res.tables)


def test_unrecovered_merged_table_is_reported_not_dropped(monkeypatch):
    """On PARTIAL recovery, a merged table the layout model could not isolate must remain
    visible as an honest skip; only the recovered stand-in is replaced."""
    other_merged = [["", "Model", "Acc"]] \
        + [["", f"model-{i}", f"{50 + i}.1"] for i in range(6)] \
        + [["", "prose", frag] for frag in
           ["abcdefgh", "ijklmnop", "qrstuvwx", "yzabcdef", "ghijklmn", "opqrstuv"]]
    res = _run_parse(monkeypatch,
                     [_FakePage([_MERGED_ROWS, other_merged])],
                     [_docling_et(_RECOVERED_ROWS)])
    assert not any(t.status == "merged" for t in res.tables)
    skipped = [t for t in res.tables
               if t.status == "skipped" and "could not isolate" in (t.reason or "")]
    assert len(skipped) == 1, f"the unrecovered merged table must be reported, got {res.tables}"
    assert any(t.backend == "docling" for t in res.tables)


def test_duplicate_docling_results_collapse(monkeypatch):
    """Identical tables in the layout model's own output are kept only once."""
    res = _run_parse(monkeypatch,
                     [_FakePage([_MERGED_ROWS])],
                     [_docling_et(_RECOVERED_ROWS), _docling_et(_RECOVERED_ROWS)])
    recovered = [t for t in res.tables if t.backend == "docling"]
    assert len(recovered) == 1, f"duplicate Docling tables must collapse, got {len(recovered)}"


def test_magnitude_suffixes_parse_as_numbers():
    """'1k', '300K', '1.5M', '175B' are numbers; lowercase m/b (metres, minutes, bits), other
    letters and trailing words are not."""
    from anomalynerd.ingest import _parse_number, MISSING
    assert _parse_number("1k") == 1000
    assert _parse_number("10k") == 10000
    assert _parse_number("300K") == 300000
    assert _parse_number("1.5M") == 1.5e6
    assert _parse_number("175B") == 175e9
    for not_a_number in ("5m", "10b", "3G", "1kg", "k", "1k2"):
        assert _parse_number(not_a_number) is MISSING, not_a_number


def test_k_suffixed_axis_is_ordered_and_outlier_found(tmp_path):
    """A results table indexed 1k..10k is read as an ordered numeric axis, so a planted drop at
    8k is flagged (before the fix the axis was text labels and nothing was flagged)."""
    from anomalynerd.analyze_pdf import analyze_source
    p = tmp_path / "t.tex"
    rows = [("1k", 71.2), ("2k", 74.8), ("3k", 77.1), ("4k", 78.9), ("5k", 80.3),
            ("6k", 81.4), ("7k", 82.2), ("8k", 61.0), ("9k", 83.5), ("10k", 84.0)]
    body = " \\\\\n".join(f"{a} & {b}" for a, b in rows)
    p.write_text("\\begin{tabular}{lr}\nTraining size & Accuracy \\\\\n" + body + " \\\\\n\\end{tabular}\n")
    res = analyze_source(str(p))
    flags = [f for fl in res.table_flags.values() for f in fl]
    assert any(f.type == "point_outlier" and f.priority == "HIGH" and "61" in f.suggestion for f in flags)


def test_chart_tick_grid_is_not_analyzed():
    """A chart's axis ticks and legend read as a grid: sparse, many single-value rows."""
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [["", "DROP", "(F1)", "", "", "Hellaswag", "(acc)"]]
    for tick in ("35", "30", "25", "20", "15", "10"):
        rows.append(["", tick, "", "", "", "", ""])
        rows.append(["", "", "", "0." + tick[0], "", "0.7", ""])
    rows.append(["", "QuAC", "(F1)", "", "", "RTE", "(acc)"])
    et = _assess_and_tidy(rows, 51, 0)
    assert et.status not in ("ok", "partial")
    assert "chart read as a table" in et.reason


def test_dense_table_with_some_single_value_rows_is_kept():
    """Section-header rows give a real table single-value rows, but its grid stays dense."""
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [["Item", "Score", "N"]]
    for g in ("Group A", "Group B"):
        rows.append([g, "", ""])
        rows += [[f"{g} x{i}", f"{50+i}.5", f"{10+i}"] for i in range(4)]
    et = _assess_and_tidy(rows, 1, 0)
    assert "chart read as a table" not in (et.reason or "")


def test_structured_grids_are_exempt_from_chart_gate():
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [["x", "a", "b", "c"]] + [[str(i), str(i * 2), "", ""] for i in range(1, 9)]
    et = _assess_and_tidy(rows, 1, 0, structured=True)
    assert "chart read as a table" not in (et.reason or "")


def test_model_size_subheader_row_joins_the_header():
    """'GPT-3' over '175B' with scores below: the size row is part of the header, not data
    (read as data, 1.75e11 looked like a huge outlier)."""
    from anomalynerd.pdf_ingest import _merge_size_subheader
    header = ["Task", "GPT-3", "Gopher", "LLaMA"]
    body = [["", "175B", "280B", "65B"], ["Anatomy", "48.0", "56.3", "57.8"],
            ["Astronomy", "49.0", "65.8", "72.4"], ["Virology", "46.0", "52.4", "53.0"]]
    h, b = _merge_size_subheader(header, body)
    assert h == ["Task", "GPT-3 175B", "Gopher 280B", "LLaMA 65B"]
    assert b == body[1:]


def test_labeled_size_row_is_kept_as_data():
    """A row WITH a label is data, even if its values are sizes."""
    from anomalynerd.pdf_ingest import _merge_size_subheader
    header = ["Setting", "A", "B"]
    body = [["Params", "7B", "13B"], ["Score", "61.0", "64.2"], ["Other", "1.0", "2.0"]]
    assert _merge_size_subheader(header, body) == (header, body)
