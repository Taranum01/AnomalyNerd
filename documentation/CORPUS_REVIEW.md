# Resumable local article review

Review an explicit list of up to 1,000 PDF, LaTeX, Word or Excel files:

```sh
python -m anomalynerd.corpus_cli paper1.pdf paper2.xlsx \
  --output-dir .anomaly-corpus --seconds 120 --detector-seconds 4
```

The existing article CLI handles each file unchanged in a separate sequential
process. `--seconds` covers the whole article process, including parsing and
statistical checks. On POSIX, deadline expiry kills the process group, including
its detector children. On Windows, only the parent is killed: do not treat this
as a descendant-process resource bound there. This is a local batch tool, not a
service, download queue, scheduler or live benchmark.

## Results and resume

- `manifest.json` records requested/reviewed counts and an entry for each finished
  file. It is replaced atomically after each file. An interrupted batch can be
  rerun with the same command. A file not yet reached has no success claim.
- Each completed file has a separate full, unmodified article JSON report under
  a hash-derived filename. Its original input path, source rows, findings,
  extraction gaps and detector statuses stay intact. There is no cross-paper
  comparison or pooled anomaly rate.
- Resume requires the same absolute source path, file SHA256, settings, runner
  schema, Python version, package-source fingerprint and selected parser/runtime
  package versions. The saved report checksum and required structure are checked.
  Missing/corrupt results are recomputed. This is accidental-corruption detection,
  not a signed or tamper-proof record.
- Failed processes, whole-file timeouts, invalid JSON and reports with failed or
  timed-out detectors are retried. A completed process with skipped detectors or
  unavailable statistical inputs can be reused, but remains explicitly unchecked.
  `completed` means process/report completion, not a clean paper or full coverage.
- LaTeX roots always rerun. Root bytes alone cannot track included-file changes.
  Existing include restrictions stay in the article parser.
- `--no-resume` forces a fresh review and invalidates a prior successful checkpoint
  before rerunning. Historical report files can remain on disk but are not a new
  success. Outputs are private local files; keep one writer per output directory.

Exit status is 0 when every process produced a valid report, 1 when any file
failed or timed out, and 2 for invalid invocation/input. A zero exit status is not
scientific validation: use per-file coverage and the full reports. No finding is
upgraded or priority rule changed. The runner does not improve table extraction.

## Limits

This does not bound input/output byte size, total disk use, process memory or the
entire batch duration. The corpus manifest is local and contains absolute paths;
it is not automatically shared. No glob discovery, network fetch, model call,
optional dependency installation or model-weight download is added. Files and
existing parser dependencies must already be available. A manifest does not
establish the true number of tables in a paper, recover missing layouts or prove
anomaly accuracy. Existing optional-Docling PDF failures remain.

## Tests

`python -m pytest -q tests/test_corpus_review.py`

Includes real CLI subprocess review, a timeout that kills a spawned descendant
on POSIX, atomic checkpoint/report integrity, source/settings/engine changes,
malformed outputs, continuation after failures, partial coverage, per-file raw
JSON preservation, forced retries and LaTeX no-resume behavior. Controlled runner
fixtures do not qualify every parser; two cached public PDF slices and two local
LaTeX fixtures are additional real-process checks, not a corpus-quality benchmark.
