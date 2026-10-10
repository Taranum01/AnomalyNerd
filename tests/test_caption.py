"""Tests for the grounded caption generator + the transparent caption comparison."""
import os
from anomalynerd.ingest import read_csv
from anomalynerd.analyze import analyze
from anomalynerd.caption import generate_caption
from anomalynerd.caption_compare import compare_captions

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def test_caption_states_known_trend():
    """The lottery caption should state the decreasing trend with its real numbers."""
    t = read_csv(os.path.join(EX, "draft_lottery_1970.csv"))
    cap = generate_caption(t, analyze(t, expect_flat=["month_num"]), level="medium").lower()
    assert "decrease" in cap, "caption should report the downward trend"
    assert "201" in cap and "121" in cap, "caption should include the real endpoint values"


def test_caption_length_knob():
    """short < medium <= detailed in content; short omits the pattern clause."""
    t = read_csv(os.path.join(EX, "newcomb_speed_of_light.csv"))
    fl = analyze(t)
    short = generate_caption(t, fl, level="short")
    medium = generate_caption(t, fl, level="medium")
    detailed = generate_caption(t, fl, level="detailed")
    assert len(short) < len(medium) <= len(detailed)
    assert "outlier" not in short.lower() and "stands out" not in short.lower()
    assert "stands out" in medium.lower()


def test_caption_grounded_no_false_pattern():
    """CO2 is a clean rising series; the caption must NOT invent a flatness/anomaly claim
    (regression for the unc-column flatness leak)."""
    t = read_csv(os.path.join(EX, "co2_annual.csv"))
    cap = generate_caption(t, analyze(t), level="detailed").lower()
    assert "no notable pattern" in cap or ("identical" not in cap and "outlier" not in cap)


def test_comparison_checkable_only():
    """Comparison shares the real caption's checkable direction claim and ignores metadata."""
    t = read_csv(os.path.join(EX, "draft_lottery_1970.csv"))
    gen = generate_caption(t, analyze(t, expect_flat=["month_num"]), level="medium")
    real = "Table 2: mean draft number by month in 1970; later months are lower. Bold = significant."
    rep = compare_captions(gen, real)
    assert "('direction', 'decrease')" in rep["shared"], "should share the downward-trend claim"
    # 'Table 2' and the year 1970 must be filtered out as metadata, not counted as claims
    assert "('value', 1970.0)" not in rep["real_claims"]
