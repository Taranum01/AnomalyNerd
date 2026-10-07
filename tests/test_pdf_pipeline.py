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
