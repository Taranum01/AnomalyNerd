"""Related-work head-to-head + paired test for AnomalyNerd.

Goal: run the other tools (paperguard, Great Expectations) on the SAME tables,
tabulate what each flags vs AnomalyNerd, and do a paired statistical test
(paired sign-flip permutation test, Shasha & Wilson, 'Statistics is Easy!').

Shared per-item task: the ground-truth planted-anomaly benchmark. For each real table we plant
one known anomaly at a known cell, then ask each tool whether it CATCHES that planted anomaly.

Fair 'hit' definitions (each tool judged by its own output on the planted table):
  - AnomalyNerd : a HIGH/MEDIUM flag at/near the planted cell (strict localization).
  - paperguard  : any finding on the table (it cannot localize to our cell, so we are
                  generous and count any flag as a 'catch').
  - Great Exp.  : any expectation violated under a standard expectation suite (non-null,
                  observed range, 3-sigma z-score) — again generous, table-level.

Honest framing (reported, not hidden): paperguard and GE target fabrication/data-quality
signatures, NOT scientific-pattern anomalies, so this measures who catches the planted
scientific-pattern anomalies — AnomalyNerd's niche — and quantifies the gap with a paired test.
"""
import os, sys, csv, json, tempfile, random, warnings, logging, subprocess, io, math
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
# make the package importable regardless of where this script lives
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anomalynerd.analyze import analyze
from tests.arxiv_ground_truth_benchmark import (
    collect_tables, plant_anomaly, _flag_hits, applicable_kinds)

# working/cache directory under the system temp dir (portable, created on use)
OUTDIR = os.path.join(tempfile.gettempdir(), "anomalynerd_relwork")


def _tidy_to_csv(tidy):
    """Write a planted tidy table back to a flat CSV (axes + value) for the external tools."""
    axes = list(tidy.axes)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.writer(tmp)
    w.writerow(axes + [tidy.metric_name or "value"])
    for r in tidy.rows:
        w.writerow([r.get(a, "") for a in axes] + [r.get("value", "")])
    tmp.close()
    return tmp.name


def _anomalynerd_hit(planted, gt):
    hit, _ = _flag_hits(analyze(planted), gt)
    return 1 if hit else 0


def _paperguard_hit(csv_path):
    """Returns 1 if paperguard raised any finding on the table (generous, table-level),
    0 if it ran cleanly and found nothing, or None if it could NOT be run (not installed,
    crashed, timed out, or produced unreadable output). A None is EXCLUDED from the paired
    comparison — never counted as 'caught nothing' — so a tool failure cannot inflate the gap."""
    import shutil
    if shutil.which("paperguard") is None:
        return None
    out = None
    try:
        tmpf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); tmpf.close()
        out = tmpf.name
        r = subprocess.run(["paperguard", "scan", csv_path, "--output-json", out],
                           capture_output=True, text=True, timeout=90)
        if r.returncode not in (0, 1):     # 0/1 are normal (1 often = findings present)
            return None
        data = json.load(open(out))
        txt = json.dumps(data).lower()
        findings = txt.count('"severity"') + txt.count('"flag')
        for key in ("findings", "issues", "results", "detections"):
            v = data.get(key) if isinstance(data, dict) else None
            if isinstance(v, list):
                findings = max(findings, len(v))
        return 1 if findings > 0 else 0
    except Exception:
        return None                        # could not run -> excluded, NOT a zero
    finally:
        if out and os.path.exists(out):
            os.unlink(out)


def _ge_hit(tidy):
    """Returns 1 if any expectation was violated (generous, table-level), 0 if GE ran cleanly
    and nothing was violated, or None if GE could NOT be run (not installed or it errored).
    None is EXCLUDED from the comparison, never counted as 'caught nothing'."""
    try:
        import great_expectations as gx
        import pandas as pd
    except Exception:
        return None
    vals = [r.get("value") for r in tidy.rows if isinstance(r.get("value"), (int, float))]
    if len(vals) < 3:
        return None                        # too few values for GE to assess -> excluded
    import statistics as st
    df = pd.DataFrame({"value": vals})
    mean, sd = st.mean(vals), (st.pstdev(vals) or 1.0)
    try:
        ctx = gx.get_context(mode="ephemeral")
        batch = (ctx.data_sources.add_pandas("p").add_dataframe_asset("a")
                 .add_batch_definition_whole_dataframe("b")
                 .get_batch(batch_parameters={"dataframe": df}))
        checks = [
            gx.expectations.ExpectColumnValuesToNotBeNull(column="value"),
            gx.expectations.ExpectColumnValuesToBeBetween(
                column="value", min_value=mean - 3 * sd, max_value=mean + 3 * sd),
        ]
        violated = 0
        for e in checks:
            res = batch.validate(e)
            if not res.success:
                violated += 1
        return 1 if violated > 0 else 0
    except Exception:
        return None                        # could not run -> excluded, NOT a zero


def _paired_sign_flip(a, b, iters=10000, seed=0):
    """Paired sign-flip permutation test (Shasha & Wilson). a,b are per-item 0/1 hit vectors.
    Statistic = mean(a) - mean(b). Under H0 the paired difference's sign is random; we flip
    each paired difference's sign at random and count how often |permuted mean| >= |observed|."""
    rng = random.Random(seed)
    diffs = [ai - bi for ai, bi in zip(a, b)]
    obs = sum(diffs) / len(diffs)
    ge = 0
    for _ in range(iters):
        s = sum(d if rng.random() < 0.5 else -d for d in diffs)
        if abs(s / len(diffs)) >= abs(obs) - 1e-12:
            ge += 1
    p = ge / iters
    return obs, p


def run(seed=13, n_papers=50):
    os.makedirs(OUTDIR, exist_ok=True)
    ids_cache = f"{OUTDIR}/sampled_ids_ml_seed{seed}_n{n_papers}.json"
    ids = json.load(open(ids_cache)) if os.path.exists(ids_cache) else None
    if ids is None:
        from tests.arxiv_scale_test import sample_arxiv_ids
        ids = sample_arxiv_ids(n_papers, seed=seed)
        json.dump(ids, open(ids_cache, "w"))
    pool = collect_tables(ids, OUTDIR)
    rng = random.Random(seed)

    # per planted item we record (anomalynerd 0/1, paperguard 0/1/None, GE 0/1/None).
    # None means the competitor could not be run on that item and is EXCLUDED from its
    # comparison (never counted as a zero), so a tool failure cannot inflate the gap.
    items = []
    for aid, tidy in pool:
        for kind in applicable_kinds(tidy):
            try:
                planted, gt = plant_anomaly(tidy, kind, rng)
            except Exception:
                continue
            csv_path = _tidy_to_csv(planted)
            a = _anomalynerd_hit(planted, gt)
            p = _paperguard_hit(csv_path)
            g = _ge_hit(planted)
            os.unlink(csv_path)
            items.append({"an": a, "pg": p, "ge": g})

    N = len(items)

    def _rate_and_test(key, label):
        """Compare AnomalyNerd vs one competitor on ONLY the items where the competitor ran
        (its value is not None). Returns (n_compared, an_caught, comp_caught, diff, p)."""
        paired = [(it["an"], it[key]) for it in items if it[key] is not None]
        n = len(paired)
        if n == 0:
            return 0, 0, 0, None, None
        a_vec = [x for x, _ in paired]; c_vec = [y for _, y in paired]
        diff, pval = _paired_sign_flip(a_vec, c_vec, iters=10000, seed=seed)
        return n, sum(a_vec), sum(c_vec), diff, pval

    print("=" * 70)
    print("RELATED-WORK HEAD-TO-HEAD (planted scientific-pattern anomalies)")
    print("=" * 70)
    print(f"Total planted anomalies: {N}")
    print("Each competitor is compared ONLY on items where it actually ran; items where a")
    print("tool could not run (not installed / crashed / too few values) are EXCLUDED, not")
    print("counted as misses.\n")

    an_overall = sum(it["an"] for it in items)
    print(f"AnomalyNerd caught {an_overall}/{N} = {an_overall/N:.0%} overall\n")
    print(f"{'comparison':24}{'n (both ran)':>14}{'AN':>7}{'other':>7}"
          f"{'diff':>8}{'p':>10}{'sig':>5}")
    results = {}
    for key, label in [("pg", "vs paperguard"), ("ge", "vs Great Expectations")]:
        n, an_c, c_c, diff, pval = _rate_and_test(key, label)
        results[key] = {"n": n, "an_caught": an_c, "comp_caught": c_c,
                        "diff": diff, "p": pval}
        if n == 0:
            print(f"  {label:22}{'0':>14}  (tool never ran — excluded)")
            continue
        ptxt = "<0.0001" if pval < 1e-4 else f"{pval:.4f}"
        sig = "Yes" if pval < 0.05 else "No"
        print(f"  {label:22}{n:>14}{an_c/n:>7.0%}{c_c/n:>7.0%}{diff:>+8.0%}{ptxt:>10}{sig:>5}")

    out = f"{OUTDIR}/related_work_paired.json"
    json.dump({"N": N, "an_overall": an_overall, "results": results,
               "items": items}, open(out, "w"))
    print(f"\nFull results: {out}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("-n", type=int, default=50)
    a = ap.parse_args()
    run(seed=a.seed, n_papers=a.n)
