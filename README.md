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

### Analyze a whole article (PDF or LaTeX source)

AnomalyNerd can read the results tables out of a paper, run the pattern detectors on
them, run statistical and digit checks (statcheck p-value recompute, GRIM mean
plausibility, Benford leading-digit), and — importantly — tell you exactly what it
**could not** check.

```bash
python -m anomalynerd.pdf_cli paper.pdf            # a compiled PDF (incl. appendices)
python -m anomalynerd.pdf_cli paper.tex            # LaTeX source (far cleaner extraction)
python -m anomalynerd.pdf_cli paper.pdf --json
```

Prefer the LaTeX source when you have it: a compiled PDF often stores tables as
borderless layouts or images that cannot be extracted cleanly, whereas LaTeX ``tabular``
cells are already delimited. On a sample of random arXiv papers, source ingest read
usable tables in the majority of papers where PDF extraction read none. Either way, the
report ends with a "WHAT I COULD NOT CHECK (and why)" section — nothing is silently
skipped.

Run the tests:
```bash
pytest -q          # 25 checks: CSV detectors + PDF/LaTeX pipeline + stat tests
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

## Layout

```
anomalynerd/        the library (model, ingest, detectors T1–T8, analyze, cli)
tests/              pytest regression suite (synthetic + 6 real datasets)
examples/           ready-to-run CSVs + DATA_SOURCES.md (provenance)
FINDINGS.md         what it found on each dataset
DESIGN.md           full design doc (detectors, edge cases, validation, roadmap)
```

---

## Honest limits
- It **points, it doesn't diagnose** — it tells you *where* to look, not *why*.
- Telling a genuine regime-shift from an accelerating trend is hard; such cases are reported
  as **low-confidence candidates**, not certainties.
- Input is CSV today; LaTeX-table and Excel readers are planned.
