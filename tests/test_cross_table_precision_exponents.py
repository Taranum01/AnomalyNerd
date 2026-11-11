"""Finite scientific values must not overflow rounding-unit intermediates."""
import math
import json
import pytest
from anomalynerd.ingest import _parse_number
from anomalynerd.cross_table import _rounding_tolerance,find_cross_table_inconsistencies
from anomalynerd.model import Axis,TidyTable

@pytest.mark.parametrize('raw,expected',[
 ('0.001e309',5e305),('-0.001e309',5e305),('0.0000001e310',5e302),
 ('0.001E+309',5e305),('0.001x10^309',5e305),('0.001×10^{309}',5e305),
 (r'0.001 \times 10^{309}',5e305),('0.001e309 (0.01)',5e305),
 ('0.'+'0'*399+'1e100',5e-301),('0.'+'0'*349+'1e50',5e-301),
 ('0.'+'0'*399+'1e400',0.5),('0.'+'0'*499+'1e500',0.5),
])
def test_combined_exponent_reports_finite_unit(raw,expected):
    value=_parse_number(raw)
    assert value is not None and math.isfinite(value)
    assert _rounding_tolerance(raw,value)==pytest.approx(expected,rel=1e-14,abs=0)

def mk(name,raw):
    return TidyTable(name,{'method':Axis('method','unordered_cat')},'method','RMSE',True,
                     [{'method':'A','value':_parse_number(raw),'_raw':raw}])

@pytest.mark.parametrize('left,right,flagged',[
 ('0.001e309','0.001e309',False),
 ('0.001e309','0.002e309',False),
 ('0.001e309','0.004e309',True),
 ('0.0000001e310','0.0000004e310',True),
])
def test_actual_cross_table_comparison(left,right,flagged):
    flags,notes=find_cross_table_inconsistencies([mk('first',left),mk('second',right)])
    assert bool(flags)==flagged
    assert notes==[]
    for flag in flags:assert math.isfinite(flag.strength)
    json.dumps([flag.to_dict() for flag in flags], allow_nan=False)
