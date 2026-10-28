# AnomalyNerd — Design Document

**A general "dentist for research results tables."**
Feed it any results table; it flags spots that deserve a second look. It **points, it does not diagnose**, and it **never re-runs experiments**. It surfaces *where* something looks off and *what to check next* — the human decides *why*.

Author: Taranum (with the project advisor)  ·  First test case: a research paper's results  ·  Also validated on the Halley ozone-hole series

---

## 1. What the tool is (and is not)

| The tool DOES | The tool does NOT |
|---|---|
| Read a finished results table | Re-run experiments or generate new data points |
| Flag unusual / inconsistent numbers | Definitively declare "this is a bug" |
| Suggest what to check next (e.g. "try p₁=0.13, 0.14") | Explain *why* the anomaly happens |
| Rank flags by how unusual they are | Require the underlying model/code |
| Work on numeric AND ordered-categorical axes | Assume one fixed input format |

Guiding metaphor (the advisor's): a dentist looks at an X-ray and says "that spot needs a closer look" — it doesn't do the root canal.

---

## 2. The anomaly types (derived from the advisor's notes + a research paper's results)

We detect eight families. T1–T5 came from the advisor's notes + a research paper's results; **T6 was added after
testing on the real ozone-hole data revealed a gap** (see §8).

**T1. Numeric smoothness violation (sudden jump).**
Along an *ordered numeric* input axis, the output changes far more between two adjacent steps than the surrounding trend would predict.
- *a research paper's results example:* a research paper's results RMSE 1.6 → 196 when p₁ goes one setting to the next (~120×).
- *Output:* "Large jump between p₁=0.12 and 0.15; suggest testing p₁=0.13, 0.14."
- *Rule:* step compared to typical step (median/MAD) and/or ratio ≥ R.

**T2. Monotonicity violation (should-rise-but-falls).**
Along an axis where the metric is expected to move one way, it reverses.
- *a research paper's results example:* error 180 → 408 → 162 → 96 as horizon grows (should rise, it falls).
- Direction of "expected" comes from user hint or is reported as "non-monotonic, direction unknown."

**T3. Win-reversal (best entity flips back and forth).**
Across an ordered axis, the argmin/argmax (best method) changes non-monotonically (A→B→A, or 3+ different winners).
- *a research paper's results example:* winner a research paper's results (p₁=0.12) → ARIMA (0.15) → MethodB (0.20, 0.25).
- Guard: ε-tolerance for near-ties, so noise doesn't create fake reversals.

**T4. Categorical-order exception (holds always, except once).**
For a *conceptually ordered* categorical axis (low<med<high; both-known > one-known > none-known), one category is almost always ≥ another — except in k cases. Flag the exceptions.
- *the advisor's original example:* "name A always has higher B than C, except once."

**T5. Duplicate + flatness (kept as TWO distinct signals — a hard-won lesson).**
- **Duplicate (copy-paste):** the same specific value appears for the *same entity* at two levels of an axis along which that entity *otherwise varies*. *a research paper's results example:* a research paper's results = a repeated value at BOTH the 5-day and 10-day horizons.
- **Flatness (ignored axis):** an entity's value is *identical across every level* of an axis that is supposed to matter → "is this axis being ignored?" *a research paper's results example:* a research paper's results identical across all 4 knowledge settings in the day-1 sweep.
- **Lesson from testing:** a naive "same value = bug" rule drowns in *legitimate* repeats (MethodB is naturally constant across the knowledge axis because knowledge doesn't affect it). The duplicate rule MUST be coordinate-aware and exclude axes an entity legitimately ignores (those belong to flatness, not copy-paste). This was the single biggest false-positive source in testing.

**T6. Level shift / baseline departure (sustained regime change).**  *(Added after ozone testing.)*
A long-stable series (baseline mean ± spread) drifts to a new level and *stays* there. This is **not** a single sharp step (T1) nor a simple reversal (T2/T3) — every individual step looks "typical," so only a baseline comparison reveals it.
- *Rule:* the first ~30% (≥5 points) of an ordered numeric series defines the baseline (mean, SD); later points that sit > z·SD away for ≥ *persist* consecutive steps are flagged.
- *Ozone example:* October ozone holds ~297 DU (1957–74), then shifts to ~167 DU from 1980 onward — an 8-SD sustained departure. **This is the flag that, in the 1980s, would have said "don't discard these readings — investigate."**
- *Why it matters:* the ozone hole was famously missed because software auto-discarded the low values as errors; a jump/threshold detector alone would miss a slow drift. T6 is the "slow anomaly" complement to T1's "fast anomaly."
- **Trend guard (lesson from CO₂ testing):** a naive baseline-vs-later comparison FALSE-flags any steadily rising series (e.g. Mauna Loa CO₂). Fix: fit the baseline and use its **R²** to decide flat vs trend — a flat-but-noisy baseline (ozone, low R²) that breaks is a HIGH-confidence shift; a genuine trend (CO₂, R²≈0.99) that merely continues/curves is at most a LOW-confidence candidate, never a HIGH alarm.

**T7. Single-point outlier (one value against the majority).**  *(Added after Anscombe testing.)*
A lone point sits far from its peers while the rest agree. Fit a robust line along a numeric axis (or take the median if the axis is ~constant), then flag a point whose residual is a large robust outlier (> ~3.5 MAD-SDs) *when only a few points (≤2) are outliers* — a genuine minority.
- *Anscombe example:* dataset III's point (13, 12.74) is flagged; datasets I/II (clean) are not.
- This is the advisor's original "name A always has higher B than C, except in one case," for numeric data.
- *Leverage mode (added after Anscombe IV):* when an axis is nearly constant except one point, that lone point has extreme leverage and is flagged directly — so Anscombe IV is now caught too.
- *Endpoint guard:* on a strong smooth trend, the newest point is not flagged as an outlier just for being the highest (avoids a false positive on the latest CO₂ year).
- *Dominant-cluster mode (added after the thyroid test):* when most values sit in one tight cluster and 1–2 sit far outside it, those few are flagged — catches a lone spike on an otherwise flat/near-constant series (e.g. an anomaly-rate bin of 0,0,…,0,0.25), which MAD-based scaling and a line fit would both miss.

**T8. Trend (the whole series slopes).**  *(Added after the 1970 draft-lottery test.)*
A statistically strong monotonic trend along an ordered axis. Honest subtlety: a trend is only
an *anomaly* if the axis is expected to be flat/random — for a genuine input→output relation
(Anscombe x→y) or a naturally trending series (CO₂ over time) a trend is normal. The tool cannot
know which, so a trend is a **LOW-confidence candidate by default** ("if X should be flat/random,
investigate"); if the caller marks the axis via `expect_flat`, a strong trend there becomes a real
HIGH/MEDIUM finding. This is the natural 1970-draft-lottery signal (ranks decline across the year
when a fair lottery should be flat) and it keeps CO₂/Anscombe from raising false alarms.

**Index/ID-axis handling.** A numeric axis that is just a row counter (values 1…N, one row each,
like Newcomb's "measurement") carries no meaningful spacing, so per-step jumps and level-shifts are
suppressed on it — only point-outliers (and trends, i.e. "drift with collection order") are kept.
Jumps whose step lands on an already-flagged outlier are also dropped to avoid double-reporting.

---

## 3. Architecture (pipeline, format-agnostic)

```
 ┌──────────┐   ┌───────────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐
 │ INGEST   │──▶│ NORMALIZE to  │──▶│ SCHEMA infer │──▶│ DETECTORS  │──▶│ RANK &   │
 │ (any fmt)│   │ tidy long-form│   │ axes/metric  │   │ T1..T5     │   │ REPORT   │
 └──────────┘   └───────────────┘   └──────────────┘   └────────────┘   └──────────┘
```

### 3.1 INGEST — accept any format
Pluggable readers, all producing the same internal frame:
- **CSV / TSV** — baseline, cleanest. (Start here.)
- **Excel (.xlsx)** — via pandas/openpyxl; handle multiple sheets, merged cells.
- **LaTeX** (`\begin{tabular}`) — reuse our a research paper's results parser; handle `\multirow`, `\multicolumn`, `\cmidrule`, styled cells.
- **HTML tables**, **Markdown tables** — easy add-ons.
- **PDF tables** — hardest; optional later (Camelot/Tabula). Flag as "best effort."

> Design rule: every reader outputs a **tidy long-form table**: one row per (coordinates…, metric_name, value). All detectors run on that, so we never special-case a format again.

### 3.2 NORMALIZE — the messy-reality layer (grounded in a research paper's results)
This is where most engineering effort goes, because real tables are dirty:
- **Un-merge multirow/multicolumn** → fill coordinates down (p₁, p₂ spanned 4 rows each).
- **Missing cells:** `--`, `N/A`, blank, `-`, `NaN` → a single MISSING sentinel; never treat as 0.
- **Strip styling but KEEP the signal:** bold / color often marks the "winner" — record `is_highlighted` as a *hint*, then strip so the number parses.
- **Number parsing:** thousands separators, `8×10³` / `8E3` scientific, `%`, ranges like `[648, 661]`, ± error bars → extract the point value (+ optional uncertainty).
- **Unicode/format noise:** en-dash vs minus, non-breaking spaces, trailing footnote marks.

### 3.3 SCHEMA inference — what are the axes vs the metric?
The tool must figure out (or be told):
- Which columns are **input axes** (p₁, p₂, horizon, knowledge, method) vs the **output metric** (RMSE).
- For each axis: **numeric** (orderable by value), **ordered-categorical** (needs an order), or **unordered-categorical** (methods).
- Which axis is the **"entity/competitor"** axis for win-reversal (methods).
- **Auto-guess + always allow a user override**, because guessing is unreliable:
  - numeric if cells parse as numbers;
  - metric = the column whose header matches RMSE/error/accuracy/score, or the numeric body when axes are on the margins;
  - ordered-categorical order from a small built-in lexicon (low/med/high, none/some/all, both/one/neither) + user hint.

### 3.4 DETECTORS — run T1..T5 (details in §4)

### 3.5 RANK & REPORT
- Every flag gets a **severity score** and **priority** (§5).
- Output is **structured (JSON)** + a **human-readable report** with the exact coordinates, the numbers, and a **suggested next check**.

---

## 4. Detector logic + thresholds

We prefer **robust, distribution-aware** rules over fixed cutoffs, so the tool travels across datasets.

**T1 numeric jump.** For a metric series along one numeric axis (holding other axes fixed):
- compute step-to-step change; compare each step to the *typical* step (median absolute step, MAD).
- flag when a step is an outlier (e.g. > k·MAD, and/or ratio ≥ R× the neighbor).
- report the **bracketing interval** and suggest the **midpoints** to test.
- Robustness: need ≥3 points on the axis; ignore steps where either endpoint is MISSING.

**T2 monotonicity.** Expected direction from user (or "unknown"):
- count "wrong-direction" steps; flag the specific reversal points.
- if direction unknown, flag axes that are *non-monotonic* only when the swing is large relative to the series range.

**T3 win-reversal.** Best entity per position along the ordered axis:
- record the winner sequence; flag when winners change ≥2 times or a winner returns (A→B→A).
- guard against **near-ties** (if top-2 within ε, call it a tie, not a reversal) to avoid noise flags.

**T4 categorical-order exception.** Given a conceptual order on a category axis:
- for each pair (cat_i < cat_j) that "should" order the metric, count violations across all other coordinate combos.
- flag the **minority exceptions** (holds in N cases, fails in k, k ≪ N).

**T5 duplicate / flatness.**
- **Duplicate:** identical high-precision value at coordinates that should differ (esp. across horizon/table) → likely copy-paste. Exact match on many decimals = stronger signal.
- **Flatness:** a value identical across every level of an axis that is supposed to matter → "axis appears ignored."

---

## 5. Priority scoring (the advisor's "against-the-majority" idea)

Each flag → **priority = f(strength, support)**:
- **Support:** how strong is the "rule" it breaks? (Bucking a pattern that holds in 30/31 cases > bucking 6/10.)
- **Strength:** how big is the deviation? (120× jump > 1.5× jump; 5-decimal exact duplicate > coincidental round number.)
- Combine into **High / Medium / Low**. A point that violates an *overwhelming* majority = High (the advisor's exact instruction).
- Always show the evidence so a human can overrule.

---

## 6. Edge cases to handle (checklist, mostly learned from a research paper's results)

1. **Missing values** (`--`, N/A, blank) — never 0; skip in steps; report coverage.
2. **Multi-index / merged cells** — un-span before analysis.
3. **Only 1–2 points on an axis** — too few for jump/monotonic; say "insufficient".
4. **Ties / near-ties for the winner** — ε-tolerance to avoid fake reversals.
5. **Styled winners (bold/color)** — use as a hint, verify against the actual numbers (the paper's own highlight might be wrong — that itself is a flag).
6. **Mixed number formats** (scientific, %, thousands, ± error, bracket ranges).
7. **Unknown axis order** for categoricals — require/hint an order; otherwise skip T4.
8. **Metric direction unknown** (lower-better vs higher-better) — ask or infer from name; affects T2/T3.
9. **Multiple metrics in one table** (RMSE + MAE) — analyze each separately.
10. **Duplicate columns / repeated headers**, transposed tables (axes on rows vs columns).
11. **Scale differences** — normalize per-series so a big-magnitude series doesn't drown a small one.
12. **Non-independent axes** — a jump might be along p₁ *or* p₂; report the axis it's measured along, don't over-claim causation.
13. **Floating-point noise** — round sensibly before "identical value" checks (but keep high-precision exact-match as a strong duplicate signal).
14. **Huge tables** — keep detectors O(n) per axis; stream if needed.
15. **False-positive control** — every flag is a *suggestion to look*, never a verdict; tunable sensitivity.

---

## 7. Output format (example, on a research paper's results)

```json
{
  "table": "parameter_sweep_day1",
  "metric": "RMSE (lower better)",
  "flags": [
    {
      "type": "numeric_jump",
      "axis": "p1", "from": 0.12, "to": 0.15,
      "entity": "a research paper's results", "held_fixed": {"p2": 0.05, "knowledge": "Both known"},
      "values": [1.61, 195.81], "ratio": 121.0,
      "priority": "HIGH",
      "suggestion": "Test intermediate p1 = 0.13, 0.14 to locate where behavior breaks."
    },
    {
      "type": "win_reversal", "axis": "p1",
      "winner_sequence": ["a research paper's results@0.12","ARIMA@0.15","MethodB@0.20","MethodB@0.25"],
      "priority": "HIGH",
      "suggestion": "Characterize the p1 boundary where the best method changes."
    },
    {
      "type": "duplicate_value", "value": a repeated value,
      "locations": ["uniform_5day / a research paper's results / p1_known", "uniform_10day / a research paper's results / p1_known"],
      "priority": "MEDIUM",
      "suggestion": "Identical value across two horizons — check for copy/paste error."
    }
  ]
}
```
Plus a plain-English report listing each flag with its numbers and next-check.

---

## 8. Validation (status)

**#1 a research paper's results (test case) — PASSED.** The tool automatically re-discovered every anomaly
we first found by hand, from the raw LaTeX tables:
- T1 jump p₁ one setting to the next on a research paper's results, with suggestion "test p₁=0.13, 0.14" (exactly the advisor's ask).
- T3 win-reversal a research paper's results→ARIMA→MethodB along p₁.
- T5 duplicate a repeated value across the 5-day & 10-day tables — *without* firing on MethodB/LSTM's legitimate repeats.
- T2 monotonicity: error expected to rise with horizon but falls.
- T5 flatness: "knowledge" ignored by a research paper's results in the day-1 sweep.

**#3 Ozone hole (historical gold standard) — PASSED, and it taught us T6.**
Real BAS Halley total-ozone series (ZOZ5699.DAT, 1956–2011, Dobson Units). October baseline
1957–75 = ~297 DU (SD 16); hole era 1990–2000 = ~140 DU (a 53% drop).
- **First run MISSED it:** the five sharp-anomaly detectors found only LOW-priority year-to-year noise, because the ozone hole is a *slow, sustained* decline — every single step looks typical.
- **Fix:** added **T6 level-shift**. Re-run flags `[HIGH] level_shift: ozone holds ~297 (1956–1974) then shifts to ~167 from 1980 onward (8 SDs, 39 points)`.
- **Takeaway:** the tool needs both a "fast anomaly" detector (T1) and a "slow anomaly" detector (T6). The ozone case is the canonical example of a slow anomaly wrongly auto-discarded.

**#2 Other public datasets — PARTIALLY DONE, and it drove two improvements.**
- **Anscombe's quartet** (ground truth: I/II clean, III & IV each have one outlier): the tool now
  flags III's outlier (T7) and correctly stays silent on I/II. IV (leverage outlier in x) is a known
  gap. This test is what motivated adding **T7 (single-point outlier)**.
- **Mauna Loa CO₂ annual** (steady monotonic rise, should NOT alarm): the first T6 FALSE-flagged the
  rise as a level shift. This motivated the **R²-based trend guard**; CO₂ is now correctly a LOW
  candidate, while ozone stays HIGH. Net: false-positive control improved and two detectors hardened.

## 9. Build order

1. Core on **tidy long-form + CSV** ingest; implement T1, T3 first (the two we're surest of).
2. Add T2, T4, T5; add priority scoring.
3. Add **LaTeX** ingest (reuse a research paper's results parser) → run full a research paper's results validation.
4. Add **Excel/HTML/Markdown** ingest.
5. Public-dataset + **ozone** validation.
6. (Optional, later) PDF ingest; optional LLM layer to *phrase* suggestions — never to decide them.

---

## 10. Open questions for the advisor
- Confirm the built-in **conceptual orders** for categorical axes (e.g. knowledge: both > one > none).
- Default **sensitivity** (how aggressive should flagging be)?
- Preferred **primary input format** for his group's tables (CSV export vs LaTeX source)?


---

## 11. Implementation status (as built)

Package `anomalynerd/` (Python, pandas/numpy):
- `model.py` — TidyTable / Axis / Flag, MISSING sentinel.
- `ingest.py` — **generic CSV ingest** with schema auto-inference: handles LONG (axes + metric column) and WIDE (entities as columns; first column = index by default, overridable) shapes; number parsing tolerates `--`/`N/A`/blank → MISSING, thousands separators, `%`, `8×10³` scientific, unicode minus; categorical order via a small lexicon + hint.
- `detectors.py` — T1 jump (trend-aware), T2 monotonicity, T3 win-reversal, T4 categorical-exception, T5 duplicate + flatness (coordinate-aware), T6 level-shift (R²-based trend guard), T7 single-point outlier (residual + leverage, with trend-endpoint guard), T8 trend (LOW candidate by default; HIGH/MED when the axis is marked expect_flat). Plus index/ID-axis suppression and adjacent-to-outlier jump suppression.
- `analyze.py` — priority scorer (HIGH/MED/LOW) + report.
- `tests/` — `test_synthetic.py` (each detector fires on a toy table) + `test_datasets.py` (ground-truth checks on ozone, CO₂, Anscombe I/III/IV, Newcomb, draft lottery, a research paper's results). 12 checks, all passing.
- `cli.py` — `python -m anomalynerd.cli FILE.csv [--metric --lower/higher-better --ignore --order --expect-increasing/-decreasing --expect-flat --json]`.

**Validated on 8 real datasets** (a research paper's results, ozone, CO₂, Anscombe, Newcomb, draft lottery, thyroid-as-summary, and two ordered-category tables) + synthetic, with zero false HIGH/MEDIUM flags. Big datasets are handled by first **summarizing** them into a results table (rates/means per bin or group) — the tool is a results-table scanner, not a raw big-data ML classifier.

**Still to do:** HTML/Markdown ingest wrappers (Excel, Word and LaTeX ingest are done); more public-dataset validations; optional LLM layer to *phrase* suggestions (never to decide them).
