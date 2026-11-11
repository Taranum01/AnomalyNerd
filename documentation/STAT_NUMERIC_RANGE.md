# GRIM and GRIMMER numeric-range limits

A supported `M=..., SD=..., N=...` string can still exceed the arithmetic used by these checks. A finite 308-digit mean times the sample size becomes infinity before GRIM rounds it. A finite 155-digit SD overflows when GRIMMER squares it. The parser also accepts decimal strings that convert directly to float infinity. Previously these errors escaped the statistical checks and aborted `analyze_source`, including unrelated valid results.

The narrowly scoped change handles `OverflowError` and `ValueError` from GRIM's nearest-mean arithmetic and GRIMMER's mean gate / squared-deviation arithmetic. It returns an explicit `not_applicable` record with the original reported string and a numeric-range reason. It does not call the reported value a scientific anomaly. The rest of the report and other supported triples continue.

## Evidence

Exact baseline: `8254824ef30e4506ced6a6c0eb0093682af56bd8`. This is independent of unmerged PR19-22.

- 20 new tests: baseline16fail/4pass; fixed20pass.
- 47 new and existing statistical-extraction cases pass.
- Eight end-to-end LaTeX cases cover positive/negative unrepresentable means, finite mean-product overflow, finite mean-square overflow, unrepresentable SD, finite SD-square and squared-sum overflow. They check that the original string gets a numeric-range warning in `could_not_check`, and ordinary GRIM and z-test results remain present.
- Full cached-PDF offline suite: fixed302pass/8fail versus baseline286pass/24fail,310collected on each. The same8existing layout-dependent tests fail without optional Docling. This is not a full-green or PDF-extraction qualification.
- 10,000 deterministic ordinary-range reported triples produce exactly identical complete GRIM/GRIMMER result records on baseline and fixed code (combined SHA256 `112346872727e36372c4a84069715a344083e2cbf8288cea14ed9be7cb72d38e`). Existing numeric tolerances and scientific rules are unchanged.

## Limits

This is not arbitrary-precision validation. It does not broaden the sample-size eligibility rules, change significance/priority thresholds, or verify the scientific context required for integer-item measures. It does not guard every source parser, enormous integer-string conversion, statistic or report path. In particular an extremely long N string's integer conversion remains outside this change. It does not assert all large finite inputs are accurately checked; it prevents the reproduced unrepresentable arithmetic from aborting the report.

No live model/scorer/source request, weight download, optional model-stack install, paid run, merge or deployment was used for these tests. Public PDF slices were already cached for the separate extraction audit.
