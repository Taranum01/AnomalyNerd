"""20 deterministic synthetic cases; no provider or manuscript inputs."""
import json
from anomalynerd.derived_metrics import check
rows=[]
for i in range(20):
    args=dict(before=str(100+i),after=str(120+i),printed_percent='0.0',decimals=1,
              inputs_exact=True,cells=['synthetic:A','synthetic:B','synthetic:C'])
    declared=check(**args,base='before',formula_source='Synthetic percent-change caption')
    ambiguous=check(**args)
    rows.append(dict(case=i+1,declared=declared,ambiguous=ambiguous))
assert all(r['declared']['status']=='REVIEW_WARNING' and r['ambiguous']['status']=='ABSTAIN' for r in rows)
print(json.dumps(rows,sort_keys=True,indent=2))
