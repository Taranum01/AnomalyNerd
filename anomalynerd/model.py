"""Core data model for AnomalyNerd.

Everything the detectors see is a TidyTable: one row per
(coordinates..., metric_name, value). Any input format (CSV, LaTeX, Excel)
is converted to this shape, so detectors never special-case a format.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional
import math


MISSING = None  # single sentinel for "no value" (never 0)


@dataclass
class Axis:
    """An input dimension of the results table."""
    name: str
    kind: str                       # "numeric" | "ordered_cat" | "unordered_cat"
    order: Optional[list] = None    # explicit order for ordered_cat / display order

    def sorted_levels(self, levels):
        levels = list(levels)
        if self.kind == "numeric":
            def _key(x):
                try:
                    return (0, float(x))
                except (TypeError, ValueError):
                    return (1, 0.0)   # blanks / non-numeric sort to the end, no crash
            return sorted(levels, key=_key)
        if self.kind == "ordered_cat" and self.order:
            idx = {v: i for i, v in enumerate(self.order)}
            return sorted(levels, key=lambda x: idx.get(x, len(idx)))
        return levels  # unordered: keep as-is


@dataclass
class TidyTable:
    """Long-form table. rows: list of dicts with axis values + 'metric' + 'value'."""
    name: str
    axes: dict[str, Axis]           # axis_name -> Axis  (input dimensions)
    entity_axis: Optional[str]      # which axis is the competitor (e.g. "method")
    metric_name: str                # e.g. "RMSE"
    lower_is_better: bool
    rows: list[dict]                # each: {axis1:.., axis2:.., 'value': float|None}

    def levels(self, axis_name: str) -> list:
        seen = []
        for r in self.rows:
            v = r.get(axis_name)
            if v not in seen:
                seen.append(v)
        return seen

    def slice(self, **fixed) -> list[dict]:
        """Rows matching the given fixed coordinates."""
        out = []
        for r in self.rows:
            if all(r.get(k) == v for k, v in fixed.items()):
                out.append(r)
        return out

    def value_at(self, **coords):
        for r in self.rows:
            if all(r.get(k) == v for k, v in coords.items()):
                return r.get("value")
        return MISSING


@dataclass
class Flag:
    """One thing worth a closer look. The tool POINTS; it does not diagnose."""
    type: str                       # numeric_jump | monotonicity | win_reversal | cat_exception | duplicate | flatness
    table: str
    axis: Optional[str]
    coords: dict                    # held-fixed coordinates / context
    detail: dict                    # type-specific evidence (values, ratio, sequence, ...)
    strength: float                 # size of deviation (raw, comparable within type)
    support: float                  # 0..1 how strongly the broken pattern otherwise holds
    priority: str = "LOW"           # HIGH | MEDIUM | LOW  (set by scorer)
    suggestion: str = ""

    def to_dict(self):
        d = {
            "type": self.type, "table": self.table, "axis": self.axis,
            "coords": self.coords, "detail": self.detail,
            "strength": round(self.strength, 4), "support": round(self.support, 4),
            "priority": self.priority, "suggestion": self.suggestion,
        }
        return d


def is_num(x) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))
