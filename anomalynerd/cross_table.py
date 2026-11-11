"""Cross-table consistency: the same metric reported at the same coordinates in two
different tables of one paper should agree, up to the rounding implied by how many
decimals were reported.

A paper that reports MethodA's RMSE as 1.61 in Table 2 and 1.83 in Table 5 has an
internal contradiction worth a look. Detectors T1..T5 work inside one table; this check
works ACROSS the tables of a single source.

Design rules (same as the detectors):
- conservative matching: only tables whose axis sets are IDENTICAL and whose metric
  names match are compared, so different experimental conditions are never conflated
- rounding-aware: a difference is only flagged when it exceeds what the reported
  precision of BOTH cells could explain (half the last reported unit on each side)
- missing values are skipped, never treated as 0
- every flag is a SUGGESTION to look, never a verdict
"""
from __future__ import annotations
import math
import re
from .model import TidyTable, Flag, MISSING, is_num


def _norm_metric(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _precision_parts(token: str) -> tuple:
    """Mantissa decimal count and integer exponent, without float scaling."""
    m = re.match(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)", token)
    if not m:
        return 0, 0
    mant = m.group(0).lstrip("+-")
    decimals = len(mant.split(".")[1]) if "." in mant else 0
    exp = re.search(r"(?:[eE]|10\^\{?)([+-]?\d+)\}?", token[m.end():])
    exponent = int(exp.group(1)) if exp else 0
    return decimals, exponent


def _decimals_in(token: str) -> tuple:
    """Legacy (decimals, scale) helper; an unrepresentable scale is infinity."""
    decimals, exponent = _precision_parts(token)
    return decimals, float(f"1e{exponent}")


def _rounding_tolerance(raw, value) -> float:
    """Half the last reported unit, from the cell's original string. Unknown precision
    means no slack: values must then match within float noise."""
    if raw is None:
        return 0.0
    t = str(raw).strip()
    if not t:
        return 0.0
    decimals, exponent = _precision_parts(t)
    # Combine the powers before converting to float. A finite cell can have
    # an exponent above 308 balanced by a fractional mantissa. Separate scale
    # and precision floats would overflow or underflow before cancelling.
    tol = float(f"5e{exponent - decimals - 1}")
    # guard: a raw like '1.61 [0.55, 1.94]' still reports 2 decimals of the central value
    return tol


def _coord_key(row, axis_names):
    key = []
    for a in axis_names:
        v = row.get(a, MISSING)
        if isinstance(v, float):
            v = round(v, 9)      # float-noise normalisation for identical parsed coords
        key.append((a, v))
    return tuple(key)


def _cell_map(table: TidyTable, axis_names):
    """coords -> (value, raw). Rows with missing values are dropped; exact duplicate
    coordinates keep the FIRST cell (within-table duplicates are the duplicate
    detector's territory, not ours)."""
    cells = {}
    for row in table.rows:
        v = row.get("value", MISSING)
        if v is MISSING or not is_num(v):
            continue
        key = _coord_key(row, axis_names)
        if key not in cells:
            cells[key] = (v, row.get("_raw"))
    return cells


def find_cross_table_inconsistencies(tables) -> tuple:
    """Compare every pair of TidyTables from ONE source. Returns (flags, notes):
    flags are cross_table_inconsistency Flags; notes explain same-metric pairs that
    could not be compared (different table shapes), for the honesty section."""
    flags, notes = [], []
    usable = [t for t in tables if t is not None and t.rows]
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            a, b = usable[i], usable[j]
            if _norm_metric(a.metric_name) != _norm_metric(b.metric_name):
                continue
            axes_a = set(a.axes)
            axes_b = set(b.axes)
            if axes_a != axes_b:
                notes.append(
                    f"'{a.name}' vs '{b.name}': same metric '{a.metric_name}' but different "
                    f"axis sets ({sorted(axes_a)} vs {sorted(axes_b)}); not compared, so a "
                    f"contradiction between them would not be caught")
                continue
            axis_names = sorted(axes_a)
            ca, cb = _cell_map(a, axis_names), _cell_map(b, axis_names)
            shared = [k for k in ca if k in cb]
            if not shared:
                continue
            consistent, worst = 0, None
            for k in shared:
                va, ra = ca[k]
                vb, rb = cb[k]
                slack = _rounding_tolerance(ra, va) + _rounding_tolerance(rb, vb)
                gap = abs(va - vb)
                if gap <= max(slack * (1 + 1e-9), 1e-9 * max(abs(va), abs(vb))):
                    consistent += 1
                else:
                    # Known precision: strength in reporting steps. Unknown precision
                    # (no raw text): strength is the raw gap, so a 1e-7 float wobble
                    # cannot masquerade as a huge contradiction.
                    steps = gap / slack if slack > 0 else gap
                    if worst is None or steps > worst[0]:
                        worst = (steps, k, va, vb, ra, rb)
            if worst is None:
                continue
            steps, coords, va, vb, ra, rb = worst
            support = consistent / len(shared)
            coord_str = ", ".join(f"{name}={val}" for name, val in coords)
            flags.append(Flag(
                type="cross_table_inconsistency",
                table=f"{a.name} vs {b.name}",
                axis=None,
                coords=dict(coords),
                detail={
                    "metric": a.metric_name,
                    "value_in_first": va, "raw_in_first": ra,
                    "value_in_second": vb, "raw_in_second": rb,
                    "reporting_steps_apart": round(steps, 2),
                    "shared_cells": len(shared), "consistent_cells": consistent,
                },
                strength=steps,
                support=support,
                suggestion=(f"'{a.metric_name}' at ({coord_str}) is reported as {ra!r} in "
                            f"'{a.name}' but {rb!r} in '{b.name}' - a gap of {steps:.4g} "
                            f"reporting steps, more than rounding of the printed values "
                            f"explains; {consistent} of {len(shared)} shared cells do agree, "
                            f"so this looks like a contradiction, not different conditions"),
            ))
    return flags, notes
