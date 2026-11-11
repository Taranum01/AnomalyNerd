"""Unrecovered stacked candidates retain data but must not claim a clean parse."""
import copy
import pytest
from anomalynerd import pdf_ingest as pi
from test_pdf_pipeline import (_FakePDF, _FakePage, _STACKED_ROWS, _STACK_A,
                               _STACK_B, _GOOD_ROWS, _docling_on_page)

NOTES = [None, 'layout backend unavailable', 'layout conversion failed',
         'layout ran but recovered no usable results']

def run(monkeypatch, recovered, note, rows=_STACKED_ROWS):
    monkeypatch.setattr(pi.pdfplumber, 'open', lambda p: _FakePDF([_FakePage([rows])]))
    monkeypatch.setattr(pi, '_try_docling', lambda p: (recovered, note))
    return pi.parse_pdf('fake.pdf')

@pytest.mark.parametrize('note', NOTES)
@pytest.mark.parametrize('recovery', ['none', 'partial', 'wrong-page'])
def test_unrecovered_stack_is_partial_with_local_caveat(monkeypatch, note, recovery):
    original = pi._assess_and_tidy(copy.deepcopy(_STACKED_ROWS), 1, 0)
    recovered = [] if recovery == 'none' else [_docling_on_page(_STACK_A, 2 if recovery == 'wrong-page' else 1)]
    result = run(monkeypatch, recovered, note)
    retained = next(t for t in result.tables if t.backend == 'pdfplumber')
    assert retained.status == 'partial'
    assert 'stacked' in retained.reason.lower()
    assert any('stacked' in c.lower() and 'not fully' in c.lower() for c in retained.caveats)
    assert retained.rows == original.rows
    assert retained.header == original.header
    assert retained.tidy == original.tidy
    assert retained.tidies == original.tidies
    assert result.notes == ([] if note is None else [note])
    assert len(retained.caveats) == len(set(retained.caveats))

@pytest.mark.parametrize('note', NOTES[:2])
def test_fully_recovered_stack_has_no_unrecovered_caveat(monkeypatch, note):
    result = run(monkeypatch, [_docling_on_page(_STACK_A), _docling_on_page(_STACK_B)], note)
    assert all(t.backend == 'docling' for t in result.tables)
    assert not any('not fully' in c for t in result.tables for c in t.caveats)

@pytest.mark.parametrize('unused_note', [None, 'backend unavailable'])
def test_clean_table_stays_unchanged(monkeypatch, unused_note):
    rows = _GOOD_ROWS
    expected = pi._assess_and_tidy(copy.deepcopy(rows), 1, 0)
    result = run(monkeypatch, [], unused_note, rows)
    assert len(result.tables) == 1
    actual = result.tables[0]
    assert (actual.status, actual.reason, actual.caveats, actual.rows) == (expected.status, expected.reason, expected.caveats, expected.rows)
    assert result.notes == []
