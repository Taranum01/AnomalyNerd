"""Ground-truth arXiv benchmark for AnomalyNerd.

A companion citation-verification approach got objective ground truth by swapping cited sources, so the right answer
was known. We do the analogous thing for results tables: take REAL results tables from random
arXiv papers (via LaTeX source, which extracts cleanly), and for each one create a matched
pair:

    - CLEAN   : the real table as published
    - PLANTED : an identical copy with ONE injected anomaly of a known type at a known cell

We then run AnomalyNerd on both and ask:
    - did it flag the PLANTED anomaly at the right place?   (detection -> recall)
    - how often does a planted run's flag land on the planted cell vs elsewhere? (precision)
    - how often does the CLEAN copy raise a flag?            (baseline flag rate)

Honesty note: a real 'clean' table may ALREADY contain a genuine anomaly (that is the whole
point of the tool), so a flag on a clean table is NOT automatically a false positive. We
therefore score detection against the PLANTED cell specifically, and report the clean-table
flag rate separately as context, not as an error rate.
"""
from __future__ import annotations
import argparse, copy, json, os, random, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from anomalynerd.analyze import analyze
from anomalynerd.latex_ingest import parse_latex
from anomalynerd.model import TidyTable
# reuse the arXiv source fetch + sampling from the coverage test
from tests.arxiv_scale_test import sample_arxiv_ids, download_source_tex


# ----------------------------------------------------------------- collect real tables
def collect_tables(ids, outdir, max_tables=80):
    """Return a list of (paper_id, TidyTable) for tables that parse to a clean numeric
    results table with a usable numeric metric column and enough rows."""
    pool = []
    for aid in ids:
        texp = download_source_tex(aid, outdir)
        if not texp:
            continue
        try:
            res = parse_latex(texp)
        except Exception:
            continue
        for t in res.tables:
            if t.status == "ok" and t.tidy is not None and _is_clean_single_metric(t.tidy):
                pool.append((aid, t.tidy))
        if len(pool) >= max_tables:
            break
        time.sleep(1)
    return pool


def _is_clean_single_metric(tidy):
    """Eligible for the ground-truth benchmark only if a single-cell planted anomaly is
    WELL-DEFINED. That means: a simple results table with at most two axes and one metric,
    enough distinct numeric values to establish a pattern, and a majority of values that are
    plain numbers (not composite strings like '25/47' or '+5.4 (<0.001, 37/47)' that signal
    a mixed-content table). Complex multi-way or mixed tables are excluded, because planting
    one value there is not a clean, scorable anomaly and would measure table-parsing quirks
    rather than the detector."""
    vals = [r.get("value") for r in tidy.rows if isinstance(r.get("value"), (int, float))]
    if len(vals) < 6 or len(set(vals)) < 5:
        return False
    # at most two input axes (one series/category axis + optionally one entity axis)
    if len(tidy.axes) > 2:
        return False
    # the values should mostly be clean numbers: require that the fraction of rows whose
    # 'value' parsed numerically is high relative to total rows (mixed cells drop out as
    # non-numeric during ingest, leaving a sparse numeric column)
    if len(vals) < 0.6 * len(tidy.rows):
        return False
    return True


# ----------------------------------------------------------------- plant one anomaly
def _numeric_rows(tidy):
    return [i for i, r in enumerate(tidy.rows)
            if isinstance(r.get("value"), (int, float))]


def _has_ordered_numeric_axis(tidy):
    for name, ax in tidy.axes.items():
        if ax.kind == "numeric":
            return name
    return None


def applicable_kinds(tidy):
    """Which planted-anomaly types are MEANINGFUL for this table's structure. Planting a
    level shift or jump only makes sense along an ordered numeric axis; a point/categorical
    outlier makes sense on any table with numeric values. This keeps the benchmark honest:
    we never score the tool on an anomaly type that cannot exist in the given table shape."""
    kinds = ["point_outlier"]
    if _has_ordered_numeric_axis(tidy):
        kinds += ["numeric_jump", "level_shift"]
    return kinds


def plant_anomaly(tidy, kind, rng):
    """Return (planted_table, ground_truth_dict) with ONE injected anomaly. Does not mutate
    the input. ground_truth gives the row index and the (axis-coords) of the planted cell.
    For jump/level_shift the injection is placed along an ordered numeric axis.

    Scale-aware: if the column naturally spans many orders of magnitude (log-scale data such
    as dataset sizes or counts), the injected anomaly is a departure in LOG space, so it is
    genuinely anomalous relative to how that column actually behaves. Otherwise it is a
    linear departure sized by the column's spread. This keeps the benchmark honest: a planted
    anomaly is always clearly out of pattern for its own column, not merely a large number in
    a column that already contains large numbers."""
    import math
    t = copy.deepcopy(tidy)
    idxs = _numeric_rows(t)
    vals = [t.rows[i]["value"] for i in idxs]
    vmax, vmin = max(vals), min(vals)
    spread = (vmax - vmin) or (abs(vmax) or 1.0)
    num_axis = _has_ordered_numeric_axis(t)

    # decide the column's natural scale
    pos = [v for v in vals if v > 0]
    is_log = len(pos) == len(vals) and len(pos) >= 3 and min(pos) > 0 and \
        math.log10(max(pos) / min(pos)) >= 2.0

    def _anomalous_value(base):
        """A value clearly out of pattern for this column's own scale."""
        if is_log:
            # push ~3 orders of magnitude beyond the natural log-range
            return (10 ** (math.log10(max(pos)) + 3))
        return vmax + 10 * spread

    def _shift_amount():
        return (10 ** (math.log10(max(pos)) + 2)) if is_log else 8 * spread

    if kind == "point_outlier":
        i = idxs[rng.randrange(len(idxs))]
        t.rows[i]["value"] = _anomalous_value(t.rows[i]["value"])
    elif kind == "numeric_jump":
        order = sorted(idxs, key=lambda r: float(t.rows[r][num_axis]))
        i = order[len(order) // 2]
        t.rows[i]["value"] = _anomalous_value(t.rows[i]["value"])
    elif kind == "level_shift":
        order = sorted(idxs, key=lambda r: float(t.rows[r][num_axis]))
        k = max(1, int(0.4 * len(order)))
        amt = _shift_amount()
        for r in order[-k:]:
            t.rows[r]["value"] = t.rows[r]["value"] + amt
        i = order[-k]   # ground-truth anchor = where the shift begins
    else:
        raise ValueError(kind)

    coords = {k: v for k, v in t.rows[i].items() if k != "value"}
    gt = {"kind": kind, "row_index": i, "coords": coords,
          "planted_value": t.rows[i]["value"]}
    return t, gt


def _injection_is_subtle(tidy, gt):
    """Was the planted value only weakly out of pattern? Compares the injected value against
    the ORIGINAL column's robust spread (median + MAD). If it sits within ~5 robust-SDs of
    the rest, a miss is honest (the value is not a clear anomaly), not a detector gap."""
    import statistics
    vals = [r.get("value") for r in tidy.rows if isinstance(r.get("value"), (int, float))]
    if len(vals) < 4:
        return True
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals]) or 0.0
    scale = 1.4826 * mad
    planted = gt.get("planted_value")
    if scale == 0 or planted is None:
        # degenerate spread: fall back to range comparison
        rng = (max(vals) - min(vals)) or 1.0
        return planted is not None and abs(planted - med) < 2 * rng
    return abs(planted - med) / scale < 5.0


def _flag_hits(flags, gt):
    """Did any HIGH/MEDIUM flag land on the planted cell's coordinates?"""
    hi_med = [f for f in flags if f.priority in ("HIGH", "MEDIUM")]
    if not hi_med:
        return False, len(hi_med)
    gt_coords = gt["coords"]
    for f in hi_med:
        # a flag matches if its coords/detail reference the planted axis value
        fc = {**(f.coords or {})}
        det = f.detail or {}
        # match if any ground-truth coord value appears in the flag coords or detail 'at'
        gtvals = set(str(v) for v in gt_coords.values())
        fvals = set(str(v) for v in fc.values()) | {str(det.get("at"))}
        if gtvals & fvals:
            return True, len(hi_med)
    # level shifts are reported as a span, not a single cell: accept a level_shift flag
    if gt["kind"] == "level_shift" and any(f.type == "level_shift" for f in hi_med):
        return True, len(hi_med)
    return False, len(hi_med)


# ----------------------------------------------------------------- benchmark
KINDS = ["point_outlier", "numeric_jump", "level_shift"]


def run(n_papers=40, seed=7, outdir="/tmp/pdf_dev/arxiv_bench", series=False):
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(seed)
    # Series-rich domains have ordered-variable tables (time, dose, size) that ML papers
    # lack, so jump/level-shift anomalies can actually be planted. ML categories are mostly
    # method x benchmark (categorical), good for point/categorical outliers.
    SERIES_CATEGORIES = ["econ.EM", "q-bio.PE", "physics.ao-ph", "stat.AP", "q-fin.ST",
                         "astro-ph.IM"]
    categories = SERIES_CATEGORIES if series else None
    tag = "series" if series else "ml"
    # Cache the sampled IDs so re-runs skip the slow arXiv API. The .tex source files are
    # already cached on disk by download_source_tex, so collect_tables re-parses locally.
    ids_cache = os.path.join(outdir, f"sampled_ids_{tag}_seed{seed}_n{n_papers}.json")
    if os.path.exists(ids_cache):
        ids = json.load(open(ids_cache))
        print(f"Using {len(ids)} cached sampled IDs (delete {ids_cache} to re-sample).")
    else:
        print(f"Sampling arXiv papers (seed={seed}, {tag} categories) ...")
        ids = sample_arxiv_ids(n_papers, seed=seed, categories=categories)
        json.dump(ids, open(ids_cache, "w"))
    print(f"Collecting real LaTeX tables from {len(ids)} papers (cached source reused) ...")
    pool = collect_tables(ids, outdir)
    print(f"Usable real tables collected: {len(pool)}\n")

    per_kind = {k: {"tp": 0, "fn": 0} for k in KINDS}
    clean_flagged = 0
    rows = []
    miss_reasons = {"detector_silent": 0, "flagged_elsewhere": 0, "subtle_injection": 0}
    for pi, (aid, tidy) in enumerate(pool):
        # baseline: does the CLEAN table flag anything hi/med?
        clean_flags = analyze(tidy)
        clean_hi = [f for f in clean_flags if f.priority in ("HIGH", "MEDIUM")]
        if clean_hi:
            clean_flagged += 1
        # plant one anomaly of each APPLICABLE kind (independent trials on the same base)
        for kind in applicable_kinds(tidy):
            try:
                planted, gt = plant_anomaly(tidy, kind, rng)
            except Exception:
                continue
            flags = analyze(planted)
            hit, n_himed = _flag_hits(flags, gt)
            per_kind[kind]["tp" if hit else "fn"] += 1
            reason = None
            if not hit:
                # classify WHY it was missed, honestly:
                #  - detector_silent : no hi/med flag anywhere -> the detector did not react
                #  - flagged_elsewhere: a hi/med flag fired but not on the planted cell
                #                       (localization/scoring issue, detector DID react)
                #  - subtle_injection : the injected value is within ~5 robust-SDs of the
                #                       column, i.e. genuinely not a clear anomaly
                reason = ("flagged_elsewhere" if n_himed > 0
                          else ("subtle_injection" if _injection_is_subtle(tidy, gt)
                                else "detector_silent"))
                miss_reasons[reason] += 1
            rows.append({"paper": aid, "kind": kind, "detected": hit,
                         "miss_reason": reason, "clean_had_flag": bool(clean_hi)})

    # ---- summary ----
    print("=" * 70)
    print("GROUND-TRUTH ARXIV BENCHMARK  (planted anomalies in real tables)")
    print("=" * 70)
    print(f"Real tables used        : {len(pool)}")
    print(f"Clean tables that flagged: {clean_flagged}/{len(pool)} "
          f"({100*clean_flagged/max(1,len(pool)):.0f}%) "
          f"-- context, may be genuine anomalies, not errors")
    print("\nDetection recall by planted-anomaly type:")
    total_tp = total = 0
    for k in KINDS:
        tp, fn = per_kind[k]["tp"], per_kind[k]["fn"]
        tot = tp + fn
        total_tp += tp; total += tot
        rec = 100 * tp / tot if tot else 0
        print(f"  {k:14}: {tp}/{tot} caught  ({rec:.0f}% recall)")
    print(f"\nOverall planted-anomaly recall: {total_tp}/{total} "
          f"({100*total_tp/max(1,total):.0f}%)")
    nmiss = sum(miss_reasons.values())
    print(f"\nMiss analysis ({nmiss} misses):")
    print(f"  subtle injection (within ~5 robust-SDs, not a clear anomaly): "
          f"{miss_reasons['subtle_injection']}")
    print(f"  flagged elsewhere on the table (localization, detector DID react): "
          f"{miss_reasons['flagged_elsewhere']}")
    print(f"  detector silent (a possible real detector gap): "
          f"{miss_reasons['detector_silent']}")

    out = os.path.join(outdir, "arxiv_ground_truth_benchmark.json")
    with open(out, "w") as f:
        json.dump({"n_papers": n_papers, "seed": seed, "tables": len(pool),
                   "clean_flagged": clean_flagged, "per_kind": per_kind,
                   "rows": rows}, f, indent=2)
    print(f"\nFull results: {out}")
    return per_kind


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=40, help="number of papers to sample")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--series", action="store_true",
                    help="sample series-rich domains (econ/epi/climate) that have ordered "
                         "tables suitable for jump/level-shift planting")
    args = ap.parse_args()
    run(n_papers=args.n, seed=args.seed, series=args.series)
