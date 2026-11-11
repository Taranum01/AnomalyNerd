# Unrecovered stacked PDF table status

A pdfplumber candidate can hold several vertically stacked tables. Existing caption detection already requests optional layout recovery. If recovery is unavailable, fails, recovers only part, or returns tables from another page, the stacked candidate stays so its numbers are not silently dropped.

Previously a retained candidate could still say `ok` / `parsed cleanly`, with no table-level caveat. Document-level backend notes and detector skip reasons could disclose the limits, but the table status contradicted the known structural uncertainty.

The change marks retained stacked candidates `partial` and adds a table-level reason/caveat that the regions were not fully separated and may mix distinct tables. It preserves rows, headers, tidy data, numeric checks, and recovery routing. A fully replaced stacked candidate is not marked; clean candidates are unchanged. Existing partial reasons are preserved with the warning appended.

## Local evidence

- Exact baseline: `8254824ef30e4506ced6a6c0eb0093682af56bd8`.
- 16 new cases: baseline 12 failed / 4 passed; fixed 16 passed. Cases cover absent, partial and wrong-page recovery, multiple backend-note outcomes, fully recovered stacks and clean candidates. Retained rows, headers and tidy data must remain equal.
- New cases plus existing PDF pipeline tests: 61 passed.
- Offline full tests with cached public PDF slices: baseline 286 passed / 20 failed; fixed 298 passed / 8 failed. Both collect 306 cases. The same 8 existing layout-dependent cases fail in this environment without Docling. This is not a full green qualification.

Five exact public paper versions were downloaded without redirects, and the configured one-page slices were tested:

| Source | Physical PDF page | Fixed result versus baseline |
|---|---:|---|
| https://arxiv.org/pdf/1907.11692v1 | 13 | retained 35x14 candidate changes from `ok` to `partial`, adds structural warning; flags unchanged |
| https://arxiv.org/pdf/2106.09685v2 | 23 | retained 47x16 candidate changes from `ok` to `partial`, adds structural warning; flags unchanged |
| https://arxiv.org/pdf/2210.11416v5 | 46 | skipped candidate unchanged; flags unchanged |
| https://arxiv.org/pdf/2304.01373v2 | 21 | partial candidate unchanged; three flag records unchanged |
| https://arxiv.org/pdf/1911.02116v2 | 12 | skipped candidate unchanged; flags unchanged |

The four failing source-page renders were inspected. RoBERTa contains three distinct tables; LoRA contains Tables 15 and 16; Flan contains Tables 22 and 23; XLMR contains one wide Table 7. This establishes extraction/status uncertainty, not scientific error in the papers.

No Docling/torch installation, model-weight download, live inference, deployment, paid API call or priority-rule change was used. This change does not recover the missing tables or make the 8 existing layout-dependent assertions pass.
