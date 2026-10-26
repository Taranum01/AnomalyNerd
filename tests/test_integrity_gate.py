import json
import pytest
from anomalynerd.latex_ingest import parse_latex,_parse_tabular,_clean_cell,expand_project
from anomalynerd.bounded import run_bounded

TABLE=r'\begin{tabular}{lr}Method & Score \\ A & 1 \\ B & 2 \\ \end{tabular}'

def test_project_includes_and_missing(tmp_path):
    (tmp_path/'child.tex').write_text(TABLE)
    main=tmp_path/'main.tex';main.write_text(r'\input{child}\input{missing}')
    p=parse_latex(str(main));assert len(p.tables)==1
    assert p.included_files==['main.tex','child.tex']
    assert p.tables[0].source_locations==[{'file':'child.tex','line':1}]
    assert p.tables[0].source_location_status=='exact'
    assert any('missing' in x for x in p.include_issues)

def test_cycle_and_outside_not_followed(tmp_path):
    p=tmp_path/'main.tex';p.write_text(r'\input{main}\input{../secret}')
    text,files,issues=expand_project(str(p));assert len(files)==1
    assert any('cycle' in x for x in issues)
    assert any('outside' in x for x in issues)

@pytest.mark.parametrize('rule',[r'\cmidrule(lr){2-10}',r'\cmidrule[1pt](lr){2-10}',r'\cline{2-10}',r'\midrule[1pt]'])
def test_rule_coordinates_removed(rule):assert _clean_cell(rule+' x')=='x'

def test_span_and_escaped_ampersand():
    h,r=_parse_tabular(r'{lrr}Method & \multicolumn{2}{c}{Scores} \\ & A & B \\ X\&Y & 1 & 2 \\ Z & 3 & 4 \\')
    assert len(h)==3 and r[0]==['X&Y','1','2']

def test_multirow():
    h,r=_parse_tabular(r'{llr}Group & Method & Score \\ \multirow{2}{*}{G} & A & 1 \\ & B & 2 \\')
    assert r==[['G','A','1'],['G','B','2']]

def test_ragged_not_silently_padded():
    p=parse_latex(r'\begin{tabular}{lr}A&B\\ X&1&2\\ Z&3\\\end{tabular}')
    assert p.tables[0].status=='skipped' and 'extraction failure' in p.tables[0].reason

def test_checkpoint_reused_and_timeout_explicit(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,runs=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert all(r['status']=='completed' for r in runs)
    _,again=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert again==runs
    _,short=run_bounded(tidy,seconds=.00001)
    assert all(r['status']=='timed_out' for r in short)


def test_fragmented_pdf_header_is_extraction_failure():
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [['Method Runtime','s (ms) Reference','gain (%)'],['A','10','12'],['B','11','13']]
    t=_assess_and_tidy(rows,1,0)
    assert t.status=='skipped' and t.original_header==rows[0]
    assert 'split across columns' in t.reason

def test_caption_figure_header_is_not_analyzed():
    from anomalynerd.pdf_ingest import _assess_and_tidy
    t=_assess_and_tidy([['Figure 5: Mean weighted','latency','comparison'],['A','1','2'],['B','3','4']],1,0)
    assert t.status=='skipped' and t.tidy is None


def test_duplicate_source_locations_are_ambiguous(tmp_path):
    (tmp_path/'a.tex').write_text(TABLE)
    (tmp_path/'b.tex').write_text(TABLE)
    p=tmp_path/'main.tex';p.write_text(r'\input{a}\input{b}')
    parsed=parse_latex(str(p))
    assert all(t.source_location_status=='ambiguous_or_unmapped' for t in parsed.tables)
    assert all(len(t.source_locations)==2 for t in parsed.tables)

def test_checkpoint_corruption_recomputed(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,first=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    files=list(tmp_path.glob('*.json'));files[0].write_text('{broken')
    _,second=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert all(r['status']=='completed' for r in second)
    assert json.loads(files[0].read_text())['status']=='completed'

def test_timeout_checkpoint_not_reused(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,runs=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    for path in tmp_path.glob('*.json'):
        record=json.loads(path.read_text());record['status']='timed_out';record['reason']='synthetic timeout';path.write_text(json.dumps(record))
    _,again=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert all(r['status']=='completed' for r in again)

def test_checkpoint_configuration_changes_key(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,a=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    _,b=run_bounded(tidy,seconds=3,checkpoint_dir=str(tmp_path))
    assert a[0]['key']!=b[0]['key']


def test_detectors_complete_via_script_launch(tmp_path):
    """An unguarded real script must not be reexecuted by a spawned worker."""
    import subprocess, sys, json, os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = tmp_path / "run_probe.py"
    script.write_text(
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "from anomalynerd.ingest import read_tables\n"
        "from anomalynerd.bounded import run_bounded\n"
        "t = read_tables(%r)[0]\n"
        "flags, runs = run_bounded(t, seconds=8.0)\n"
        "print('RESULT' + json.dumps({'statuses': [r['status'] for r in runs],\n"
        "      'types': sorted({f.type for f in flags})}))\n"
        % (repo, os.path.join(repo, "examples/draft_lottery_1970.csv"))
    )
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    line = [l for l in out.stdout.splitlines() if l.startswith("RESULT")]
    assert line, f"child produced no result; stderr tail: {out.stderr[-500:]}"
    data = json.loads(line[0][len("RESULT"):])
    assert all(s == "completed" for s in data["statuses"]), \
        f"detectors must complete via .py script launch, got {data['statuses']}"
    assert "trend" in data["types"], "the lottery trend must be detected"


@pytest.mark.parametrize('launch', ['guarded', 'stdin', 'command'])
def test_detectors_complete_in_other_launch_contexts(tmp_path, launch):
    import subprocess, sys, os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    body = (
        'from anomalynerd.ingest import read_tables\n'
        'from anomalynerd.bounded import run_bounded\n'
        't = read_tables(%r)[0]\n'
        'flags, runs = run_bounded(t, seconds=8)\n'
        'assert len(runs) == 8 and all(r["status"] == "completed" for r in runs)\n'
        'assert "trend" in {f.type for f in flags}\n'
    ) % os.path.join(repo, 'examples/draft_lottery_1970.csv')
    env = os.environ.copy(); env['PYTHONPATH'] = repo
    if launch == 'guarded':
        script = tmp_path / 'guarded.py'
        script.write_text('def main():\n' + ''.join('    '+l+'\n' for l in body.splitlines()) +
                          'if __name__ == "__main__": main()\n')
        command = [sys.executable, str(script)]
    else:
        command = [sys.executable, '-'] if launch == 'stdin' else [sys.executable, '-c', body]
    out = subprocess.run(command, input=body if launch == 'stdin' else None,
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


def _controlled_worker(monkeypatch, code):
    """Use the real process lifecycle, replacing only the child's detector body."""
    import anomalynerd.bounded as bounded
    original = bounded.subprocess.Popen
    children = []
    def launch(command, **kwargs):
        child = original([command[0], '-c', code], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(bounded.subprocess, 'Popen', launch)
    return bounded, children


def test_timeout_kills_and_reaps_slow_worker(tmp_path, monkeypatch):
    import time
    marker = tmp_path / 'late-write'
    bounded, children = _controlled_worker(monkeypatch,
        'import time; from pathlib import Path; time.sleep(.5); Path(%r).write_text("late")' % str(marker))
    started = time.monotonic()
    record = bounded._run_bounded_one('unused', None, None, .05)
    assert record['status'] == 'timed_out' and record['flags'] == []
    assert time.monotonic() - started < 2
    assert len(children) == 1 and children[0].returncode is not None
    time.sleep(.6)
    assert not marker.exists(), 'timed-out detector kept running'


def test_worker_crash_does_not_retry_in_process(monkeypatch):
    bounded, children = _controlled_worker(monkeypatch, 'import os; os._exit(23)')
    def forbidden(table):
        pytest.fail('crashed detector was retried in the caller')
    monkeypatch.setattr(bounded.detectors, 'detect_point_outliers', forbidden)
    record = bounded._run_bounded_one('detect_point_outliers', None, None, 2)
    assert record['status'] == 'failed' and record['flags'] == []
    assert '23' in record['reason']
    assert len(children) == 1 and children[0].returncode == 23


def test_worker_missing_result_is_explicit_failure(monkeypatch):
    bounded, children = _controlled_worker(monkeypatch, 'pass')
    record = bounded._run_bounded_one('unused', None, None, 2)
    assert record['status'] == 'failed' and record['flags'] == []
    assert 'without result' in record['reason']
    assert len(children) == 1


def test_worker_start_failure_cleans_resources(tmp_path, monkeypatch):
    import anomalynerd.bounded as bounded
    from pathlib import Path
    monkeypatch.setattr(bounded.tempfile, 'tempdir', str(tmp_path))
    def fail(*args, **kwargs):
        raise OSError('controlled launch failure')
    monkeypatch.setattr(bounded.subprocess, 'Popen', fail)
    fd_dir = Path('/proc/self/fd')
    before = len(list(fd_dir.iterdir())) if fd_dir.exists() else None
    for _ in range(20):
        record = bounded._run_bounded_one('unused', None, None, 1)
        assert record['status'] == 'failed' and record['flags'] == []
        assert 'controlled launch failure' in record['reason']
        assert list(tmp_path.iterdir()) == []
    if before is not None:
        assert len(list(fd_dir.iterdir())) == before
