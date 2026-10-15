"""Per-detector process limits with content-addressed resumable checkpoints."""
import inspect
import hashlib,json,multiprocessing as mp,os,tempfile,time
from dataclasses import asdict
from pathlib import Path
from . import detectors
from .model import Flag
from .analyze import score,is_pathological,_suppress_index_axis,_suppress_jumps_adjacent_to_outliers

DETECTORS = ('detect_numeric_jumps','detect_win_reversals','detect_monotonicity',
             'detect_cat_exceptions','detect_duplicates_and_flatness','detect_level_shifts',
             'detect_point_outliers','detect_trends')
VERSION = 'bounded-v1'

def _worker(conn,name,table,expect_flat):
    try:
        kwargs = {'expect_flat':expect_flat} if name in ('detect_level_shifts','detect_trends') else {}
        if name == 'detect_trends': kwargs['index_axes'] = detectors.index_like_axes(table)
        flags = getattr(detectors,name)(table,**kwargs)
        conn.send({'status':'completed','flags':[asdict(f) for f in flags]})
    except Exception as exc:
        conn.send({'status':'failed','reason':type(exc).__name__+': '+str(exc),'flags':[]})
    finally: conn.close()

def run_bounded(table,seconds=4.0,checkpoint_dir=None,expect_flat=None):
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
                # A process, not a thread, makes the hard timeout enforceable.
                context=mp.get_context('spawn');parent,child=context.Pipe(duplex=False)
                process=context.Process(target=_worker,args=(child,name,table,expect_flat));process.start();child.close()
                try:
                    record=parent.recv() if parent.poll(seconds) else {'status':'timed_out','reason':'detector time limit exceeded','flags':[]}
                except EOFError:record={'status':'failed','reason':'detector process exited without result','flags':[]}
                finally:
                    if process.is_alive():process.terminate()
                    process.join(timeout=2)
                    if process.is_alive():process.kill();process.join()
                    parent.close()
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
