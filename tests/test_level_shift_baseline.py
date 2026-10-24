"""Regression tests: detect_level_shifts baseline must be robust to a true shift
that begins inside the first-30% baseline window.

A mean/SD baseline is contaminated by shifted points inside the window, which
inflates the spread and can suppress the flag entirely (verified defect: a 2x
sustained shift at 25% of the series was not flagged). The detector uses a
median/MAD baseline per the anomalynerd design rule.
"""

import random

from anomalynerd.detectors import detect_level_shifts
from anomalynerd.model import Axis, TidyTable


def _series(xs, ys):
    return TidyTable(
        "t", {"X": Axis("X", "numeric")}, None, "Y", False,
        [{"X": x, "value": y} for x, y in zip(xs, ys)],
    )


def test_shift_inside_baseline_window_is_flagged():
    xs = list(range(20))
    flags = detect_level_shifts(_series(xs, [100] * 5 + [200] * 15))
    assert len(flags) == 1
    # Baseline center must be the true pre-shift level, not a contaminated mean.
    assert abs(flags[0].detail["baseline_mean"] - 100.0) < 1e-9


def test_shift_past_baseline_window_still_flagged():
    xs = list(range(20))
    flags = detect_level_shifts(_series(xs, [100] * 10 + [200] * 10))
    assert len(flags) == 1
    assert flags[0].detail["shift_span"][0] == 10


def test_noisy_flat_series_stays_quiet():
    random.seed(7)
    xs = list(range(20))
    flags = detect_level_shifts(_series(xs, [100 + random.gauss(0, 5) for _ in xs]))
    assert flags == []


def test_linear_ramp_stays_quiet():
    xs = list(range(20))
    flags = detect_level_shifts(_series(xs, [100 + i * 5 for i in xs]))
    assert flags == []


def test_brief_dip_below_persist_stays_quiet():
    xs = list(range(20))
    flags = detect_level_shifts(_series(xs, [100] * 8 + [50] * 2 + [100] * 10))
    assert flags == []
