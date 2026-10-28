# AnomalyNerd

**An automatic "dentist" for research results tables.** Give it a table of numbers and it
points at the spots that look odd and deserve a closer look — like a dentist circling a
problem on an X-ray. It **flags** what to investigate; it does **not** try to explain *why*,
declare a definitive bug, or re-run any experiments. That judgment stays with the researcher.

Part of the "Nerd" family of research tools.

---

## Quick start

```bash
pip install -r requirements.txt        # Python 3.9+ (core is pure stdlib; pytest for tests)

# analyze any CSV:
python -m anomalynerd.cli examples/halley_ozone_october.csv --metric ozone_DU --lower-better
python -m anomalynerd.cli examples/co2_annual.csv --metric mean --higher-better --ignore unc
python -m anomalynerd.cli examples/newcomb_speed_of_light.csv --metric deviation
python -m anomalynerd.cli examples/draft_lottery_1970.csv --metric mean_draft_rank --ignore month --expect-flat month_num
python -m anomalynerd.cli results.csv --json          # machine-readable output
```

### Local review page

Write a self-contained report with priority counts, filterable findings, computed evidence and an optional caption:

```bash
python -m anomalynerd.cli examples/halley_ozone_october.csv --metric ozone_DU --lower-better --caption --html report.html
```

Open `report.html` in a browser. The report uses no remote assets or model calls; table content is escaped before rendering. It keeps the detector output unchanged. It supports narrow screens and printing.

### Generate a caption for a table

Because detecting anomalies means detecting patterns, AnomalyNerd can also describe a table in
a grounded caption built only from what it computed (no made-up context). You choose how much
caption you want, and can optionally compare against a paper's real caption on the claims the
table can actually verify.

```bash
python -m anomalynerd.cli results.csv --caption medium          # short | medium | detailed
python -m anomalynerd.cli results.csv --caption-words 20        # target a word budget
python -m anomalynerd.cli results.csv --caption --compare-caption "the paper's real caption"
```

The comparison is deliberately scoped to checkable claims (trends, best method, shifts,
outliers, numbers) and ignores context the numbers cannot confirm (dataset names, "bold =
best"). Human captions vary in quality, so this is a descriptive comparison, not a score.

### Analyze a whole article (PDF, LaTeX, Word or Excel)

AnomalyNerd can read the results tables out of a paper, run the pattern detectors on
them, run statistical and digit checks (statcheck p-value recompute, GRIM mean
plausibility, Benford leading-digit), and — importantly — tell you exactly what it
**could not** check.

```bash
python -m anomalynerd.pdf_cli paper.pdf            # a compiled PDF (incl. appendices)
python -m anomalynerd.pdf_cli paper.tex            # LaTeX source (far cleaner extraction)
python -m anomalynerd.pdf_cli paper.docx           # a Word document: every table, plus statistics in the text
python -m anomalynerd.pdf_cli results.xlsx         # an Excel workbook: each sheet is read as a table
python -m anomalynerd.pdf_cli paper.pdf --json
```

Prefer the LaTeX source when you have it: a compiled PDF often stores tables as
borderless layouts or images that cannot be extracted cleanly, whereas LaTeX ``tabular``
cells are already delimited. On a sample of random arXiv papers, source ingest read
usable tables in the majority of papers where PDF extraction read none. Either way, the
report ends with a "WHAT I COULD NOT CHECK (and why)" section — nothing is silently
skipped.

Word and Excel files store tables as real cells, so they are read exactly, with no layout
guessing. They still pass the same checks as PDF tables: a table that is too small or has no
numeric column is listed as not analyzed rather than dropped.

For PDFs without available source, an optional layout-model fallback (Docling) reads many
borderless tables that the default extractor misses. It runs when the fast extractor finds
nothing, and also when a results table has been merged with the surrounding text, in which
case the layout model separates the table from the text and the clean version is analyzed. It
is an optional dependency (`pip install docling`); without it the pipeline still runs and says
so. On a paired sample, PDF table coverage rose from 0%
(pdfplumber only) to 43% with the Docling fallback, versus 57% from LaTeX source.

Run the tests:
```bash
pytest -q    # 155 tests across 11 test files, under a minute
```

---

## What it looks for (8 detectors)

| # | Anomaly | Plain meaning |
|---|---------|---------------|
| T1 | **Numeric jump** | the result explodes between two nearby input values (trend-aware) |
| T2 | **Monotonicity** | a value that should rise (or fall) does the opposite |
| T3 | **Win-reversal** | the "best" method keeps flipping across a setting |
| T4 | **Categorical exception** | "A always beats B, except in one case" |
| T5 | **Duplicate / flatness** | the same value where it shouldn't be, or a setting with no effect |
| T6 | **Level shift** | a stable series quietly moves to a new level and stays there |
| T7 | **Point outlier** | a lone point far from the rest (residual, leverage, or a spike on a flat series) |
| T8 | **Trend** | the whole series slopes; a real flag only if you say the axis should be flat/random |

Every flag is ranked **HIGH / MEDIUM / LOW** so you triage the important ones first. The tool
also recognizes an **index/ID axis** (a plain row counter) and skips meaningless "jump" flags there.

---

## How it works

Any input (CSV) → converted to one **tidy long-form table** → schema inferred (which columns
are inputs vs. the metric) → the 8 detectors run → results ranked and reported. Because every
input becomes the same tidy shape, detectors never care about the source format.

---

## Validated on real datasets (with documented ground truth)

| Dataset | What it catches |
|---|---|
| Antarctic ozone (Halley, 1956–2011) | the ozone-hole level shift (the drop once dismissed as errors) |
| Mauna Loa CO₂ | *nothing* — correctly stays quiet on a normal upward trend |
| Anscombe's quartet | the known outliers in datasets III and IV; clean sets stay clean |
| Newcomb speed-of-light (1882) | both historically-documented outliers |
| 1970 US draft lottery | the late-year bias (a trend where the axis should be random) |
| Thyroid (summarized) | the feature bins where anomalies concentrate |

Big datasets are handled by first **summarizing** them into a results table (rates/means per
bin or group) — AnomalyNerd is a results-table scanner, not a raw big-data ML classifier.

---

### Local web page

A small web page lets you upload a file and browse the flagged anomalies, filtered by priority.
It accepts CSV, Excel, PDF, LaTeX and Word files.

```bash
pip install -r web/requirements.txt
python web/app.py            # then open http://127.0.0.1:8000
```

It runs on your own machine only: it listens on 127.0.0.1, so other computers cannot reach it,
and it has no login. Do not expose it to a network as it is.

## Layout

```
anomalynerd/            the library
  ingest.py             reads CSV tables into one tidy shape
  detectors.py          the eight pattern detectors (T1 to T8)
  analyze.py, cli.py    run the detectors on a CSV and print the report
  caption.py            writes a short caption from what was computed
  caption_compare.py    compares it with a paper's own caption
  pdf_ingest.py         finds results tables in a PDF
  latex_ingest.py       finds results tables in LaTeX source
  office_ingest.py      reads tables from Word (.docx) and Excel (.xlsx) files
  stat_tests.py         statcheck, GRIM and Benford checks
  bounded.py            runs each detector with a time limit
  html_report.py        writes the local review page
web/                    local upload page (FastAPI), runs on 127.0.0.1 only
tests/                  regression tests (synthetic data and six real datasets)
examples/               ready-to-run CSVs, with sources in DATA_SOURCES.md
FINDINGS.md             what it found on each dataset
DESIGN.md               the full design notes (detectors, edge cases, validation, roadmap)
```

---

## Honest limits
- It **points, it doesn't diagnose** — it tells you *where* to look, not *why*.
- Telling a genuine regime-shift from an accelerating trend is hard; such cases are reported
  as **low-confidence candidates**, not certainties.
- Input can be CSV, Excel, PDF, LaTeX or Word. PDF is the least reliable, because tables have to be
  reconstructed from the page layout; prefer the LaTeX source, Word or Excel when you have them.

### Bounded article review (local)

```bash
python -m anomalynerd.pdf_cli article.pdf --detector-seconds 4 --checkpoint-dir .anomaly-checkpoints --json
```

What this does, in plain steps:

1. Each of the eight detectors runs in its own process and has a hard time limit.
2. Finished results are saved in the checkpoint folder. If you run the same table again with the same settings, they are reused.
3. A detector that fails or runs out of time counts as not finished. It is tried again next time. It is never shown as a pass.
4. The time limit covers the detectors only. PDF reading and the statistical checks are not limited, so use your own process time limit if you need one.
5. The JSON output keeps the raw headers and cells, and the page or file where each table was found.

LaTeX files: `\input` and `\include` are followed only inside the folder of the main file, with limits on depth, loops and size. Line numbers in the expanded text are not the original line numbers of each file. A missing include is listed as something that could not be checked. Complex macros and unusual table layouts can still be skipped.

How to read the results:

- "Coverage" counts the table regions found, not the true number of tables in the paper. Some regions can be plain text, or a second view of the same table. Skipped regions stay visible even when another region on the same page worked.
- Original headers are kept. If a header cannot be identified, that table is marked as an extraction failure. No names are invented.
- The layout fallback (Docling) is optional and is never installed for you.
- Every flag is a suggestion to look closer. The same baseline or run time appearing in several datasets can be intentional. Copying a printed number correctly does not prove a research mistake.
- `not_applicable` for statcheck, GRIM or GRIMMER means the supported inputs were not found. It does not mean the paper's statistics were checked and are fine. A clean pass is never claimed.

The HTML report stays on your machine. Editing changes the displayed copy, not the CSV or detector output. Saved edits show an edited-report notice; downloaded edited copies carry the same warning. Re-run the CSV to restore the computed report.

---

## Contributing

1. Install the requirements and run the tests above. All should pass before you open a pull request.
2. Keep each pull request to one change, and add a test for it.
3. Do not commit generated reports or checkpoint folders.
