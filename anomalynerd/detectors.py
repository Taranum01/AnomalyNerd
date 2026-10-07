"""Detectors T1..T5. Each takes a TidyTable and returns a list[Flag].

Design rules:
- robust (median/MAD) thresholds, not fixed cutoffs, so the tool travels across datasets
- every flag is a SUGGESTION to look, never a verdict
- missing values are skipped, never treated as 0
"""
from __future__ import annotations
from itertools import product
from statistics import median
from .model import TidyTable, Flag, Axis, is_num


def _mad(values):
    if len(values) < 2:
        return 0.0
    m = median(values)
    return median([abs(v - m) for v in values]) or 0.0


def index_like_axes(t):
    """Return axis names that look like a row-INDEX/ID rather than a meaningful variable:
    a numeric axis whose values are (nearly) 1..N consecutive with one row each. On such an
    axis a 'jump' or 'level shift' is meaningless (the x is just a serial number) — only
    per-point outliers make sense. Newcomb's 'measurement' 1..66 is the canonical case."""
    out = set()
    n = len(t.rows)
    for ax_name, ax in t.axes.items():
        if ax.kind != "numeric":
            continue
        vals = [r.get(ax_name) for r in t.rows if isinstance(r.get(ax_name), (int, float))]
        if len(vals) < 5 or len(set(vals)) != len(vals):
            continue  # must be one row per value
        s = sorted(vals)
        steps = {round(s[i + 1] - s[i], 6) for i in range(len(s) - 1)}
        if steps == {1.0} or (len(steps) == 1 and s == list(range(int(s[0]), int(s[0]) + len(s)))):
            out.add(ax_name)
    return out


def _other_axis_combos(t: TidyTable, along: str):
    """All coordinate combinations of axes other than `along` (and entity axis kept separate)."""
    others = [a for a in t.axes if a != along]
    level_lists = [t.levels(a) for a in others]
    for combo in product(*level_lists) if level_lists else [()]:
        yield dict(zip(others, combo))


# ---------------------------------------------------------------- T1
def detect_numeric_jumps(t: TidyTable, k_mad=4.0, ratio=3.0, min_points=3):
    """Flag adjacent steps along a numeric axis that jump far more than the typical step."""
    flags = []
    for ax_name, ax in t.axes.items():
        if ax.kind != "numeric":
            continue
        levels = ax.sorted_levels(t.levels(ax_name))
        if len(levels) < min_points:
            continue
        for fixed in _other_axis_combos(t, ax_name):
            series = [(lv, t.value_at(**{ax_name: lv}, **fixed)) for lv in levels]
            pts = [(lv, v) for lv, v in series if is_num(v)]
            if len(pts) < min_points:
                continue
            steps = [pts[i + 1][1] - pts[i][1] for i in range(len(pts) - 1)]   # SIGNED steps
            abs_steps = [abs(s) for s in steps]
            typ = _mad(abs_steps) or (median(abs_steps) if abs_steps else 0.0)
            med_step = median(steps) if steps else 0.0   # typical signed step = local trend
            step_mad = _mad(steps) or typ
            for i in range(len(pts) - 1):
                (a, va), (b, vb) = pts[i], pts[i + 1]
                step = abs(vb - va)
                r = (max(va, vb) / min(va, vb)) if min(va, vb) > 0 else float("inf")
                # A jump is interesting only if the step deviates from the LOCAL TREND
                # (the typical signed step), not merely from zero. On a smooth trend every
                # step ~ med_step, so deviation is small and nothing fires. A real jump is
                # both far from the trend AND a large ratio.
                trend_dev = abs((vb - va) - med_step) / step_mad if step_mad > 0 else 0.0
                big = (trend_dev > k_mad) and (r >= ratio)
                if big:
                    mids = _suggest_midpoints(a, b)
                    flags.append(Flag(
                        type="numeric_jump", table=t.name, axis=ax_name,
                        coords=fixed,
                        detail={"from": a, "to": b, "values": [va, vb],
                                "ratio": round(r, 2), "step": round(step, 3),
                                "typical_step": round(typ, 3),
                                "trend_dev_sd": round(trend_dev, 1)},
                        strength=r if r != float("inf") else 999.0,
                        support=1.0,
                        suggestion=f"Large jump in {t.metric_name} between {ax_name}={a} and {ax_name}={b}"
                                   + (f"; consider testing {ax_name}={mids}." if mids else "."),
                    ))
    return flags


def _suggest_midpoints(a, b):
    try:
        a, b = float(a), float(b)
    except Exception:
        return None
    lo, hi = min(a, b), max(a, b)
    gap = hi - lo
    mids = [round(lo + gap * f, 4) for f in (1/3, 2/3)]
    # nice round intermediates if the endpoints look like 0.12 / 0.15
    step = round(gap / 3, 4)
    if step > 0:
        cand = [round(lo + step, 4), round(lo + 2 * step, 4)]
        return cand
    return mids


# ---------------------------------------------------------------- T2
def detect_monotonicity(t: TidyTable, expected: dict[str, str] | None = None, min_points=3):
    """expected: {axis_name: 'increasing'|'decreasing'}. If absent, flag only large non-monotonic swings."""
    expected = expected or {}
    flags = []
    for ax_name, ax in t.axes.items():
        if ax.kind not in ("numeric", "ordered_cat"):
            continue
        levels = ax.sorted_levels(t.levels(ax_name))
        if len(levels) < min_points:
            continue
        for fixed in _other_axis_combos(t, ax_name):
            pts = [(lv, t.value_at(**{ax_name: lv}, **fixed)) for lv in levels]
            pts = [(lv, v) for lv, v in pts if is_num(v)]
            if len(pts) < min_points:
                continue
            vals = [v for _, v in pts]
            rng = max(vals) - min(vals) or 1.0
            exp = expected.get(ax_name)
            reversals = []
            for i in range(len(pts) - 1):
                d = pts[i + 1][1] - pts[i][1]
                wrong = (exp == "increasing" and d < 0) or (exp == "decreasing" and d > 0)
                if wrong and abs(d) / rng > 0.05:
                    reversals.append({"from": pts[i][0], "to": pts[i + 1][0],
                                      "values": [pts[i][1], pts[i + 1][1]]})
            if exp and reversals:
                flags.append(Flag(
                    type="monotonicity", table=t.name, axis=ax_name, coords=fixed,
                    detail={"expected": exp, "reversals": reversals,
                            "sequence": [v for _, v in pts]},
                    strength=max(abs(r["values"][1] - r["values"][0]) for r in reversals) / rng,
                    support=1.0,
                    suggestion=f"{t.metric_name} was expected to be {exp} along {ax_name} but reverses; check why.",
                ))
    return flags


# ---------------------------------------------------------------- T3
def detect_win_reversals(t: TidyTable, tie_eps=0.02, min_points=3):
    """Along each ordered axis, does the best entity flip back and forth?"""
    flags = []
    if not t.entity_axis:
        return flags
    ent = t.entity_axis
    entities = t.levels(ent)
    for ax_name, ax in t.axes.items():
        if ax_name == ent or ax.kind not in ("numeric", "ordered_cat"):
            continue
        levels = ax.sorted_levels(t.levels(ax_name))
        if len(levels) < min_points:
            continue
        non_ent = [a for a in t.axes if a not in (ent, ax_name)]
        level_lists = [t.levels(a) for a in non_ent]
        for combo in (product(*level_lists) if level_lists else [()]):
            fixed = dict(zip(non_ent, combo))
            winners = []
            for lv in levels:
                scored = []
                for e in entities:
                    v = t.value_at(**{ax_name: lv, ent: e}, **fixed)
                    if is_num(v):
                        scored.append((e, v))
                if not scored:
                    winners.append(None); continue
                scored.sort(key=lambda x: x[1], reverse=not t.lower_is_better)
                best_v = scored[0][1]
                # tie guard: if 2nd within eps, still take best but note
                winners.append((lv, scored[0][0], best_v))
            seq = [w for w in winners if w]
            names = [w[1] for w in seq]
            distinct = list(dict.fromkeys(names))
            # reversal = a winner returns, or 3+ distinct winners
            returns = any(names[i] != names[i - 1] and names[i] in names[:i - 1]
                          for i in range(2, len(names)))
            if len(distinct) >= 3 or returns:
                flags.append(Flag(
                    type="win_reversal", table=t.name, axis=ax_name, coords=fixed,
                    detail={"winner_sequence": [f"{w[1]}@{w[0]}" for w in seq],
                            "distinct_winners": distinct},
                    strength=float(len(distinct)),
                    support=1.0,
                    suggestion=f"Best {ent} changes along {ax_name} (winners: {distinct}); "
                               f"characterize where each wins.",
                ))
    return flags


# ---------------------------------------------------------------- T4
def detect_cat_exceptions(t: TidyTable, min_support=4):
    """For an ordered categorical axis: cat_i 'should' out-rank cat_j almost always; flag the exceptions."""
    flags = []
    for ax_name, ax in t.axes.items():
        if ax.kind != "ordered_cat" or not ax.order:
            continue
        order = [c for c in ax.order if c in t.levels(ax_name)]
        if len(order) < 2:
            continue
        others = [a for a in t.axes if a != ax_name]
        level_lists = [t.levels(a) for a in others]
        for i in range(len(order) - 1):
            hi, lo = order[i], order[i + 1]
            wins = fails = 0
            exceptions = []
            for combo in (product(*level_lists) if level_lists else [()]):
                fixed = dict(zip(others, combo))
                vhi = t.value_at(**{ax_name: hi}, **fixed)
                vlo = t.value_at(**{ax_name: lo}, **fixed)
                if not (is_num(vhi) and is_num(vlo)):
                    continue
                # "should" direction: earlier-in-order tends to have the better metric
                better_first = (vhi < vlo) if t.lower_is_better else (vhi > vlo)
                if better_first:
                    wins += 1
                elif vhi != vlo:
                    fails += 1
                    exceptions.append({"coords": fixed, "values": {hi: vhi, lo: vlo}})
            total = wins + fails
            if total >= min_support and 0 < fails <= max(1, total // 4):
                flags.append(Flag(
                    type="cat_exception", table=t.name, axis=ax_name,
                    coords={"pair": [hi, lo]},
                    detail={"holds": wins, "fails": fails, "exceptions": exceptions},
                    strength=wins / total,
                    support=wins / total,
                    suggestion=f"'{hi}' usually ranks better than '{lo}' ({wins}/{total}); "
                               f"{fails} exception(s) — worth a look.",
                ))
    return flags


# ---------------------------------------------------------------- T5
def detect_duplicates_and_flatness(t: TidyTable, min_decimals=2):
    """Two signals, kept distinct:

    FLATNESS: an entity's value is identical across every level of an axis that is
    supposed to matter -> "is this axis being ignored?" (e.g. MethodA identical across
    all knowledge settings). This is the RIGHT home for legitimate constant-across-axis
    repeats, so they are NOT reported as copy/paste.

    DUPLICATE (copy/paste): the SAME specific value appears for the SAME entity at two
    levels of an axis along which that entity OTHERWISE varies. Example: MethodA 'p_1
    known' = 379.180 at BOTH the 5-day and 10-day horizons, even though MethodA's value
    changes across horizon elsewhere. That is the true copy/paste signal, and it does not
    fire on MethodB being naturally constant across knowledge."""
    flags = []
    # ---- flatness first; remember which (entity, axis) pairs are 'ignored' ----
    ignored = set()  # (entity_value, axis) that are legitimately constant
    if t.entity_axis:
        ent = t.entity_axis
        for ax_name in t.axes:
            if ax_name == ent or len(t.levels(ax_name)) < 2:
                continue
            non = [a for a in t.axes if a not in (ent, ax_name)]
            level_lists = [t.levels(a) for a in non]
            for e in t.levels(ent):
                all_const = True; any_series = False
                for combo in (product(*level_lists) if level_lists else [()]):
                    fixed = dict(zip(non, combo)); fixed[ent] = e
                    vals = [t.value_at(**{ax_name: lv}, **fixed) for lv in t.levels(ax_name)]
                    vals = [v for v in vals if is_num(v)]
                    if len(vals) >= 2:
                        any_series = True
                        if len(set(round(v, 6) for v in vals)) != 1:
                            all_const = False
                if any_series and all_const:
                    ignored.add((e, ax_name))
                    # report flatness once (representative)
                    flags.append(Flag(
                        type="flatness", table=t.name, axis=ax_name, coords={ent: e},
                        detail={"note": f"{e} constant across all {ax_name} levels"},
                        strength=1.0, support=1.0,
                        suggestion=f"{e} is identical across all {ax_name} settings — is {ax_name} being ignored?",
                    ))

    # ---- duplicates: per entity, per axis it otherwise varies on ----
    if t.entity_axis:
        ent = t.entity_axis
        for ax_name in t.axes:
            if ax_name == ent:
                continue
            non = [a for a in t.axes if a not in (ent, ax_name)]
            level_lists = [t.levels(a) for a in non]
            for e in t.levels(ent):
                if (e, ax_name) in ignored:
                    continue  # this axis legitimately doesn't affect e -> not copy/paste
                for combo in (product(*level_lists) if level_lists else [()]):
                    fixed = dict(zip(non, combo)); fixed[ent] = e
                    seen = {}
                    for lv in t.levels(ax_name):
                        v = t.value_at(**{ax_name: lv}, **fixed)
                        if not is_num(v):
                            continue
                        dec = len(f"{v}".split(".")[1]) if "." in f"{v}" else 0
                        if dec < min_decimals:
                            continue
                        seen.setdefault(round(v, 6), []).append(lv)
                    for val, lvs in seen.items():
                        if len(lvs) >= 2:
                            flags.append(Flag(
                                type="duplicate", table=t.name, axis=ax_name,
                                coords={ent: e, **{k: v for k, v in fixed.items() if k != ent}},
                                detail={"value": val, "levels": lvs},
                                strength=float(len(lvs)), support=1.0,
                                suggestion=f"{e} has identical value {val} at {ax_name}={lvs} "
                                           f"(but varies elsewhere) — check for copy/paste.",
                            ))
    return flags


ALL_DETECTORS = [
    detect_numeric_jumps, detect_win_reversals,
    detect_monotonicity, detect_cat_exceptions, detect_duplicates_and_flatness,
]


# ---------------------------------------------------------------- T6
def detect_level_shifts(t: TidyTable, baseline_frac=0.30, min_baseline=5,
                        z_thresh=3.0, persist=3):
    """Flag a SUSTAINED departure from an early stable baseline (a regime/level shift).

    This is the ozone-hole pattern: a long-stable series (baseline mean +/- spread)
    that drifts to a new level and STAYS there. Single sharp jumps are T1's job; this
    catches slow, persistent shifts that no single step reveals. For each numeric axis,
    the first `baseline_frac` of the ordered series defines the baseline; later points
    that sit > z_thresh baseline-SDs away for >= `persist` consecutive steps are flagged.
    """
    from statistics import mean, pstdev
    flags = []
    for ax_name, ax in t.axes.items():
        if ax.kind != "numeric":
            continue
        levels = ax.sorted_levels(t.levels(ax_name))
        if len(levels) < min_baseline + persist:
            continue
        # build per-entity (or single) series along this axis
        others = [a for a in t.axes if a != ax_name]
        from itertools import product as _p
        level_lists = [t.levels(a) for a in others]
        for combo in (_p(*level_lists) if level_lists else [()]):
            fixed = dict(zip(others, combo))
            series = [(lv, t.value_at(**{ax_name: lv}, **fixed)) for lv in levels]
            pts = [(lv, v) for lv, v in series if is_num(v)]
            if len(pts) < min_baseline + persist:
                continue
            nb = max(min_baseline, int(len(pts) * baseline_frac))
            base_pts = pts[:nb]
            base_vals = [v for _, v in base_pts]
            mu = mean(base_vals); sd = pstdev(base_vals) or (abs(mu) * 0.05) or 1.0

            # TREND GUARD: if the baseline is itself trending (not flat), fit its linear
            # trend and judge later points against the EXTRAPOLATED trend, not a flat mean.
            # A steady monotonic rise (e.g. CO2) must NOT be called a 'shift'.
            xs = [float(_x(lv)) for lv, _ in base_pts]
            slope, intercept, resid_sd = _lin_fit(xs, base_vals)
            # TREND vs FLAT: use R^2 of the baseline linear fit. A genuine trend (CO2) is a
            # near-perfect line (high R^2); a flat-but-noisy baseline (ozone) has low R^2 even
            # if it has a slight slope. Raw slope alone is too sensitive.
            base_var = pstdev(base_vals) ** 2
            r2 = 0.0 if base_var == 0 else max(0.0, 1.0 - (resid_sd ** 2) / base_var)
            baseline_is_trend = (r2 >= 0.6) and (abs(slope) * (xs[-1] - xs[0]) > 2 * (resid_sd or 1e-9)) if len(xs) > 1 else False
            # spread to compare against = residual SD around the (possibly sloped) baseline
            ref_sd = resid_sd or sd

            def predict(lv):
                try:
                    return intercept + slope * float(_x(lv))
                except Exception:
                    return mu

            run = []; best_run = []
            for lv, v in pts[nb:]:
                expected = predict(lv) if baseline_is_trend else mu
                if abs(v - expected) > z_thresh * ref_sd:
                    run.append((lv, v))
                    if len(run) > len(best_run):
                        best_run = run[:]
                else:
                    run = []
            if len(best_run) >= persist:
                shifted_mean = mean([v for _, v in best_run])
                ref = predict(best_run[len(best_run)//2]) if baseline_is_trend else mu
                z = abs(shifted_mean - ref) / ref_sd
                # Confidence: a shift off a FLAT baseline is a strong signal (ozone).
                # A "shift" off a TRENDING baseline is often just curvature/acceleration
                # (CO2) -> low-confidence candidate, not a HIGH alarm. Encode via `support`
                # and a distinct wording so the scorer can down-rank it.
                if baseline_is_trend:
                    conf = "candidate"; supp = 0.4
                    kind = "departs from its established trend"
                else:
                    conf = "clear"; supp = 1.0
                    kind = f"holds ~{mu:.0f}"
                flags.append(Flag(
                    type="level_shift", table=t.name, axis=ax_name, coords=fixed,
                    detail={"baseline_mean": round(mu, 2), "baseline_sd": round(ref_sd, 2),
                            "baseline_span": [pts[0][0], pts[nb - 1][0]],
                            "baseline_trend": bool(baseline_is_trend), "confidence": conf,
                            "shifted_mean": round(shifted_mean, 2),
                            "shift_span": [best_run[0][0], best_run[-1][0]],
                            "n_persist": len(best_run), "z": round(z, 1)},
                    strength=z, support=supp,
                    suggestion=(f"{t.metric_name} {kind} (baseline "
                                f"{pts[0][0]}-{pts[nb-1][0]}) then sits ~{shifted_mean:.0f} "
                                f"from {best_run[0][0]} onward ({z:.0f} SDs, {len(best_run)} points)"
                                + (" — likely just a continuing/accelerating trend; low confidence."
                                   if baseline_is_trend else
                                   " — sustained departure from a stable baseline; worth investigating.")),
                ))
    return flags


def _x(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def _logspace_if_wide(ys):
    """If a column's positive values span many orders of magnitude (e.g. dataset sizes,
    counts, runtimes: 10 ... 1,000,000), a value 100x larger is NOT an outlier — the data
    naturally covers decades. In that case compare in log space so only values that break
    the log-scale pattern are flagged. Returns (transformed_ys, is_log). Leaves data
    unchanged when it is not wide-span or has non-positive values."""
    import math
    pos = [y for y in ys if isinstance(y, (int, float)) and y > 0]
    if len(pos) < len(ys) or len(pos) < 3:
        return ys, False
    span = math.log10(max(pos) / min(pos)) if min(pos) > 0 else 0.0
    if span >= 2.0:   # >= 2 orders of magnitude
        return [math.log10(y) for y in ys], True
    return ys, False


def _lin_fit(xs, ys):
    """Simple least-squares slope/intercept + residual SD. Returns (0,mean,sd) if degenerate."""
    from statistics import mean, pstdev
    n = len(xs)
    if n < 2 or len(set(xs)) < 2:
        return 0.0, (mean(ys) if ys else 0.0), (pstdev(ys) if ys else 0.0)
    mx = mean(xs); my = mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    return slope, intercept, (pstdev(resid) if len(resid) > 1 else 0.0)


ALL_DETECTORS.append(detect_level_shifts)


# ---------------------------------------------------------------- T7
def detect_point_outliers(t: TidyTable, z_thresh=3.5, min_points=5):
    """Flag a SINGLE point that sits far from its peers (the advisor's 'one against the majority').

    Two modes along each numeric axis (per entity/held-fixed slice):
      (a) trend outlier: fit a robust line, flag a point whose residual is a large outlier
          (> z_thresh robust-SDs) while the rest fit well  -> Anscombe III.
      (b) leverage/level outlier: if the axis is nearly constant except one point, or one
          y sits far from the median of the rest -> Anscombe IV / lone deviant.
    Only fires when exactly a FEW points (<=2) are outliers, i.e. a genuine minority."""
    from statistics import median, pstdev
    flags = []
    for ax_name, ax in t.axes.items():
        if ax.kind != "numeric":
            continue
        others = [a for a in t.axes if a != ax_name]
        from itertools import product as _p
        level_lists = [t.levels(a) for a in others]
        for combo in (_p(*level_lists) if level_lists else [()]):
            fixed = dict(zip(others, combo))
            # Use ALL raw observations matching `fixed` (not deduped axis levels): a covariate
            # axis like Anscombe's x can have many rows at the same x. value_at() would collapse
            # them, so read rows directly.
            pts = []
            for r in t.rows:
                if all(r.get(k) == v for k, v in fixed.items()) and is_num(r.get("value")) and is_num(r.get(ax_name)):
                    pts.append((float(r[ax_name]), r["value"]))
            pts.sort()
            if len(pts) < min_points:
                continue
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            # ---- leverage mode (Anscombe IV): axis nearly constant except one point ----
            # If almost all x share one value and a single point sits at a far-off x, that
            # lone point has extreme leverage (it alone determines any fitted slope). Flag it.
            xcount = {}
            for x in xs:
                xcount[x] = xcount.get(x, 0) + 1
            if len(xcount) == 2:
                common_x = max(xcount, key=xcount.get)
                rare = [x for x in xcount if x != common_x]
                if xcount[common_x] >= max(4, len(xs) - 1) and len(rare) == 1 and xcount[rare[0]] == 1:
                    rx = rare[0]
                    ri = xs.index(rx)
                    flags.append(Flag(
                        type="point_outlier", table=t.name, axis=ax_name, coords=fixed,
                        detail={"at": rx, "value": ys[ri], "mode": "leverage",
                                "common_axis_value": common_x, "n_at_common": xcount[common_x]},
                        strength=8.0, support=1.0,
                        suggestion=f"All {ax_name} values are {common_x:g} except one point at {ax_name}={rx:g} "
                                   f"({t.metric_name}={ys[ri]:g}); this lone high-leverage point alone drives "
                                   f"any trend — verify it.",
                    ))
                    continue  # leverage case handled; skip residual mode for this slice
            # DOMINANT-CLUSTER OUTLIER (check RAW values first, before any line fit): if most
            # values sit in a tight cluster near a common level and 1-2 sit far outside it, those
            # few are the obvious anomaly. This catches both a perfectly flat background with one
            # spike (thyroid feat2: 0,...,0,0.25) AND a near-flat background with a dominant spike
            # that decays (feat3/feat4: 0.24, 0.005, 0.003, 0, 0...). A linear fit would tilt and
            # hide these; MAD-scaling goes blind on near-constant data.
            srt = sorted(ys)
            # "background" = the majority (drop up to 2 extremes from each end), its spread + center
            core = srt[:len(srt) - 2] if len(srt) > 4 else srt
            core_lo, core_hi = min(core), max(core)
            core_center = median(core)
            core_spread = core_hi - core_lo
            data_range = max(ys) - min(ys)
            outside = []
            if data_range > 0:
                for i, v in enumerate(ys):
                    # far outside the core cluster: more than max(6*core_spread, 40% of range)
                    thresh = max(6 * core_spread, 0.4 * data_range)
                    if abs(v - core_center) > thresh and (v < core_lo or v > core_hi):
                        outside.append(i)
            if 1 <= len(outside) <= 2:
                for i in outside:
                    flags.append(Flag(
                        type="point_outlier", table=t.name, axis=ax_name, coords=fixed,
                        detail={"at": pts[i][0], "value": pts[i][1], "mode": "vs_cluster",
                                "majority_value": round(core_center, 4)},
                        strength=8.0, support=1.0,
                        suggestion=f"Single {t.metric_name} value {pts[i][1]:g} at {ax_name}={pts[i][0]:g} "
                                   f"stands out from the rest (which cluster near {core_center:g}) "
                                   f"— check it.",
                    ))
                continue
            # residuals from a linear fit (falls back to median if x is ~constant)
            if len(set(xs)) >= 2:
                slope, intercept, _ = _lin_fit(xs, ys)
                resid = [y - (intercept + slope * x) for x, y in pts]
                # TREND-ENDPOINT GUARD: if the data is a strong smooth trend (high R^2), the
                # largest residual is usually the newest point of an accelerating curve, NOT a
                # true outlier (e.g. latest-year CO2). Don't flag boundary points of a good-fit
                # trend. Only genuine INTERIOR spikes survive.
                base_var = (pstdev(ys) ** 2) if len(ys) > 1 else 0.0
                resid_var = (pstdev(resid) ** 2) if len(resid) > 1 else 0.0
                r2 = 0.0 if base_var == 0 else max(0.0, 1.0 - resid_var / base_var)
                is_trend = r2 >= 0.9
            else:
                med = median(ys); resid = [y - med for y in ys]
                is_trend = False
            # robust scale: MAD of residuals
            rmed = median(resid)
            mad = median([abs(r - rmed) for r in resid]) or 0.0
            scale = 1.4826 * mad
            if scale == 0:
                continue  # constant series already handled by the near-constant check above
            zs = [(abs(r - rmed) / scale, i) for i, r in enumerate(resid)]
            outliers = [(z, i) for z, i in zs if z > z_thresh]
            # On a strong smooth trend, drop outliers that are the FIRST/LAST point (endpoints
            # of an accelerating curve are expected, not anomalous — e.g. latest-year CO2).
            if is_trend:
                outliers = [(z, i) for z, i in outliers if 0 < i < len(pts) - 1]
            if 1 <= len(outliers) <= 2:  # a genuine minority
                for z, i in outliers:
                    lv = t.levels(ax_name)[i] if i < len(t.levels(ax_name)) else pts[i][0]
                    flags.append(Flag(
                        type="point_outlier", table=t.name, axis=ax_name, coords=fixed,
                        detail={"at": pts[i][0], "value": pts[i][1], "z": round(z, 1),
                                "peers_median": round(median(ys), 3)},
                        strength=z, support=1.0,
                        suggestion=f"Single {t.metric_name} value {pts[i][1]:g} at {ax_name}={pts[i][0]:g} "
                                   f"is far from the rest ({z:.0f} robust-SDs) — lone outlier, check it.",
                    ))

    # ---- categorical mode: an ablation-style table (one metric value per category) ----
    # A results table like (setting -> accuracy) has an UNORDERED categorical axis, so the
    # numeric modes above never run. But a lone bad value among the categories (e.g. one
    # ablation row at 0.30 while the rest are ~0.90) is exactly a 'one against the majority'
    # outlier. Check each unordered-categorical axis that carries a single value per level.
    for ax_name, ax in t.axes.items():
        if ax.kind != "unordered_cat":
            continue
        others = [a for a in t.axes if a != ax_name]
        from itertools import product as _p
        level_lists = [t.levels(a) for a in others]
        for combo in (_p(*level_lists) if level_lists else [()]):
            fixed = dict(zip(others, combo))
            levels = t.levels(ax_name)
            pairs = [(lv, t.value_at(**{ax_name: lv}, **fixed)) for lv in levels]
            pairs = [(lv, v) for lv, v in pairs if is_num(v)]
            if len(pairs) < min_points:
                continue
            ys = [v for _, v in pairs]
            # If the column spans many orders of magnitude (dataset sizes, counts), decide
            # outlier status in log space so naturally decade-spanning columns are not flagged.
            ys_eval, is_log = _logspace_if_wide(ys)
            data_range = max(ys_eval) - min(ys_eval)
            if data_range == 0:
                continue
            srt = sorted(ys_eval)
            # the "background" cluster = the middle, trimming up to one extreme from EACH end
            # (the outlier may be the smallest OR the largest value, so trim both sides).
            trim = 1 if len(srt) >= 5 else 0
            core = srt[trim:len(srt) - trim] if trim else srt
            core_lo, core_hi = min(core), max(core)
            core_center = median(core)
            core_spread = core_hi - core_lo
            thresh = max(6 * core_spread, 0.4 * data_range)
            outside = [i for i, ev in enumerate(ys_eval)
                       if abs(ev - core_center) > thresh and (ev < core_lo or ev > core_hi)]
            if 1 <= len(outside) <= 2:
                for i in outside:
                    lv, v = pairs[i]
                    flags.append(Flag(
                        type="point_outlier", table=t.name, axis=ax_name, coords=fixed,
                        detail={"at": lv, "value": v, "mode": "categorical",
                                "log_scale": is_log},
                        strength=8.0, support=1.0,
                        suggestion=f"Single {t.metric_name} value {v:g} at {ax_name}='{lv}' "
                                   f"stands out from the rest — check it.",
                    ))
    return flags


ALL_DETECTORS.append(detect_point_outliers)


# ---------------------------------------------------------------- T8
def detect_trends(t: TidyTable, r2_min=0.5, min_points=6, index_axes=None, expect_flat=None):
    """Flag a statistically significant MONOTONIC TREND along an ordered axis.

    Motivation (1970 draft lottery): the monthly mean draft rank steadily DECLINES across
    the year, when a fair lottery should be flat/random. No single jump or level-shift
    captures 'the whole series slopes when it shouldn't.'

    IMPORTANT honesty point: a strong trend is only an *anomaly* if the axis is EXPECTED to
    be flat/random. For a genuine input->output relationship (Anscombe x->y) or a naturally
    trending series (CO2 over time), a trend is normal, not a problem. The tool cannot know
    which is which, so by default a trend is a LOW-confidence *candidate* worded 'if X should
    be flat/random, investigate.' If the caller marks an axis in `expect_flat`, a strong trend
    there becomes a real HIGH/MEDIUM finding.
    """
    from statistics import pstdev
    index_axes = set(index_axes or [])   # trends ARE allowed on index axes
    expect_flat = set(expect_flat or [])
    flags = []
    for ax_name, ax in t.axes.items():
        if ax.kind not in ("numeric", "ordered_cat"):
            continue
        levels = ax.sorted_levels(t.levels(ax_name))
        if len(levels) < min_points:
            continue
        others = [a for a in t.axes if a != ax_name]
        from itertools import product as _p
        level_lists = [t.levels(a) for a in others]
        for combo in (_p(*level_lists) if level_lists else [()]):
            fixed = dict(zip(others, combo))
            # position along the ordered axis: numeric value, else rank in the order
            pts = []
            for i, lv in enumerate(levels):
                v = t.value_at(**{ax_name: lv}, **fixed)
                if is_num(v):
                    x = float(_x(lv)) if ax.kind == "numeric" else float(i)
                    pts.append((x, v, lv))
            if len(pts) < min_points:
                continue
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            slope, intercept, resid_sd = _lin_fit(xs, ys)
            var = pstdev(ys) ** 2 if len(ys) > 1 else 0.0
            if var == 0:
                continue
            r2 = max(0.0, 1.0 - (resid_sd ** 2) / var)
            total_change = slope * (xs[-1] - xs[0])
            # require a strong fit AND a change that spans a real fraction of the value range
            rng = max(ys) - min(ys) or 1.0
            if r2 >= r2_min and abs(total_change) > 0.5 * rng:
                direction = "declines" if slope < 0 else "rises"
                is_expected_flat = ax_name in expect_flat
                # support drives priority: expected-flat trend = real finding; otherwise a
                # low-confidence candidate (the tool can't know if the trend is expected).
                supp = 1.0 if is_expected_flat else 0.3
                tail = (f"but {ax_name} is expected to be flat/random — investigate."
                        if is_expected_flat else
                        f"if {ax_name} should be flat/random, investigate (otherwise this is "
                        f"just the expected relationship).")
                flags.append(Flag(
                    type="trend", table=t.name, axis=ax_name, coords=fixed,
                    detail={"slope": round(slope, 3), "r2": round(r2, 3),
                            "from": pts[0][2], "to": pts[-1][2],
                            "start_value": round(ys[0], 2), "end_value": round(ys[-1], 2),
                            "expected_flat": is_expected_flat},
                    strength=r2,
                    support=supp,
                    suggestion=f"{t.metric_name} steadily {direction} along {ax_name} "
                               f"(~{ys[0]:.0f} to ~{ys[-1]:.0f}, R²={r2:.2f}); " + tail,
                ))
    return flags


ALL_DETECTORS.append(detect_trends)
