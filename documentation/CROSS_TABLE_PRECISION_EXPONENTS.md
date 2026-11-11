# Cross-table rounding precision without intermediate overflow

A finite scientific cell can have an exponent above the float range, balanced by its fractional mantissa. For example, `0.001e309` parses to finite `1e306`, but the cross-table rounding code separately computed `10**309` and raised `OverflowError`. Very long fractional mantissas could also underflow the precision factor before a positive exponent restored its scale.

Retain the mantissa decimal count and exponent as integers, combine them and convert the final half-reported-unit to float once. The rounding policy is still half the last printed unit. The legacy private `_decimals_in` helper still returns decimal-count/scale, but an unrepresentable scale now returns infinity instead of raising; comparisons use the combined parts, not that intermediate scale.

This does not change metric/coordinate matching, label interpretation, scientific anomaly rules or global priority ranking. It does not fix unrelated overflow in subtraction or reporting-step ratios for extreme opposite-signed finite values, or ambiguity in existing raw-token recognition. Float representability and float-noise guards still apply. No claim of scientific correctness is made from these arithmetic checks.

Prepared independently on main `8254824ef30e4506ced6a6c0eb0093682af56bd8`, without unmerged PR19 (CSV structure) or PR20 (magnitude suffixes).

Offline tests:
- 16 new cases fail on the main base and pass fixed: 12 direct finite-cell tolerance cases and 4 actual cross-table comparisons, including equal/within-rounding/beyond-rounding values.
- The focused new plus existing cross-table selection passes 27 tests.
- Full socket-blocked comparison: baseline 280 passes/16 new failures, fixed 296 passes, with the same 10 external-PDF skips. Those PDF paths remain unqualified.
- 10,000 ordinary-range formula comparisons agree within relative tolerance 5e-16 (not bit-for-bit identity). Existing rounding slack/noise guards are not removed.

No live source/model/scorer calls, paid runs, merge, deployment or scientific-label changes.
