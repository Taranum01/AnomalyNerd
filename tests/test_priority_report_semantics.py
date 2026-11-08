import copy,json
from types import SimpleNamespace as NS
import pytest
from anomalynerd.analyze import PRIORITY_NOTE,score,analyze
from anomalynerd.cli import _fmt_report
from anomalynerd.analyze_pdf import PdfAnalysis,format_report
from anomalynerd.model import Flag,Axis,TidyTable

def flag(priority='HIGH',typ='point_outlier'):
    return Flag(typ,'test','x',{'method':'A'},{'value':123},9,1,priority,'check this value')
def pdf(flags):
    t=NS(status='ok',page=1,index_on_page=0,tidies=[NS(metric_name='score')],caveats=[])
    p=NS(path='test.pdf',tables=[t],stat_strings=[],n_pages=1)
    r=PdfAnalysis('test.pdf',p);r.table_flags={(1,0,0):flags};return r
@pytest.mark.parametrize('priority',['HIGH','MEDIUM','LOW'])
def test_csv_priorities_preserved(priority):
    f=flag(priority);before=copy.deepcopy(f.to_dict());text=_fmt_report([f],'test')
    assert PRIORITY_NOTE in text and f'[{priority}]' in text
    assert f.to_dict()==before
@pytest.mark.parametrize('priority',['HIGH','MEDIUM','LOW'])
def test_pdf_priorities_preserved(priority):
    f=flag(priority);before=copy.deepcopy(f.to_dict());text=format_report(pdf([f]))
    assert text.count(PRIORITY_NOTE)==1 and f'[{priority}]' in text
    assert f.to_dict()==before
@pytest.mark.parametrize('typ',['point_outlier','flatness','duplicate','unit_mismatch'])
def test_mixed_detector_json_unchanged(typ):
    fs=[flag('MEDIUM',typ),flag('HIGH')];old=json.dumps([f.to_dict() for f in fs],sort_keys=True)
    _fmt_report(fs,'test');format_report(pdf(fs))
    assert json.dumps([f.to_dict() for f in fs],sort_keys=True)==old
    assert [f.type for f in fs]==[typ,'point_outlier']
def test_no_flags_csv_preserves_existing_message():
    assert _fmt_report([],'test')=="AnomalyNerd: no anomalies flagged in 'test'."
def test_pdf_no_flags_keeps_coverage_and_qualification():
    r=pdf([]);r.could_not_check=['another table skipped'];text=format_report(r)
    assert PRIORITY_NOTE in text and 'another table skipped' in text

def test_current_score_strength_and_context_still_apply():
    fs=[Flag('flatness','t','x',{}, {},1,1),Flag('point_outlier','t','x',{}, {},9,1),Flag('point_outlier','t','x',{}, {'column_scale':True},9,1)]
    scored=score(fs)
    assert [(f.type,f.priority) for f in scored]==[('point_outlier','HIGH'),('flatness','MEDIUM'),('point_outlier','LOW')]

def test_current_synthetic_detector_report_does_not_reorder():
    t=TidyTable('series',{'x':Axis('x','numeric')},None,'score',False,[{'x':i,'value':x} for i,x in enumerate([10.1,10.2,10.3,10.4,999.5,10.6,10.7,10.8])])
    fs=analyze(t);assert fs
    old=[f.to_dict() for f in fs];text=_fmt_report(fs,t.name)
    assert PRIORITY_NOTE in text and [f.to_dict() for f in fs]==old
