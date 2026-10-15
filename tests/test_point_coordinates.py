"""Categorical point-outlier flags must name the flagged category in coords.

Before the fix, coords carried only the fixed (slicing) axes, so a flag could
not say WHICH category was anomalous. These tests pin the coordinates and
guard the neighboring modes (numeric axes, leverage, missing values)."""
import pytest
from anomalynerd.detectors import detect_point_outliers
from anomalynerd.model import TidyTable, Axis


def _table(rows, axes, entity='method', metric='score', lower=False, name='synthetic'):
    return TidyTable(name, axes, entity, metric, lower, rows)


CAT_ROWS = [{'group': 'g', 'method': f'm{i}', 'value': v}
            for i, v in enumerate([1, 1.01, 1.02, 1.03, 1.04, 20])]
CAT_AXES = {'group': Axis('group', 'unordered_cat'), 'method': Axis('method', 'unordered_cat')}


@pytest.mark.parametrize("missing", [True, False])
def test_categorical_outlier_keeps_full_coordinates(missing):
    rows = CAT_ROWS + ([{'group': 'g', 'method': 'mx', 'value': None}] if missing else [])
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert len(flags) == 1
    assert flags[0].coords == {'group': 'g', 'method': 'm5'}


def test_single_axis_categorical_outlier_coordinates_are_not_empty():
    rows = [{k: v for k, v in r.items() if k != 'group'} for r in CAT_ROWS]
    table = _table(rows, {'method': Axis('method', 'unordered_cat')})
    flags = detect_point_outliers(table)
    assert len(flags) == 1
    assert flags[0].coords == {'method': 'm5'}
    assert flags[0].detail['at'] == 'm5'


def test_flagged_category_matches_detail_at():
    table = _table(list(CAT_ROWS), dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert len(flags) == 1
    axis_name = flags[0].axis
    assert flags[0].coords[axis_name] == flags[0].detail['at']


def test_each_slice_flags_its_own_outlier_category():
    rows = []
    for g, out in [('g1', 'm2'), ('g2', 'm4')]:
        for i, v in enumerate([1, 1.01, 1.02, 1.03, 1.04, 1.05]):
            v = 20 if (g, f'm{i}') == (g, out) else v
            rows.append({'group': g, 'method': f'm{i}', 'value': v})
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert len(flags) == 2
    by_group = {f.coords['group']: f.coords['method'] for f in flags}
    assert by_group == {'g1': 'm2', 'g2': 'm4'}


def test_clean_categorical_slice_produces_no_flag():
    rows = [{'group': 'g', 'method': f'm{i}', 'value': v}
            for i, v in enumerate([1, 1.01, 1.02, 1.03, 1.04, 1.05])]
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert flags == []


def test_outlier_at_first_category_is_named():
    rows = [{'group': 'g', 'method': f'm{i}', 'value': v}
            for i, v in enumerate([20, 1.0, 1.01, 1.02, 1.03, 1.04])]
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert len(flags) == 1
    assert flags[0].coords['method'] == 'm0'


def test_low_outlier_is_named_not_just_high_outlier():
    rows = [{'group': 'g', 'method': f'm{i}', 'value': v}
            for i, v in enumerate([10, 10.1, 10.2, 10.3, 10.4, 0.01])]
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert len(flags) == 1
    assert flags[0].coords['method'] == 'm5'
    assert flags[0].detail['value'] == 0.01


def test_ordered_categorical_axis_also_names_the_level():
    rows = [{'dose': d, 'value': v}
            for d, v in zip(['low', 'medium', 'high', 'xhigh', 'xxhigh', 'max'],
                            [1, 1.01, 1.02, 1.03, 1.04, 20])]
    table = _table(rows, {'dose': Axis('dose', 'ordered_cat')}, entity='dose')
    flags = detect_point_outliers(table)
    assert len(flags) == 1
    assert flags[0].coords.get('dose') == 'max'  # any mode must name the level


def test_coords_do_not_mutate_between_flags():
    rows = []
    for g, out in [('g1', 'm1'), ('g2', 'm3')]:
        for i, v in enumerate([1, 1.01, 1.02, 1.03, 1.04, 1.05]):
            v = 30 if (g, f'm{i}') == (g, out) else v
            rows.append({'group': g, 'method': f'm{i}', 'value': v})
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert len(flags) == 2
    coordsets = {tuple(sorted(f.coords.items())) for f in flags}
    assert len(coordsets) == 2  # the two flags did not share/overwrite one coords dict


def test_min_points_guard_still_applies_on_categorical_axis():
    rows = [{'group': 'g', 'method': f'm{i}', 'value': v}
            for i, v in enumerate([1, 1.01, 20])]
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert flags == []


def test_numeric_axis_flags_unaffected_by_coordinate_change():
    # one point far off an otherwise clean line: the residual mode must still fire
    rows = [{'x': x, 'value': y} for x, y in zip(range(1, 11), [1, 2, 3, 4, 5, 6, 7, 50, 9, 10])]
    table = _table(rows, {'x': Axis('x', 'numeric')}, entity='x')
    flags = detect_point_outliers(table)
    assert len(flags) == 1
    assert flags[0].detail.get('mode') != 'categorical'
    assert flags[0].detail.get('at') == 8.0


def test_log_scale_categorical_outlier_keeps_category():
    rows = [{'group': 'g', 'method': f'm{i}', 'value': v}
            for i, v in enumerate([100, 101, 99, 100.5, 99.5, 100000])]
    table = _table(rows, dict(CAT_AXES))
    flags = [f for f in detect_point_outliers(table) if f.detail.get('mode') == 'categorical']
    assert len(flags) == 1
    assert flags[0].coords['method'] == 'm5'
