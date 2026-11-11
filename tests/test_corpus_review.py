import json
from pathlib import Path
import subprocess
import sys
import pytest
from anomalynerd import corpus_cli as cc


def report(path, **updates):
    value = dict(path=str(path), pages=1, tables_detected=1, tables_extraction_usable=1,
                 metric_slices_completed=1, extraction=[{'status':'ok'}], pattern_flags=[],
                 stat_findings=[], could_not_check=[], takeaways=[], detector_runs={'1':[{'status':'completed'}]},
                 coverage_unit='detected extraction regions')
    value.update(updates)
    return value

@pytest.fixture
def inputs(tmp_path):
    files=[tmp_path/'one.pdf', tmp_path/'two.pdf']
    for f in files:f.write_bytes(b'fixture')
    return files,tmp_path/'out'

def success(calls, **updates):
    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd,0,json.dumps(report(cmd[3],**updates)),'')
    return run

def test_two_files_manifest_and_resume(inputs):
    files,out=inputs;calls=[]
    first=cc.review_files(files,out,run=success(calls))
    assert first['completed']==2 and len(calls)==2
    second=cc.review_files(files,out,run=success(calls))
    assert all(r['resumed'] for r in second['files']) and len(calls)==2
    assert json.loads((out/'manifest.json').read_text())==second

def test_changed_input_invalidates_only_its_cache(inputs):
    files,out=inputs;calls=[];cc.review_files(files,out,run=success(calls))
    files[0].write_bytes(b'changed');result=cc.review_files(files,out,run=success(calls))
    assert len(calls)==3 and [r['resumed'] for r in result['files']]==[False,True]

def test_settings_invalidate_cache(inputs,monkeypatch):
    files,out=inputs;calls=[];cc.review_files(files,out,run=success(calls))
    cc.review_files(files,out,seconds=2,run=success(calls));assert len(calls)==4
    monkeypatch.setenv('ANOMALYNERD_FUSED_RECOVERY','true')
    cc.review_files(files,out,seconds=2,run=success(calls));assert len(calls)==6

def test_engine_changes_invalidate_cache(inputs,monkeypatch):
    files,out=inputs;calls=[];cc.review_files(files,out,run=success(calls))
    monkeypatch.setattr(cc,'_engine_fingerprint',lambda:'changed')
    cc.review_files(files,out,run=success(calls));assert len(calls)==4

@pytest.mark.parametrize('failure',['exit','timeout','json','schema','wrong_path'])
def test_failure_continues_and_retries(inputs,failure):
    files,out=inputs;calls=[]
    def run(cmd,**kw):
        calls.append(cmd)
        if len(calls)>1:return subprocess.CompletedProcess(cmd,0,json.dumps(report(cmd[3])),'')
        if failure=='timeout':raise subprocess.TimeoutExpired(cmd,kw['timeout'])
        if failure=='exit':return subprocess.CompletedProcess(cmd,3,'','private error')
        if failure=='json':return subprocess.CompletedProcess(cmd,0,'not json','')
        if failure=='schema':return subprocess.CompletedProcess(cmd,0,'{}','')
        return subprocess.CompletedProcess(cmd,0,json.dumps(report('wrong')),'')
    result=cc.review_files(files,out,run=run)
    assert result['completed']==1 and result['failed']+result['timed_out']==1
    assert 'private error' not in json.dumps(result)
    again=cc.review_files(files,out,run=success(calls));assert again['completed']==2 and len(calls)==3

@pytest.mark.parametrize('corruption',['report','checkpoint','missing'])
def test_corrupt_checkpoint_recomputed(inputs,corruption):
    files,out=inputs;calls=[];first=cc.review_files(files[:1],out,run=success(calls));item=first['files'][0]
    target=out/item['report_file']
    if corruption=='report':target.write_text('{}')
    elif corruption=='checkpoint':target.with_name(target.stem+'.checkpoint.json').write_text('null')
    else:target.unlink()
    cc.review_files(files[:1],out,run=success(calls));assert len(calls)==2

@pytest.mark.parametrize('updates',[{'could_not_check':['missing']},{'extraction':[{'status':'partial'}]},
 {'detector_runs':{'1':[{'status':'timeout'}]}} , {'detector_runs':{'1':[{'status':'skipped'}]}},{'stat_findings':[{'status':'not_applicable'}]},
 {'tables_detected':0,'tables_extraction_usable':0,'extraction':[]}])
def test_unchecked_work_not_hidden(inputs,updates):
    files,out=inputs;result=cc.review_files(files[:1],out,run=success([],**updates))
    assert result['completed']==1 and result['files'][0]['coverage']['has_unchecked_work']

@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf')])
def test_bad_timeout_rejected_before_output(inputs,value):
    files,out=inputs
    with pytest.raises(ValueError):cc.review_files(files,out,seconds=value)
    assert not out.exists()

def test_duplicates_and_unsupported_rejected(inputs):
    files,out=inputs
    with pytest.raises(ValueError):cc.review_files([files[0],files[0]],out)
    bad=files[0].with_suffix('.txt');bad.write_text('fixture')
    with pytest.raises(ValueError):cc.review_files([bad],out)
    assert not out.exists()

def test_tex_project_never_resumes_root_only_hash(inputs):
    files,out=inputs;source=files[0].with_suffix('.tex');source.write_text('fixture');calls=[]
    cc.review_files([source],out,run=success(calls));cc.review_files([source],out,run=success(calls))
    assert len(calls)==2

def test_input_mutation_during_run_not_checkpointed(inputs):
    files,out=inputs
    def run(cmd,**kw):
        files[0].write_bytes(b'changed')
        return subprocess.CompletedProcess(cmd,0,json.dumps(report(cmd[3])),'')
    result=cc.review_files(files[:1],out,run=run)
    assert result['failed']==1 and not list(out.glob('*.checkpoint.json'))

def test_failed_forced_rerun_invalidates_old_success(inputs):
    files,out=inputs;calls=[];cc.review_files(files[:1],out,run=success(calls))
    cc.review_files(files[:1],out,resume=False,run=lambda *a,**kw:subprocess.CompletedProcess(a[0],1,'',''))
    cc.review_files(files[:1],out,run=success(calls));assert len(calls)==2

def test_real_process_timeout():
    with pytest.raises(subprocess.TimeoutExpired):
        cc._run_article([sys.executable,'-c','import time; time.sleep(30)'],timeout=.05)

def test_real_tex_article_process(tmp_path):
    source=tmp_path/'article.tex'
    source.write_text(r'''\documentclass{article}\begin{document}
\begin{tabular}{lr}Method & Accuracy \\
A & 1 \\ B & 2 \\ C & 3 \\ D & 40 \\ E & 5 \\
\end{tabular}\end{document}''')
    result=cc.review_files([source],tmp_path/'reports',seconds=20,detector_seconds=.2)
    assert result['completed']==1
    stored=json.loads((tmp_path/'reports'/result['files'][0]['report_file']).read_text())
    assert stored['path']==str(source) and stored['tables_detected']>=1
    assert stored['coverage_unit']=='detected extraction regions, not verified true table count'

def test_nonfinite_report_not_saved(inputs):
    files,out=inputs
    result=cc.review_files(files[:1],out,run=success([],stat_findings=[{'status':'not_applicable','evidence':float('nan')}]))
    assert result['failed']==1 and not list(out.glob('*.checkpoint.json'))

def test_real_timeout_kills_descendant(tmp_path):
    import os,time
    if os.name!='posix':pytest.skip('POSIX process-group assertion')
    marker=tmp_path/'must-not-exist'
    child='import time,pathlib; time.sleep(.5); pathlib.Path('+repr(str(marker))+').write_text("alive")'
    parent='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(child)+']); time.sleep(30)'
    with pytest.raises(subprocess.TimeoutExpired):cc._run_article([sys.executable,'-c',parent],timeout=.1)
    time.sleep(.6)
    assert not marker.exists()

@pytest.mark.parametrize('status',['failed','timeout','error'])
def test_unfinished_detectors_are_retried(inputs,status):
    files,out=inputs;calls=[]
    cc.review_files(files[:1],out,run=success(calls,detector_runs={'1':[{'status':status}]}))
    result=cc.review_files(files[:1],out,run=success(calls))
    assert len(calls)==2 and not result['files'][0]['resumed']

def test_completed_report_preserves_direct_article_output(inputs):
    files,out=inputs;expected=report(files[0],pattern_flags=[{'type':'example','detail':{'value':12}}])
    def run(cmd,**kwargs):return subprocess.CompletedProcess(cmd,0,json.dumps(expected),'')
    result=cc.review_files(files[:1],out,run=run)
    assert json.loads((out/result['files'][0]['report_file']).read_text())==expected
