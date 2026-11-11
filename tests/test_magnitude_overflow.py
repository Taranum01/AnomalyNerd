"""Suffix normalization must honor the parser's finite-or-missing contract."""
import math
from pathlib import Path
import pytest
from anomalynerd.ingest import _parse_number, read_csv, read_tables
from anomalynerd.model import MISSING

# The mantissa fits a float, but applying its suffix overflows it.
OVERFLOW = '9' * 308

@pytest.mark.parametrize('suffix', ['k', 'K', 'M', 'B'])
@pytest.mark.parametrize('sign', ['', '-'])
def test_suffix_product_overflow_is_missing(sign, suffix):
    assert _parse_number(sign + OVERFLOW + suffix) is MISSING

@pytest.mark.parametrize('token', ['9' * 400 + 'k', '-' + '9' * 400 + 'B'])
def test_suffix_mantissa_overflow_is_missing(token):
    assert _parse_number(token) is MISSING

@pytest.mark.parametrize('entrypoint', [read_csv, read_tables])
def test_overflow_metric_does_not_crash_index_detection(tmp_path, entrypoint):
    p = tmp_path / 'fixture.csv'
    p.write_text('step,value\n1,1\n2,2\n3,' + OVERFLOW + 'B\n4,4\n5,5\n')
    tables = entrypoint(str(p))
    if not isinstance(tables, list):
        tables = [tables]
    assert len(tables) == 1
    assert tables[0].rows[2]['value'] is MISSING
    assert tables[0].rows[2]['_raw'] == OVERFLOW + 'B'
    assert [r['value'] for i,r in enumerate(tables[0].rows) if i != 2] == [1,2,4,5]

@pytest.mark.parametrize('token,expected', [('1k', 1000), ('300K', 300000), ('1.5M', 1500000), ('175B', 175000000000), ('-2K', -2000), ('0B', 0)])
def test_normal_suffixes_preserve_value(token, expected):
    actual = _parse_number(token)
    assert math.isfinite(actual)
    assert actual == expected
