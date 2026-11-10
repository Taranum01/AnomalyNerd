# CSV column integrity

Duplicate column names previously overwrote earlier values during CSV import.
Rows with more cells than the header silently discarded the extra values.
Reject both shapes before inference or detector execution. The error names
the header problem or the row's ending line instead of returning findings on
a different table from the supplied file.

Unique blank/whitespace headers, short rows, quoted commas, leading comments,
BOMs and blank rows retain their existing behavior. Header names are not
renamed or normalized. This does not fix scientific labels or PDF extraction.
The existing upload error remains HTTP 500 with a detail message; this patch
does not redesign that response contract.

```sh
python -B -m unittest discover -s tests -p test_csv_column_integrity.py -v
python -B -m pytest -q -rs
```

The 14 added offline tests give 8 failures/6 passes on main 8254824 and all
14 pass with the fix, including read_tables and the real local upload route.
Ten repeats pass 140 checks. A full socket-blocked comparison gives main
286 passes/8 new failures/10 skips, versus 294 passes/the same 10 skips with
the fix. All ten skips need external arXiv PDF fixtures unavailable with the
network blocked. They are unqualified, not fixed; this is not a clean full-PDF
result or a new scientific-validation result. No models, manuscripts, paid
runs, merge or deployment are involved.
