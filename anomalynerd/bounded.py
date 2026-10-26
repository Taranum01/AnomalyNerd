"""Per-detector process limits with content-addressed resumable checkpoints."""
import inspect
import hashlib,json,os,pickle,subprocess,sys,tempfile,time
from dataclasses import asdict
from pathlib import Path
from . import detectors
from .model import Flag
from .analyze import score,is_pathological,_suppress_index_axis,_suppress_jumps_adjacent_to_outliers

DETECTORS = ('detect_numeric_jumps','detect_win_reversals','detect_monotonicity',
             'detect_cat_exceptions','detect_duplicates_and_flatness','detect_level_shifts',
             'detect_point_outliers','detect_trends')
VERSION = 'bounded-v1'

def _subprocess_main():
    """Importable worker entry point; never re-import the caller's __main__."""
    request_path, result_path = sys.argv[1:3]
    with open(request_path, 'rb') as f:
        name, table, expect_flat = pickle.load(f)
    try:
        kwargs = {'expect_flat': expect_flat} if name in ('detect_level_shifts', 'detect_trends') else {}
        if name == 'detect_trends':
            kwargs['index_axes'] = detectors.index_like_axes(table)
        flags = getattr(detectors, name)(table, **kwargs)
        record = {'status': 'completed', 'flags': [asdict(f) for f in flags],
                  'notes': detectors.drain_notes()}
    except Exception as exc:
        record = {'status': 'failed', 'reason': type(exc).__name__ + ': ' + str(exc),
                  'flags': [], 'notes': detectors.drain_notes()}
    Path(result_path).write_text(json.dumps(record))


def _run_bounded_one(name, table, expect_flat, seconds):
    """Run only in an isolated process, including from stdin or unguarded scripts.

    A fresh interpreter imports this module directly instead of reconstructing the
    launching program. Failures never trigger an in-process retry. The deadline
    includes process startup and execution; a timed-out worker is killed and reaped.
    """
    deadline = time.monotonic() + seconds
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix='anomalynerd-worker-') as work:
            request = Path(work) / 'request.pickle'
            result = Path(work) / 'result.json'
            with request.open('wb') as f:
                pickle.dump((name, table, expect_flat), f)
            env = os.environ.copy()
            source_root = str(Path(__file__).resolve().parent.parent)
            env['PYTHONPATH'] = source_root + os.pathsep + env.get('PYTHONPATH', '')
            command = [sys.executable, '-c',
                       'from anomalynerd.bounded import _subprocess_main; _subprocess_main()',
                       str(request), str(result)]
            try:
                process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    return {'status': 'timed_out', 'reason': 'detector time limit exceeded', 'flags': []}
                if process.returncode != 0:
                    return {'status': 'failed',
                            'reason': 'detector process exited with code ' + str(process.returncode), 'flags': []}
                if not result.exists():
                    return {'status': 'failed', 'reason': 'detector process exited without result', 'flags': []}
                return json.loads(result.read_text())
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
    except Exception as exc:
        return {'status': 'failed', 'reason': 'detector subprocess failure: ' + type(exc).__name__ + ': ' + str(exc), 'flags': []}


def run_bounded(table,seconds=4.0,checkpoint_dir=None,expect_flat=None):
    """Run each detector with a time limit. Reuses saved results from checkpoint_dir when the table, detector code and settings are unchanged."""
    if not 0 < seconds <= 300: raise ValueError('detector time limit must be in (0,300] seconds')
    payload={'detector_source':hashlib.sha256(inspect.getsource(detectors).encode()).hexdigest(),'runner_source':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'version':VERSION,'table':asdict(table),'expect_flat':expect_flat,'seconds':seconds}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()
    root=Path(checkpoint_dir) if checkpoint_dir else None
    if root: root.mkdir(parents=True,exist_ok=True)
    results=[];flags=[]
    for name in DETECTORS:
        path=root/(digest+'-'+name+'.json') if root else None
        record=None
        if path and path.exists():
            try:
                cached=json.loads(path.read_text())
                if cached.get('key')==digest and cached.get('detector')==name and cached.get('status')=='completed':record=cached
            except (OSError,ValueError): pass
        if record is None:
            started=time.monotonic()
            if is_pathological(table):record={'status':'skipped','reason':'too many axes or level combinations (likely mis-parsed); not analyzed','flags':[]}
            else:
                record = _run_bounded_one(name, table, expect_flat, seconds)
            record.update({'key':digest,'detector':name,'limit_seconds':seconds,'elapsed_seconds':round(time.monotonic()-started,5)})
            if path:
                fd,tmp=tempfile.mkstemp(dir=root,prefix='.pending-')
                with os.fdopen(fd,'w') as f:json.dump(record,f)
                os.replace(tmp,path)
        results.append(record)
        flags.extend(Flag(**f) for f in record.get('flags',[]))
    idx=detectors.index_like_axes(table)
    flags=_suppress_index_axis(flags,idx);flags=_suppress_jumps_adjacent_to_outliers(flags)
    return score(flags),results
