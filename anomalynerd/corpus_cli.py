"""Local, sequential article review with whole-file timeouts and resumable results.

Each file still runs the existing article CLI unchanged in a child process.
No discovery, downloading, models or cross-paper comparisons are added.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile

SCHEMA = 1
SUPPORTED = {'.pdf', '.tex', '.docx', '.xlsx'}


def _atomic_json(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.corpus-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _fingerprint(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _engine_fingerprint():
    h = hashlib.sha256()
    for source in sorted(Path(__file__).parent.glob('*.py')):
        h.update(source.name.encode())
        h.update(source.read_bytes())
    # Optional parser availability/version can change extraction results.
    for name in ('numpy', 'scipy', 'pandas', 'pdfplumber', 'pypdfium2', 'docling', 'openpyxl', 'python-docx'):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = 'not-installed'
        h.update((name + ':' + version).encode())
    h.update(sys.version.encode())
    return h.hexdigest()


def _run_article(command, **options):
    # A detector can spawn its own child. On POSIX, kill the entire process
    # group at the whole-file deadline, not just the article parent.
    timeout = options['timeout']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=(os.name == 'posix'))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except BaseException:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate()
        raise


def _valid_report(value, source):
    """A result must carry the article CLI's coverage and finding fields."""
    if not isinstance(value, dict) or value.get('path') != str(source):
        return False
    integers = ['pages', 'tables_detected', 'tables_extraction_usable', 'metric_slices_completed']
    if any(type(value.get(k)) is not int or value[k] < 0 for k in integers):
        return False
    if value['tables_extraction_usable'] > value['tables_detected']:
        return False
    lists = ['extraction', 'pattern_flags', 'stat_findings', 'could_not_check', 'takeaways']
    if any(not isinstance(value.get(k), list) for k in lists):
        return False
    if any(not isinstance(t, dict) or t.get('status') not in ('ok', 'partial', 'skipped', 'failed', 'error') for t in value['extraction']):
        return False
    groups = value.get('detector_runs')
    if not isinstance(groups, dict) or any(not isinstance(group, list) for group in groups.values()):
        return False
    if any(not isinstance(run, dict) or run.get('status') not in ('completed', 'failed', 'timeout', 'error', 'skipped') for group in groups.values() for run in group):
        return False
    if any(not isinstance(finding, dict) or not isinstance(finding.get('status'), str) for finding in value['stat_findings']):
        return False
    return isinstance(value.get('coverage_unit'), str)


def _coverage(report):
    extraction_partial = any(not isinstance(t, dict) or t.get('status') != 'ok' for t in report['extraction'])
    runs = [r for group in report['detector_runs'].values() if isinstance(group, list) for r in group]
    unfinished = any(not isinstance(r, dict) or r.get('status') != 'completed' for r in runs)
    stats_unchecked = any(not isinstance(s, dict) or s.get('status') in ('not_applicable', 'error', 'timeout') for s in report['stat_findings'])
    return {
        'tables_detected': report['tables_detected'],
        'tables_extraction_usable': report['tables_extraction_usable'],
        'metric_slices_completed': report['metric_slices_completed'],
        'pattern_flags': len(report['pattern_flags']),
        'stat_findings': len(report['stat_findings']),
        'retryable_detectors': any(isinstance(r, dict) and r.get('status') in ('failed', 'timeout', 'error') for r in runs),
        'has_unchecked_work': bool(report['could_not_check'] or extraction_partial or unfinished or stats_unchecked or not report['tables_detected']),
        'interpretation': 'Process completion is not complete extraction, statistical validation or proof of research errors.',
    }


def review_files(paths, output_dir, *, seconds=120.0, detector_seconds=4.0, resume=True, run=None):
    """Return a manifest; failed/timed-out/corrupt results are retried next run.

    Explicit files only. Output paths derive from hashes, never source names.
    Resume is scoped to source bytes, absolute path, settings and this runner's
    schema. Included LaTeX projects are rerun because child-file dependencies
    cannot be established from the root-file hash alone.
    """
    if not all(math.isfinite(v) and v > 0 for v in (seconds, detector_seconds)):
        raise ValueError('Timeouts must be finite and greater than zero')
    sources = [Path(p).expanduser().resolve() for p in paths]
    if not sources or len(sources) > 1000:
        raise ValueError('Supply 1-1000 explicit source files')
    if len(set(sources)) != len(sources):
        raise ValueError('Duplicate source paths are not allowed')
    for source in sources:
        if not source.is_file() or source.suffix.lower() not in SUPPORTED:
            raise ValueError(f'Not a supported source file: {source}')
    output = Path(output_dir).expanduser().resolve()
    if output in sources:
        raise ValueError('Output directory cannot be an input file')
    output.mkdir(parents=True, exist_ok=True)
    runner = _run_article if run is None else run
    settings = {'seconds': seconds, 'detector_seconds': detector_seconds, 'engine_sha256': _engine_fingerprint(), 'fused_recovery_env': os.environ.get('ANOMALYNERD_FUSED_RECOVERY', '').strip().lower() in ('1', 'true', 'yes')}
    records = []
    for source in sources:
        digest = _fingerprint(source)
        identity = {'schema': SCHEMA, 'path': str(source), 'sha256': digest, 'settings': settings}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        report_path = output / (key + '.json')
        checkpoint = output / (key + '.checkpoint.json')
        record = dict(identity, report_file=report_path.name, status='failed', resumed=False)
        if resume and source.suffix.lower() != '.tex':
            try:
                saved = json.loads(checkpoint.read_text(encoding='utf-8'))
                raw = report_path.read_bytes()
                report = json.loads(raw)
                if saved.get('identity') == identity and saved.get('report_sha256') == hashlib.sha256(raw).hexdigest() and _valid_report(report, source) and not _coverage(report)['retryable_detectors']:
                    record.update(status='completed', resumed=True, coverage=_coverage(report))
                    records.append(record)
                    continue
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        # Invalidate an earlier success before a requested rerun.
        checkpoint.unlink(missing_ok=True)
        command = [sys.executable, '-m', 'anomalynerd.pdf_cli', str(source), '--json', '--detector-seconds', str(detector_seconds)]
        try:
            result = runner(command, capture_output=True, text=True, timeout=seconds, check=False)
            if result.returncode != 0:
                record.update(error='Article process failed', returncode=result.returncode)
            else:
                report = json.loads(result.stdout)
                if not _valid_report(report, source):
                    raise ValueError('Incomplete or malformed article report')
                if _fingerprint(source) != digest:
                    raise ValueError('Input changed during review')
                _atomic_json(report_path, report)
                if not _coverage(report)['retryable_detectors']:
                    _atomic_json(checkpoint, {'identity': identity, 'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest()})
                record.update(status='completed', coverage=_coverage(report))
        except subprocess.TimeoutExpired:
            record.update(status='timeout', error='Whole-file deadline exceeded; no successful checkpoint saved')
        except (OSError, ValueError, TypeError) as error:
            record.update(error=f'{type(error).__name__}: {error}')
        records.append(record)
        # Persist progress after each file so interruption preserves finished work.
        _atomic_json(output / 'manifest.json', _manifest(records, len(sources), settings))
    manifest = _manifest(records, len(sources), settings)
    _atomic_json(output / 'manifest.json', manifest)
    return manifest


def _manifest(records, total, settings):
    return {'schema': SCHEMA, 'settings': settings, 'requested_files': total, 'reviewed_files': len(records),
            'completed': sum(r['status'] == 'completed' for r in records),
            'failed': sum(r['status'] == 'failed' for r in records),
            'timed_out': sum(r['status'] == 'timeout' for r in records),
            'scope': 'Detected extraction regions only. Findings are unverified review suggestions. No cross-paper aggregation.',
            'files': records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--seconds', type=float, default=120.0)
    parser.add_argument('--detector-seconds', type=float, default=4.0)
    parser.add_argument('--no-resume', action='store_true')
    args = parser.parse_args(argv)
    try:
        manifest = review_files(args.files, args.output_dir, seconds=args.seconds, detector_seconds=args.detector_seconds, resume=not args.no_resume)
    except (OSError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, allow_nan=False))
    return 1 if manifest['failed'] or manifest['timed_out'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
