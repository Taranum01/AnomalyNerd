"""A trend flag says "monotonic" only when the series is ordered; the flag itself is unchanged."""
import pytest
from anomalynerd.detectors import _order_word, _monotonic_direction, detect_trends, _relationship_suggestion
from anomalynerd.model import TidyTable, Axis


def _trend_flags(series):
    rows = [{"m": "A", "step": i, "value": v} for i, v in enumerate(series)]
    t = TidyTable("t", {"m": Axis("m", "unordered_cat"), "step": Axis("step", "numeric")},
                  entity_axis="m", metric_name="gap", lower_is_better=False, rows=rows)
    return [f for f in detect_trends(t) if f.type == "trend"]


def _text(series):
    f = _trend_flags(series)
    return f, (f[0].suggestion if f else "")


@pytest.mark.parametrize("mono,word", [("increasing", "monotonic"), ("decreasing", "monotonic"),
                                       ("non_decreasing", "monotonic with ties"),
                                       ("non_increasing", "monotonic with ties"),
                                       ("none", "not monotonic"), ("flat", "not monotonic")])
def test_order_word(mono, word):
    assert _order_word(mono) == word


def test_strictly_rising_nonlinear_series_is_called_monotonic():
    f, s = _text([1, 2, 4, 8, 16, 32, 64, 128])
    assert f and "(monotonic, Spearman" in s


def test_series_with_ties_says_so():
    f, s = _text([1, 2, 2, 8, 16, 32, 64, 128])
    assert f and "(monotonic with ties, Spearman" in s


def test_series_that_dips_is_not_called_monotonic():
    series = [1, 3, 2, 9, 30, 28, 90, 200]
    assert _monotonic_direction(series) == "none"
    f, s = _text(series)
    assert len(f) == 1 and "not monotonic" in s and "(monotonic," not in s


def test_a_dipping_nonlinear_series_still_gets_its_flag_with_honest_wording():
    ys = [11.6, 37.8, 49.2, 54.2, 63.4, 62.4]
    s = _relationship_suggestion("gap", "step", "rises", ys, 0.5, 0.94, False, False)
    assert "not monotonic" in s and "(monotonic," not in s and "Spearman rho=0.94" in s


def test_relationship_text_for_ordered_series_is_unchanged():
    ys = [1, 2, 3, 4, 5, 6, 7, 10000]
    s = _relationship_suggestion("gap", "step", "rises", ys, 0.3, 1.0, False, False)
    assert s.startswith("gap increases as step increases (monotonic, Spearman rho=1.00")


def test_linear_wording_is_untouched():
    s = _relationship_suggestion("gap", "step", "rises", [1, 2, 3, 4], 0.99, 1.0, True, False)
    assert "roughly linear" in s and "monotonic" not in s


def test_flag_count_and_type_do_not_depend_on_the_wording():
    a = _trend_flags([1, 2, 4, 8, 16, 32, 64, 128])
    b = _trend_flags([1, 2, 2, 8, 16, 32, 64, 128])
    assert len(a) == len(b) == 1 and a[0].type == b[0].type == "trend"
