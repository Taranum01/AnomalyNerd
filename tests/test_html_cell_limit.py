import pytest
from anomalynerd.html_ingest import _TableHTMLParser, HTMLCellLimitError, parse_html


def feed(body, cap):
    p = _TableHTMLParser(max_cells=cap)
    p.feed(body)
    p.close()
    return p


def test_at_limit_is_accepted():
    p = feed('<table><tr><td colspan="4">1</td></tr></table>', 4)
    assert p.tables == [[['1'] * 4]]


def test_colspan_is_checked_before_expansion():
    with pytest.raises(HTMLCellLimitError):
        feed('<table><tr><td colspan="1000">1</td></tr></table>', 4)


def test_rowspan_carry_counts():
    with pytest.raises(HTMLCellLimitError):
        feed('<table><tr><td colspan="3" rowspan="3">1</td></tr><tr></tr><tr></tr></table>', 8)


def test_late_wide_row_counts_padding_of_prior_rows():
    with pytest.raises(HTMLCellLimitError):
        feed('<table><tr><td>1</td></tr><tr><td colspan="5">2</td></tr></table>', 9)


def test_budget_is_document_wide():
    t = '<table><tr><td colspan="3">1</td></tr></table>'
    with pytest.raises(HTMLCellLimitError):
        feed(t + t, 5)


def test_parse_reports_rejection_without_partial_findings(tmp_path):
    p = tmp_path / 'oversized.html'
    p.write_text('<table><tr><td>2</td></tr></table>' + '<table><tr>' + '<td colspan="1000">1</td>' * 101 + '</tr></table>')
    result = parse_html(str(p))
    assert result.tables == [] and result.stat_strings == []
    assert any('cell limit' in n and 'not analyzed' in n for n in result.notes)


# ---- default cap: boundary, document-wide budget, normal pages, and what the report says ----
from anomalynerd.html_ingest import MAX_HTML_CELLS


def _page(rows, cols):
    return '<table>' + ('<tr>' + '<td>1</td>' * cols + '</tr>') * rows + '</table>'


def test_default_cap_accepts_exactly_the_limit(tmp_path):
    p = tmp_path / 'at_limit.html'
    p.write_text(_page(MAX_HTML_CELLS // 100, 100))
    assert parse_html(str(p)).tables


def test_default_cap_rejects_one_row_over(tmp_path):
    p = tmp_path / 'over.html'
    p.write_text(_page(MAX_HTML_CELLS // 100 + 1, 100))
    result = parse_html(str(p))
    assert result.tables == [] and any('cell limit' in n for n in result.notes)


def test_many_small_tables_share_one_budget(tmp_path):
    p = tmp_path / 'many.html'
    p.write_text(_page(1, 1000) * (MAX_HTML_CELLS // 1000 + 1))
    assert parse_html(str(p)).tables == []


def test_wide_span_fixture_is_rejected_quickly(tmp_path):
    p = tmp_path / 'wide.html'
    p.write_text('<table>' + "<tr><td colspan='1000'>x</td></tr>" * 20000 + '</table>')
    assert parse_html(str(p)).tables == []


def test_rejection_is_visible_in_the_report(tmp_path):
    from anomalynerd.analyze_pdf import analyze_source
    p = tmp_path / 'wide.html'
    p.write_text('<table>' + "<tr><td colspan='1000'>x</td></tr>" * 2000 + '</table>')
    res = analyze_source(str(p))
    assert any('cell limit' in line and 'not analyzed' in line for line in res.could_not_check)
