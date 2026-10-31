"""Routing of pdfplumber tables with fused cells to the layout model (no Docling needed)."""
from anomalynerd.pdf_ingest import _add_layout_tables, _assess_and_tidy, is_fused


def _tbl(rows, page, idx=0):
    et = _assess_and_tidy(rows, page, idx)
    return et


CLEAN = [["Model", "a", "b"], ["x", "1.0", "2.0"], ["y", "3.0", "4.0"], ["z", "5.0", "6.0"]]
FUSED = [["Model", "a", "b", "c", "d"], ["x", "1.0 3.0 9.0", "2.0 4.0 8.0", "1.3", "2.7"], ["y", "1.1", "2.2", "3.1", "4.2"],
         ["z", "5.0", "6.0", "5.5", "6.5"], ["w", "7.0", "8.0", "7.5", "8.5"]]
LAYOUT = [["Model", "a", "b"], ["x", "1.5", "2.5"], ["y", "3.5", "4.5"], ["z", "5.5", "6.5"]]


def _fused(t):
    return any(is_fused(c) for r in t.rows for c in r)


def test_fused_table_replaced_on_same_page():
    fused = _tbl(FUSED, 1)
    assert _fused(fused)
    layout = _tbl(LAYOUT, 1)
    out = _add_layout_tables([fused], [layout], [fused])
    assert out == [layout]


def test_fused_table_kept_when_layout_model_found_nothing():
    fused = _tbl(FUSED, 1)
    out = _add_layout_tables([fused], [], [fused])
    assert out == [fused]


def test_fused_table_kept_when_layout_table_is_on_another_page():
    fused = _tbl(FUSED, 1)
    layout = _tbl(LAYOUT, 2)
    out = _add_layout_tables([fused], [layout], [fused])
    assert fused in out and layout in out


def test_clean_tables_on_the_same_page_are_kept_and_not_duplicated():
    fused = _tbl(FUSED, 1, 0)
    clean = _tbl(CLEAN, 1, 1)
    dup_of_clean = _tbl(CLEAN, 1, 0)
    layout = _tbl(LAYOUT, 1, 1)
    out = _add_layout_tables([fused, clean], [dup_of_clean, layout], [fused])
    assert clean in out and layout in out and fused not in out
    assert dup_of_clean not in out
    assert len(out) == 2


def test_no_fused_tables_changes_nothing_but_dedupes():
    clean = _tbl(CLEAN, 1)
    out = _add_layout_tables([clean], [_tbl(CLEAN, 1)], [])
    assert out == [clean]
