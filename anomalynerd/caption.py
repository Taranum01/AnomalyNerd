"""Caption generation for AnomalyNerd.

Because detecting anomalies already means detecting PATTERNS, the same computed
facts can be turned into a caption describing the table. The caption is GROUNDED: it states
only what the schema and the detectors actually found (metric, axes, trends, best method,
level shifts, outliers), never invented context. The user chooses how much caption they want.

A caption is therefore a deterministic rendering of:
    - the table schema (what is measured, against which input axes), and
    - the ranked detected patterns (what is notable about the numbers),
assembled up to a requested detail level. No LLM is required; phrasing is template-based so
the caption can never assert anything the tool did not compute.

Length knob:
    "short"    - one sentence: what the table reports (metric x axes).
    "medium"   - short + the single most salient pattern.
    "detailed" - medium + secondary patterns and the specific flagged entries.
A target word budget can also be given; the longest level under the budget is used.
"""
from __future__ import annotations
from .model import TidyTable, Flag
from .analyze import analyze


# ----------------------------------------------------------------- phrasing per pattern
def _fmt_axislist(axes, metric):
    inputs = [a for a in axes if a != "value"]
    if not inputs:
        return f"{metric}"
    if len(inputs) == 1:
        return f"{metric} across {inputs[0]}"
    return f"{metric} across " + ", ".join(inputs[:-1]) + f" and {inputs[-1]}"


def _pattern_phrase(f: Flag, t: TidyTable) -> str:
    """One grounded clause describing a single detected pattern, from its real detail values."""
    d = f.detail or {}
    m = t.metric_name
    ax = f.axis
    if f.type == "trend":
        direction = "increases" if d.get("slope", 0) > 0 else "decreases"
        return (f"{m} {direction} with {ax} "
                f"(from {d.get('start_value', '?')} to {d.get('end_value', '?')}, "
                f"$R^2={d.get('r2', '?')}$)")
    if f.type == "level_shift":
        bm = d.get("baseline_mean"); span = d.get("baseline_span")
        return (f"{m} holds near {bm} over {ax}={span[0]:g}--{span[1]:g} and then shifts to a "
                f"new level" if (bm is not None and span) else f"{m} shows a level shift along {ax}")
    if f.type == "numeric_jump":
        at = d.get("at"); return f"{m} jumps sharply near {ax}={at}" if at is not None else f"{m} has a sharp jump along {ax}"
    if f.type == "win_reversal":
        winners = d.get("winners") or d.get("sequence")
        return (f"the best {t.entity_axis or 'method'} changes along {ax}"
                + (f" (order: {', '.join(map(str, winners))})" if winners else ""))
    if f.type == "monotonicity":
        return f"{m} reverses its expected direction along {ax}"
    if f.type == "cat_exception":
        return f"the usual ordering along {ax} has an exception"
    if f.type == "point_outlier":
        at = d.get("at"); v = d.get("value")
        where = f" at {ax}={at}" if at is not None else ""
        return f"one {m} value ({v}){where} stands out from the rest" if v is not None else f"a lone {m} outlier{where}"
    if f.type in ("duplicate", "flatness"):
        return (f"{m} is identical across {ax} (the axis appears to have no effect)"
                if f.type == "flatness" else f"a repeated {m} value suggests a possible copy")
    return f"a {f.type.replace('_', ' ')} along {ax}"


# ----------------------------------------------------------------- caption assembly
_LEVELS = ("short", "medium", "detailed")


def generate_caption(t: TidyTable, flags=None, level: str = "medium",
                     word_budget: int | None = None) -> str:
    """Build a grounded caption for a tidy table at the requested detail level.

    `flags` may be passed in (the output of analyze); if omitted they are computed. Only
    HIGH/MEDIUM patterns are described in the caption body; LOW-confidence candidates are
    not asserted. Returns a plain-text caption."""
    if flags is None:
        flags = analyze(t)
    if level not in _LEVELS:
        level = "medium"

    metric = t.metric_name
    # the "what" sentence (always present)
    subject = _fmt_axislist(list(t.axes), metric)
    entity = t.entity_axis
    if entity:
        others = [a for a in t.axes if a not in ("value", entity)]
        axpart = (" across " + ", ".join(others)) if others else ""
        base = f"{metric} for each {entity}{axpart}."
    else:
        base = f"{subject.capitalize()}."

    salient = [f for f in flags if f.priority in ("HIGH", "MEDIUM")]
    # A caption should describe the table's PATTERN. Flatness/duplicate flags are
    # data-quality notes about an auxiliary column (e.g. a constant uncertainty column),
    # not a pattern in the measured metric, so they are not used to drive the caption's
    # main clause; they only appear in the detailed "Also:" tail if nothing else is there.
    pattern_kinds = ("trend", "level_shift", "numeric_jump", "win_reversal",
                     "monotonicity", "cat_exception", "point_outlier")
    main = [f for f in salient if f.type in pattern_kinds]
    aux = [f for f in salient if f.type not in pattern_kinds]
    # order: HIGH before MEDIUM, then by strength
    main.sort(key=lambda f: (0 if f.priority == "HIGH" else 1, -float(f.strength or 0)))
    salient = main  # the caption describes substantive patterns only

    def _assemble(lvl):
        parts = [base]
        if lvl == "short":
            return base
        if not salient:
            return base + " No notable pattern is flagged."
        # medium: the single most salient pattern
        parts.append(_pattern_phrase(salient[0], t).capitalize() + ".")
        if lvl == "detailed" and len(salient) > 1:
            extra = "; ".join(_pattern_phrase(f, t) for f in salient[1:4])
            parts.append("Also: " + extra + ".")
        return " ".join(parts)

    if word_budget is not None:
        # pick the richest level that fits the budget
        chosen = base
        for lvl in _LEVELS:
            cand = _assemble(lvl)
            if len(cand.split()) <= word_budget:
                chosen = cand
            else:
                break
        return chosen
    return _assemble(level)
