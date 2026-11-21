"""Opt-in declared checks, kept separate from statistical flags."""
import json
from pathlib import Path
from .derived_metrics import check

FIELDS={'before','after','printed_percent','decimals','base','formula_source','cells','inputs_exact'}

def load_checks(path):
    if Path(path).stat().st_size > 1_000_000:
        raise ValueError('derived schema exceeds 1MB')
    try:
        data=json.loads(Path(path).read_text())
    except RecursionError as exc:
        raise ValueError('derived schema nesting exceeds decoder limit') from exc
    if not isinstance(data,list) or len(data)>100:
        raise ValueError('derived schema must be a list of at most 100 checks')
    results=[]
    for i,item in enumerate(data):
        if not isinstance(item,dict) or set(item)-FIELDS or not {'before','after','printed_percent','decimals'} <= set(item):
            raise ValueError('invalid fields in derived check '+str(i+1))
        results.append({'id':i+1,**check(**item)})
    return results

def format_checks(results,details=False):
    counts={s:sum(r['status']==s for r in results) for s in ('REVIEW_WARNING','CONSISTENT','ABSTAIN')}
    lines=['Derived arithmetic (opt-in, separate from outlier flags): '
           f"{counts['REVIEW_WARNING']} review warning(s), {counts['CONSISTENT']} consistent, {counts['ABSTAIN']} abstained.",
           'Caller-supplied schema; provenance and CSV correspondence require review. No values changed.']
    for r in results:
        if r['status']=='REVIEW_WARNING':
            lines.append(f"  Check {r['id']}: declared percentage-change mismatch. Not an experimental-error finding.")
    if not details:
        lines.append('Use --derived-details to inspect formula, source cells and rounding evidence.')
    else:
        lines.append('Evidence (including abstentions):\n'+json.dumps(results,indent=2,ensure_ascii=True))
    return '\n'.join(lines)
