"""A whole column on its own scale is listed as LOW with a note, not as a HIGH outlier."""
from anomalynerd.model import TidyTable, Axis
from anomalynerd.detectors import detect_point_outliers


def _wide(rows, cols, data):
    out = []
    for r, vals in zip(rows, data):
        for c, v in zip(cols, vals):
            out.append({"system": r, "method": c, "value": v})
    return TidyTable("t", {"system": Axis("system", "unordered_cat"), "method": Axis("method", "unordered_cat")},
                     "method", "value", False, out)


def _whole_table(flags):
    return [f for f in flags if f.detail.get("mode") == "whole_table"]


def test_glue_style_low_column_is_not_flagged():
    # values printed in RoBERTa (arXiv:1907.11692) Table 8: CoLA is 63-68, other tasks 78-96
    cols = ["MNLI", "QNLI", "QQP", "RTE", "SST", "MRPC", "CoLA", "STS"]
    data = [[87.6, 92.8, 91.9, 78.7, 94.8, 90.2, 63.6, 91.2], [89.0, 93.9, 91.9, 84.5, 95.3, 90.2, 66.3, 91.6],
            [89.3, 94.0, 92.0, 82.7, 95.6, 91.4, 66.1, 92.2], [90.0, 94.5, 92.2, 83.3, 96.1, 91.1, 67.4, 92.3],
            [90.2, 94.7, 92.2, 86.6, 96.4, 90.9, 68.0, 92.4]]
    t = _wide(["a", "b", "c", "d", "e"], cols, data)
    flags = _whole_table(detect_point_outliers(t))
    assert len(flags) == 1 and flags[0].detail["column_scale"] is True
    assert "different task or scale" in flags[0].suggestion
    from anomalynerd.analyze import score
    assert score(flags)[0].priority == "LOW"   # still listed, never HIGH or MEDIUM


def test_single_low_cell_in_a_normal_column_is_still_flagged():
    cols = ["c1", "c2", "c3", "c4"]
    data = [[90, 91, 92, 90], [91, 90, 91, 92], [92, 91, 90, 91], [90, 92, 91, 90], [91, 90, 92, 5]]
    t = _wide(["a", "b", "c", "d", "e"], cols, data)
    flags = detect_point_outliers(t)
    hit = [f for f in flags if f.detail.get("value") == 5]
    assert hit and not hit[0].detail.get("column_scale")


def test_cell_low_in_its_row_and_column_is_flagged_as_before():
    # printed values of Llama 2 (arXiv:2307.09288) Table 46, rows seen on page 71; the ChatGPT
    # row is low in every column, but the detector only fires on the lone extreme cell
    cols = ["c1", "c2", "c3", "c4"]
    data = [[0.42, 0.31, 0.28, 0.45], [0.40, 0.34, 0.28, 0.42], [0.42, 0.34, 0.28, 0.52], [0.18, 0.16, 0.15, 0.19],
            [0.38, 0.32, 0.29, 0.32], [0.40, 0.34, 0.30, 0.36], [0.55, 0.43, 0.40, 0.49], [0.51, 0.40, 0.38, 0.49],
            [0.46, 0.40, 0.35, 0.39], [0.51, 0.43, 0.40, 0.49]]
    t = _wide(list("abcdefghij"), cols, data)
    from anomalynerd import detectors
    guarded = detect_point_outliers(t)
    orig = detectors._column_wide_scale_difference
    detectors._column_wide_scale_difference = lambda *a, **k: False
    try:
        unguarded = detect_point_outliers(t)
    finally:
        detectors._column_wide_scale_difference = orig
    assert [f.detail.get("value") for f in guarded] == [f.detail.get("value") for f in unguarded]
    assert not any(f.detail.get("column_scale") for f in guarded)


def _glue_scaled(f):
    cols = ["MNLI", "QNLI", "QQP", "RTE", "SST", "MRPC", "CoLA", "STS"]
    data = [[87.6, 92.8, 91.9, 78.7, 94.8, 90.2, 63.6, 91.2], [89.0, 93.9, 91.9, 84.5, 95.3, 90.2, 66.3, 91.6],
            [89.3, 94.0, 92.0, 82.7, 95.6, 91.4, 66.1, 92.2], [90.0, 94.5, 92.2, 83.3, 96.1, 91.1, 67.4, 92.3],
            [90.2, 94.7, 92.2, 86.6, 96.4, 90.9, 68.0, 92.4]]
    for r in data:
        r[6] = round(r[6] * f, 1)
    return _wide(["a", "b", "c", "d", "e"], cols, data)


def test_whole_column_halved_is_still_high():
    # CoLA column scaled to ~half (a unit-style error) is caught at full priority by the
    # categorical detector, with the guard in place, not listed as "its own scale"
    from anomalynerd.analyze import score
    flags = detect_point_outliers(_glue_scaled(0.5))
    assert flags and all(f.detail.get("mode") != "whole_table" for f in flags)
    assert all(s.priority == "HIGH" for s in score(flags))


def test_whole_column_x0_1_is_still_high():
    from anomalynerd.analyze import score
    flags = detect_point_outliers(_glue_scaled(0.1))
    assert flags and all(s.priority == "HIGH" for s in score(flags))


def test_unscaled_glue_shape_is_listed_low_with_note():
    from anomalynerd.analyze import score
    flags = _whole_table(detect_point_outliers(_glue_scaled(1.0)))
    assert len(flags) == 1 and flags[0].detail["column_scale"] is True
    assert score(flags)[0].priority == "LOW"
