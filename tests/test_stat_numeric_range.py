"""Unrepresentable statistic arithmetic is unchecked, not a whole-report crash."""
import math
import pytest
from anomalynerd.stat_tests import grim, grimmer
from anomalynerd.analyze_pdf import analyze_source

EXTREMES=[
 ('infinite-mean','9'*309+'.0','1.0'),
 ('negative-infinite-mean','-'+'9'*309+'.0','1.0'),
 ('finite-mean-product','9'*308+'.0','1.0'),
 ('negative-finite-mean-product','-'+'9'*308+'.0','1.0'),
 ('finite-mean-square','9'*155+'.0','1.0'),
 ('infinite-sd','1.0','9'*309+'.0'),
 ('finite-sd-square','1.0','9'*155+'.0'),
 ('finite-sd-sum','1.0','9'*153+'.0'),
]

@pytest.mark.parametrize('name,mean,sd',EXTREMES)
def test_actual_analysis_keeps_other_statistics_when_range_is_unrepresentable(tmp_path,name,mean,sd):
 s=f'M={mean}, SD={sd}, N=1000'
 path=tmp_path/'range.tex'
 path.write_text('\\documentclass{article}\n\\begin{document}\n'+s+'\n M=1.2, SD=1.0, N=10\n z=0.0, p=1.0\n\\end{document}')
 r=analyze_source(str(path))
 failed_kind='grim' if 'mean' in name and 'square' not in name else 'grimmer'
 records=[x for x in r.stat_findings if x.kind==failed_kind and x.evidence.get('reported')==s]
 assert len(records)==1
 assert records[0].status=='not_applicable'
 assert 'numeric range' in records[0].detail.lower()
 assert any('numeric range' in x.lower() for x in r.could_not_check)
 assert any(x.kind=='grim' and x.status=='ok' and x.evidence.get('reported')=='M=1.2, SD=1.0, N=10' for x in r.stat_findings)
 assert any(x.kind=='statcheck' and x.status=='ok' for x in r.stat_findings)

@pytest.mark.parametrize('name,mean,sd',EXTREMES)
def test_direct_checks_do_not_crash(name,mean,sd):
 s=f'M={mean}, SD={sd}, N=1000'
 for fn in (grim,grimmer):
  result=fn([s])
  assert result
  for finding in result:
   assert finding.status in ('ok','finding','not_applicable')
   assert all(not isinstance(v,float) or math.isfinite(v) for v in finding.evidence.values())

@pytest.mark.parametrize('fn,s,status',[(grim,'M=1.2, SD=1.0, N=10','ok'),(grim,'M=1.23, SD=1.0, N=10','finding'),(grimmer,'M=1.0, SD=1.0, N=10','ok'),(grimmer,'M=1.23, SD=1.0, N=10','not_applicable')])
def test_ordinary_result_unchanged(fn,s,status):
 assert fn([s])[0].status==status
