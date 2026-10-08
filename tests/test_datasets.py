"""Regression suite: locks in every validated behavior across the real datasets.

Run:  pytest -q   (from the anomalynerd/ directory)

Each test encodes ground truth we confirmed during development, so future changes
cannot silently regress the detectors.
"""
import os, sys, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anomalynerd.model import TidyTable, Axis
from anomalynerd.ingest import read_csv
from anomalynerd.analyze import analyze

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def _types(flags):
    return {f.type for f in flags}


def _by(flags, typ):
    return [f for f in flags if f.type == typ]


def _has(flags, typ, priority=None):
    return any(f.type == typ and (priority is None or f.priority == priority) for f in flags)


# --------------------------------------------------------------- ozone (T6)
def test_ozone_flags_high_level_shift():
    t = read_csv(os.path.join(EX, "halley_ozone_october.csv"),
                 metric_col="ozone_DU", lower_is_better=True)
    flags = analyze(t)
    ls = _by(flags, "level_shift")
    assert ls, "ozone must produce a level_shift"
    assert any(f.priority == "HIGH" for f in ls), "ozone level_shift must be HIGH (flat baseline break)"
    # baseline near 300, shift down to ~150-170
    f = [x for x in ls if x.priority == "HIGH"][0]
    assert f.detail["baseline_mean"] > 270
    assert f.detail["shifted_mean"] < 210


# --------------------------------------------------------------- CO2 (trend, must NOT alarm)
def test_co2_trend_not_high():
    t = read_csv(os.path.join(EX, "co2_annual.csv"),
                 metric_col="mean", lower_is_better=False, ignore_cols=["unc"])
    flags = analyze(t)
    assert not any(f.priority == "HIGH" for f in flags), \
        "a steady CO2 rise must not produce any HIGH alarm"
    # if a level_shift exists it must be the low-confidence trend candidate
    for f in _by(flags, "level_shift"):
        assert f.detail.get("baseline_trend") is True
        assert f.priority in ("LOW",)
    # no point_outlier on the trend endpoint
    assert not _by(flags, "point_outlier"), "CO2 trend endpoint must not be a point_outlier"


# --------------------------------------------------------------- Anscombe (ground truth)
def _anscombe(ds):
    rows = list(csv.DictReader(open(os.path.join(EX, "anscombe.csv"))))
    pts = [(float(r["x"]), float(r["y"])) for r in rows if r["dataset"] == ds]
    return TidyTable(f"ans_{ds}", {"x": Axis("x", "numeric")}, entity_axis=None,
                     metric_name="y", lower_is_better=True,
                     rows=[{"x": x, "value": y} for x, y in pts])


def test_anscombe_I_clean():
    flags = analyze(_anscombe("I"))
    assert not any(f.priority in ("HIGH", "MEDIUM") for f in flags), \
        "Anscombe I is clean; no HIGH/MEDIUM flags allowed"


def test_anscombe_III_residual_outlier():
    flags = analyze(_anscombe("III"))
    po = _by(flags, "point_outlier")
    assert po, "Anscombe III has one residual outlier"
    assert po[0].detail.get("mode", "resid") == "resid"
    assert po[0].detail["at"] == 13.0


def test_anscombe_IV_leverage_outlier():
    flags = analyze(_anscombe("IV"))
    po = _by(flags, "point_outlier")
    assert po, "Anscombe IV has one leverage outlier"
    assert po[0].detail.get("mode") == "leverage"
    assert po[0].detail["at"] == 19.0
    assert po[0].priority == "HIGH"


# --------------------------------------------------------------- MethodA day-1 sweep




def test_newcomb_two_outliers_no_noise():
    t = read_csv(os.path.join(EX, "newcomb_speed_of_light.csv"), metric_col="deviation")
    flags = analyze(t)
    po = _by(flags, "point_outlier")
    ats = sorted(f.detail["at"] for f in po)
    # both known outliers caught (values -44 at measurement 6, -2 at measurement 10)
    vals = sorted(f.detail["value"] for f in po)
    assert -44 in vals and -2 in vals, "both known Newcomb outliers must be flagged"
    # measurement is an index axis -> no jump/level_shift noise
    assert not _by(flags, "numeric_jump"), "no jump noise on the index axis"
    assert not _by(flags, "level_shift"), "no level_shift on the index axis"


# --------------------------------------------------------------- Draft lottery (T8 trend)
def test_draft_lottery_trend():
    # Auto-detection: no metric_col / ignore hints. Schema inference must recognize month_num
    # as the index axis, mean_draft_rank as the metric, and drop the redundant 'month' label
    # (a 1:1 alias of month_num) rather than treating it as an entity axis.
    t = read_csv(os.path.join(EX, "draft_lottery_1970.csv"))
    assert t.metric_name == "mean_draft_rank", "metric should auto-detect as mean_draft_rank"
    assert "month" not in t.axes, "the redundant 'month' label alias should be dropped"
    assert "month_num" in t.axes, "month_num should be kept as the numeric index axis"
    # without a flatness hint: a low-confidence candidate only, and NO spurious win_reversal
    flags_default = analyze(t)
    assert not _by(flags_default, "win_reversal"), \
        "a redundant month label must not create a spurious best-method reversal"
    tr = _by(flags_default, "trend")
    assert tr, "a downward trend should be detected"
    assert tr[0].priority == "LOW", "trend is a LOW candidate unless axis is expected flat"
    # with expect_flat: it becomes a real finding (the lottery should be random)
    flags_flat = analyze(t, expect_flat=["month_num"])
    tr2 = _by(flags_flat, "trend")
    assert tr2 and tr2[0].priority in ("HIGH", "MEDIUM"), \
        "with month_num expected flat, the trend is a real anomaly"
    assert tr2[0].detail["slope"] < 0, "draft rank declines across the year"


# --------------------------------------------------------------- thyroid (big data as summary)
def test_thyroid_summary_flags_hot_bins():
    """Big anomaly dataset summarized as anomaly-rate-per-bin: the tool should flag the
    extreme bins where anomalies concentrate, and stay quiet on the flat features."""
    import csv as _csv
    rows = list(_csv.DictReader(open(os.path.join(EX, "thyroid_anomaly_rate_by_bin.csv"))))
    feats = [c for c in rows[0] if c.endswith("_anom_rate")]
    truth = {"feat2": 10, "feat3": 1, "feat4": 1, "feat6": 1}  # feat1/feat5 have no hot bin
    for c in feats:
        pts = [(int(r["bin"]), float(r[c])) for r in rows]
        t = TidyTable(c, {"bin": Axis("bin", "numeric")}, entity_axis=None,
                      metric_name="anomaly_rate", lower_is_better=False,
                      rows=[{"bin": b, "value": v} for b, v in pts])
        po = _by(analyze(t), "point_outlier")
        flagged = [f.detail["at"] for f in po]
        fname = c.replace("_anom_rate", "")
        exp = truth.get(fname)
        if exp:
            assert exp in flagged, f"{fname}: expected hot bin {exp} flagged, got {flagged}"
        else:
            assert not po, f"{fname}: flat feature should not flag, got {flagged}"


# --------------------------------------------------------------- income by education (ordered-category monotonicity)
def test_income_by_education_reversal():
    ORD = ["<9th", "HS", "SomeCollege", "Associate", "Bachelor", "Master", "Professional", "Doctorate"]
    t = read_csv(os.path.join(EX, "income_by_education.csv"),
                 metric_col="median_income", lower_is_better=False, orders={"education": ORD})
    assert t.axes["education"].kind == "ordered_cat", "explicit order must make education ordered, not entity"
    flags = analyze(t, expected_monotonic={"education": "increasing"})
    mono = _by(flags, "monotonicity")
    assert mono, "income should be expected to rise with education but reverses at the top"
    revs = mono[0].detail["reversals"]
    assert any(r["from"] == "Professional" and r["to"] == "Doctorate" for r in revs), \
        "the Professional->Doctorate income reversal must be named"


# --------------------------------------------------------------- product tiers (T4 categorical-order exception)
def test_product_tier_cat_exception():
    t = read_csv(os.path.join(EX, "product_tier_ratings.csv"),
                 metric_col="rating", lower_is_better=False, orders={"tier": ["Premium", "Standard"]})
    flags = analyze(t)
    ce = _by(flags, "cat_exception")
    assert ce, "Premium-usually-beats-Standard with one exception should be flagged"
    d = ce[0].detail
    assert d["fails"] == 1 and d["holds"] == 7, f"expected 7 holds / 1 exception, got {d}"


def test_whole_table_gross_outlier_small_slices():
    """A gross outlier in a table whose per-slice series are too short for the normal
    point-outlier pass (e.g. 3 settings x 2 methods) is still caught by the whole-table pass,
    while a normal table stays quiet."""
    import csv, tempfile, os
    from anomalynerd.ingest import read_csv
    from anomalynerd.analyze import analyze
    # 3 x 2 table, one gross outlier (443 among ~60-90)
    p = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.writer(p); w.writerow(["setting", "method_A", "method_B"])
    for row in [["s1", 90.9, 90.3], ["s2", 443.0, 83.0], ["s3", 55.7, 73.9]]:
        w.writerow(row)
    p.close()
    flags = analyze(read_csv(p.name))
    os.unlink(p.name)
    po = [f for f in flags if f.type == "point_outlier" and f.priority in ("HIGH", "MEDIUM")]
    assert po, "a gross outlier in a small-slice table should be caught by the whole-table pass"

    # a normal small table must stay quiet (no false positive)
    p2 = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.writer(p2); w.writerow(["setting", "method_A", "method_B"])
    for row in [["s1", 90.9, 90.3], ["s2", 88.0, 83.0], ["s3", 85.7, 86.9]]:
        w.writerow(row)
    p2.close()
    flags2 = analyze(read_csv(p2.name))
    os.unlink(p2.name)
    assert not [f for f in flags2 if f.type == "point_outlier" and f.priority == "HIGH"], \
        "a normal small table must not raise a whole-table false positive"
