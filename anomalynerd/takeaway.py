"""Suggested takeaway for a table: what a reader could conclude, in plain words.

Like the caption, it is built only from what the tool computed, never invented. It says up to
three things:

1. who comes out on top, and whether they win everywhere or only in some settings;
2. what matters: the clearest trend or level shift along a numeric setting;
3. what to check: values the tool flagged, so a takeaway never hides a possible error.

"Best" is only used when the metric's name says which direction is better (accuracy, loss,
BLEU, an arrow...); otherwise the takeaway says "highest". It is a suggestion: an author may
draw a different conclusion from the same numbers.
"""
from __future__ import annotations

from collections import Counter
from itertools import product

from .analyze import analyze, is_pathological
from .ingest import metric_direction
from .model import TidyTable, is_num

_MAX_SETTINGS = 500   # a table with more settings than this is not summarised as a contest
LABEL = "Suggested takeaway (computed from the table; the authors may conclude differently)"
_CHECK_KINDS = ("point_outlier", "numeric_jump", "duplicate", "unit_mismatch", "monotonicity",
                "cat_exception", "level_shift", "flatness", "cross_table_inconsistency")


def _fmt(v):
    return f"{v:.4g}" if isinstance(v, (int, float)) else str(v)


_TASK_ROWS = __import__("re").compile(r"benchmark|dataset|data set|task|corpus|language", __import__("re").I)


def _short(name, n=40):
    name = str(name)
    return name if len(name) <= n else name[:n].rstrip() + "..."


def _is_name(label) -> bool:
    """A competitor's label must contain a letter: '47.1±.1' is a misread number, not a name."""
    return any(ch.isalpha() for ch in str(label))


def _axis_word(name):
    """How to call an axis in a sentence: unnamed axes ('col0') are just 'row'."""
    import re
    return "row" if re.fullmatch(r"col\d+", str(name)) else str(name)


def _count_phrase(k, n, unit):
    return f"both {unit}" if (k == n == 2) else (f"all {n} {unit}" if k == n else f"{k} of {n} {unit}")


def _competitors(t: TidyTable):
    """(axis whose levels compete, list of axes that define one setting), or None.

    A wide table read from a paper (entity axis 'method' made from the column headers) usually
    has systems as rows and tasks or metrics as columns, so its row labels compete within each
    column. Otherwise the entity axis competes within each combination of the other axes."""
    ent = t.entity_axis
    if not ent or ent not in t.axes:
        return None
    others = [a for a in t.axes if a != ent]
    if ent == "method" and t.metric_name == "value":
        rows = [a for a in others if t.axes[a].kind != "numeric"]
        if len(rows) == 1:
            if _TASK_ROWS.search(str(rows[0])):
                return None   # rows are benchmarks or datasets, columns the systems: unclear
            return rows[0], [ent] + [a for a in others if a != rows[0]]
        if rows:
            return None   # unclear which labels are the competitors: say nothing
    return ent, others


def _winners(t: TidyTable, lower_better: bool):
    """For each setting with at least two scored competitors: (setting, best, best value,
    runner-up, runner-up value)."""
    comp = _competitors(t)
    if comp is None or is_pathological(t):
        return None, []
    who, setting_axes = comp
    n_settings = 1
    for a in setting_axes:
        n_settings *= max(1, len(t.levels(a)))
    if n_settings > _MAX_SETTINGS:
        return who, []
    # each competitor must have one value per setting; repeated coordinates (raw observations
    # rather than a results table) have no single value to compare
    keys = Counter(tuple(r.get(a) for a in [who] + setting_axes) for r in t.rows if is_num(r.get("value")))
    if any(c > 1 for c in keys.values()):
        return who, []
    # competitors must be the same kind of quantity: values that span more than 1000x in one
    # setting (a hyperparameter table: learning rate next to step counts) mix quantities, so
    # nothing "wins"
    for combo in product(*[t.levels(a) for a in setting_axes]) if setting_axes else [()]:
        fixed = dict(zip(setting_axes, combo))
        vals = [t.value_at(**{who: e}, **fixed) for e in t.levels(who)]
        vals = [abs(v) for v in vals if is_num(v) and v != 0]
        if len(vals) >= 2 and max(vals) / min(vals) > 1000:
            return who, []
    out = []
    for combo in product(*[t.levels(a) for a in setting_axes]) if setting_axes else [()]:
        fixed = dict(zip(setting_axes, combo))
        scored = [(e, t.value_at(**{who: e}, **fixed)) for e in t.levels(who)]
        scored = [(e, v) for e, v in scored if is_num(v) and _is_name(e)]
        if len(scored) < 2:
            continue
        scored.sort(key=lambda x: x[1], reverse=not lower_better)
        if scored[0][1] == scored[1][1]:
            continue  # a tie has no single winner
        out.append((fixed, scored[0][0], scored[0][1], scored[1][0], scored[1][1]))
    return who, out


def _who_wins(t: TidyTable, direction):
    lower = bool(direction) if direction is not None else False
    word = "best" if direction is not None else "highest"
    who, wins = _winners(t, lower)
    if not wins:
        return None
    metric = "" if t.metric_name == "value" else f" {_short(t.metric_name)}"
    unit = "columns" if who != t.entity_axis else "settings"
    if len(wins) == 1:
        _, best, bv, second, sv = wins[0]
        return f"{best} has the {word}{metric or ' value'} ({_fmt(bv)}), ahead of {second} ({_fmt(sv)})."
    count = Counter(w[1] for w in wins)
    n = len(wins)
    top, k = count.most_common(1)[0]
    if k * 2 > n:
        return f"{top} has the {word}{metric or ' value'} in {_count_phrase(k, n, unit)}."
    if n < 3:
        return None   # a 1-1 split says nothing
    leaders = ", ".join(f"{e} in {c}" for e, c in count.most_common(3))
    return f"No single {_axis_word(who)} leads: {leaders} of the {n} {unit}."


def _subject(f, t):
    """What a flag is about. A wide CSV is melted into one 'value' metric with the original
    column as the 'method' coordinate, so the flag's own column names the subject."""
    if t.metric_name == "value" and (f.coords or {}).get("method") and t.entity_axis == "method":
        return str(f.coords["method"])
    return t.metric_name


def _what_matters(t: TidyTable, flags):
    trends = [f for f in flags if f.type == "trend"]
    flat = [f for f in trends if (f.detail or {}).get("expected_flat")]
    if flat:
        f = max(flat, key=lambda f: f.strength or 0)
        d = f.detail
        verb = "falls" if (d.get("slope") or 0) < 0 else "rises"
        return (f"{_subject(f, t)} {verb} as {f.axis} goes up (from {_fmt(d.get('start_value'))} "
                f"to {_fmt(d.get('end_value'))}), although {f.axis} was expected not to matter.")
    if trends:
        f = max(trends, key=lambda f: f.strength or 0)
        d = f.detail
        verb = "falls" if (d.get("slope") or 0) < 0 else "rises"
        return (f"{_subject(f, t)} {verb} as {f.axis} goes up, from {_fmt(d.get('start_value'))} "
                f"to {_fmt(d.get('end_value'))}.")
    shifts = [f for f in flags if f.type == "level_shift"]
    if shifts:
        f = max(shifts, key=lambda f: ({"HIGH": 2, "MEDIUM": 1}.get(f.priority, 0), f.strength or 0))
        d = f.detail or {}
        span = d.get("shift_span") or []
        where = f" from {f.axis}={_fmt(span[0])} on" if span else f" along {f.axis}"
        level = (f" (about {_fmt(d.get('baseline_mean'))} before, {_fmt(d.get('shifted_mean'))} after)"
                 if d.get("baseline_mean") is not None and d.get("shifted_mean") is not None else "")
        b, a = d.get("baseline_mean"), d.get("shifted_mean")
        if f.priority not in ("HIGH", "MEDIUM") and not (
                b and a is not None and abs(a - b) >= 0.1 * abs(b)):
            return None   # a small, low-confidence shift is not worth a takeaway
        hedge = "" if f.priority in ("HIGH", "MEDIUM") else " The tool is not confident this is a shift rather than a steep trend."
        return f"{_subject(f, t)} moves to a new level{where}{level}.{hedge}"
    return None


def _plain_phrase(f, t):
    """The caption's phrase for a flag, with its location but without internal fields."""
    from .caption import _pattern_phrase_unscoped
    if f.type == "unit_mismatch" and f.suggestion:
        return f.suggestion.rstrip(".")
    subj = _subject(f, t)
    named = subj != t.metric_name
    scope = ", ".join(f"{_short(k, 30)}={_short(_fmt(v), 30)}" for k, v in (f.coords or {}).items()
                      if not str(k).startswith("_") and str(v).strip() and not (named and k == "method"))
    if named:
        import copy
        t = copy.copy(t)
        t.metric_name = subj
    phrase = _pattern_phrase_unscoped(f, t)
    if t.metric_name:
        phrase = phrase.replace(t.metric_name, _short(t.metric_name))
    phrase = phrase.replace("value value", "value")
    return f"{phrase} ({scope})" if scope else phrase


# Whole words that mark a model or cost column. A word matches when it equals one of these
# (optionally plural) or starts with one of the stems, so "Params (M)", "GPU hours" and "FLOPs"
# are settings but "Sentiment" (contains "time"), "Headline" (contains "head") and "Timeline"
# are results.
_COST_WORDS = ("size", "time", "step", "hardware", "batch", "gpu", "tpu", "token", "layer", "dim",
               "head", "lr", "epoch", "hour", "second", "minute")
_COST_STEMS = ("flop", "param", "latenc", "throughput", "memor", "speed", "hyperparam",
               "dimension")


def _is_setting_column(name):
    """A column that describes the model or its cost (FLOPs, parameters, time, d_model, hidden
    size) rather than its result, so it is left out of 'who comes out on top'."""
    import re
    low = (name or "").lower()
    if re.fullmatch(r"d[\s_]?[a-z]{1,5}|hidden|width|depth|n|k|h", low.strip()):
        return True   # dimension-style names: d_model, dk, dff, hidden
    if "#" in low or "learning rate" in low:
        return True
    for word in re.findall(r"[a-z]+", low):
        if word.startswith(_COST_STEMS) or any(word in (w, w + "s", w + "es") for w in _COST_WORDS):
            return True
    return False


def _what_to_check(t: TidyTable, flags):
    hits = [f for f in flags if f.type in _CHECK_KINDS and f.priority in ("HIGH", "MEDIUM")
            and not (f.type == "level_shift")]
    if not hits:
        return None
    first = _plain_phrase(hits[0], t)
    if len(hits) == 1:
        return f"Before relying on it, check this: {first}."
    return f"Before relying on it, check the {len(hits)} flagged values, for example: {first}."


def generate_takeaway(t: TidyTable, flags=None, direction_known=None) -> list:
    """Up to three plain sentences a reader could take away from the table.

    flags: the output of analyze (computed if omitted). direction_known: True when the
    caller set the metric direction explicitly (for example --lower-better); otherwise it is
    read from the metric's name."""
    if flags is None:
        flags = analyze(t)
    direction = metric_direction(t.metric_name)
    if direction is None and direction_known:
        direction = t.lower_is_better
    parts = [_who_wins(t, direction), _what_matters(t, flags), _what_to_check(t, flags)]
    return [p for p in parts if p]


def _column_label(td, repeated):
    """A result column's name. Side-by-side groups that reuse a name (each has a '%Doc'
    column) are labelled by their group instead, so the sentence stays readable."""
    import re
    m = re.search(r"\[([^\]]+)\]$", td.name or "")
    if repeated and m:
        return _short(m.group(1))
    return _short(td.metric_name)


def table_takeaway(tidies, flags_list):
    """Takeaway for one table read from a paper. A wide table split into one tidy table per
    column is summarised once: which row leads across the result columns, the clearest trend,
    and how many values were flagged."""
    if len(tidies) == 1:
        td = tidies[0]
        if _is_setting_column(td.metric_name):
            return [p for p in (_what_matters(td, flags_list[0]), _what_to_check(td, flags_list[0])) if p]
        return generate_takeaway(td, flags_list[0])
    leads, words = [], set()
    names = [td.metric_name for td in tidies]
    repeated = len(set(names)) < len(names)
    for td, fl in zip(tidies, flags_list):
        if _is_setting_column(td.metric_name):
            continue
        direction = metric_direction(td.metric_name)
        who, wins = _winners(td, bool(direction))
        if len(wins) != 1:
            continue
        leads.append((_column_label(td, repeated), wins[0][1]))
        words.add("best" if direction is not None else "highest")
    parts = []
    if len(leads) >= 2:
        word = "best" if words == {"best"} else "highest"
        count = Counter(w for _, w in leads)
        top, k = count.most_common(1)[0]
        cols = [m for m, w in leads if w == top]
        shown = ", ".join(cols[:4]) + (", ..." if len(cols) > 4 else "")
        n = len(leads)
        rest = "; ".join(f"{w} in {', '.join(m for m, x in leads if x == w)}"
                         for w, _ in count.most_common() if w != top)
        if k == n:
            parts.append(f"{top} has the {word} value in {_count_phrase(k, n, 'result columns')} ({shown}).")
        elif k * 2 > n:
            parts.append(f"{top} has the {word} value in {k} of {n} result columns ({shown}); "
                         f"{rest}.")
        elif n >= 3:
            leaders = "; ".join(f"{w} in {', '.join(m for m, x in leads if x == w)}"
                                for w, _ in count.most_common(3))
            parts.append(f"No single row leads every column: {leaders}.")
    elif len(leads) == 1:
        td = next(td for td in tidies if _column_label(td, repeated) == leads[0][0])
        one = _who_wins(td, metric_direction(td.metric_name))
        if one:
            parts.append(one)
    matters = [m for m in (_what_matters(td, fl) for td, fl in zip(tidies, flags_list)) if m]
    if matters:
        parts.append(matters[0])
    hits = [(td, f) for td, fl in zip(tidies, flags_list) for f in fl
            if f.type in _CHECK_KINDS and f.type != "level_shift" and f.priority in ("HIGH", "MEDIUM")]
    if hits:
        td, f = hits[0]
        first = _plain_phrase(f, td)
        parts.append(f"Before relying on it, check this: {first}." if len(hits) == 1 else
                     f"Before relying on it, check the {len(hits)} flagged values, for example: {first}.")
    return parts
