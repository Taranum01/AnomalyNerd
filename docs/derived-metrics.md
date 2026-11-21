# Declared percentage-change checks

Opt-in Python helper, not automatic detection. It checks only `(after-before)/base*100` where the declared base is `before` or `after`. It does not infer formulas, extract tables, run in the detector pipeline or edit values.

```python
from anomalynerd.derived_metrics import check
result = check(before="100", after="112", printed_percent="9.0", decimals=1,
    base="before", inputs_exact=True,
    formula_source="Synthetic caption: percent=(after-before)/before*100",
    cells=["synthetic:A1", "synthetic:B1", "synthetic:C1"])
assert result["status"] == "REVIEW_WARNING"
```

Supply formula/base provenance and the before, after and percentage cell identifiers. These strings are recorded, not authenticated or interpreted. Callers must verify the formula and extraction; a guessed base is not sufficient. Use decimal strings for exact inputs. `inputs_exact=True` is a caller assertion, not a completeness guarantee. Leave it false for rounded or uncertain inputs.

`CONSISTENT` means within half a unit of printed precision, inclusive at ties to avoid assuming a rounding convention. `REVIEW_WARNING` means this declared relationship fails that tolerance, not that the experiment is wrong. `ABSTAIN` identifies missing provenance, unknown precision/base, nonpositive base, malformed/nonfinite/out-of-range numbers or incompatible printed precision. Evidence records the cells, formula source, base, printed result and, where available, expected percentage/tolerance.

No candidate-denominator search, statistical significance, automatic replacement, general formulas or scientific validation. This is separate from statistical outlier detection. Synthetic tests establish mechanics, not real-world false-positive rates.
