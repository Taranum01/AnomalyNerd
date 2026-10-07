"""Generic ingest: CSV -> TidyTable, with schema auto-inference.

Two accepted CSV shapes:
  (A) LONG/tidy: columns are axes + one metric column. e.g.
        p1,p2,method,RMSE
        0.12,0.05,MethodA,1.61
  (B) WIDE: some index columns + many metric columns (one per entity). e.g.
        p1,MethodB,ARIMA,MethodA
        0.12,48.72,86.44,1.61
      -> melted into long form with entity axis = the metric-column name.

Schema inference is a best-effort GUESS; every choice can be overridden by the caller.
"""
from __future__ import annotations
import csv, os, re, math
from .model import TidyTable, Axis, MISSING

_METRIC_WORDS = ("rmse", "mae", "mse", "error", "err", "loss", "accuracy", "acc",
                 "score", "auc", "f1", "precision", "recall", "value", "runtime",
                 "time", "latency")
_LOWER_BETTER = ("rmse", "mae", "mse", "error", "err", "loss", "runtime", "time", "latency")
_MISSING_TOKENS = {"", "--", "-", "n/a", "na", "nan", "none", "null"}

_ORDER_LEXICON = [
    ["low", "medium", "med", "high"],
    ["none", "some", "all"],
    ["both known", "p_1 known", "p_2 known", "both unknown"],
    ["small", "medium", "large"],
    ["cold", "warm", "hot"],
]


def _parse_number(s: str):
    if s is None:
        return MISSING
    t = s.strip().lower()
    if t in _MISSING_TOKENS:
        return MISSING
    t2 = s.strip().replace(",", "").replace("%", "")
    t2 = re.sub(r"[×x]\s*10\^?(-?\d+)", lambda m: f"e{m.group(1)}", t2)  # 8×10^3 -> 8e3
    t2 = t2.replace("−", "-")  # unicode minus
    try:
        return float(t2)
    except ValueError:
        return MISSING


def _looks_numeric(values):
    got = 0; total = 0
    for v in values:
        if v is None or str(v).strip().lower() in _MISSING_TOKENS:
            continue
        total += 1
        if _parse_number(v) is not MISSING:
            got += 1
    return total > 0 and got / total >= 0.8


def _infer_order(levels):
    lows = [str(l).strip().lower() for l in levels]
    for lex in _ORDER_LEXICON:
        if set(lows) <= set(lex):
            idx = {v: i for i, v in enumerate(lex)}
            return sorted(levels, key=lambda x: idx[str(x).strip().lower()])
    return None


def read_csv(path, metric_col=None, entity_col=None, lower_is_better=None,
             name=None, orders=None, index_cols=None, ignore_cols=None):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        data = [row for row in reader if any(c.strip() for c in row)]
    cols = {h: [row[i] if i < len(row) else "" for row in data] for i, h in enumerate(header)}
    name = name or path.split("/")[-1]
    orders = orders or {}

    numeric_cols = [h for h in header if _looks_numeric(cols[h])]
    cat_cols = [h for h in header if h not in numeric_cols]

    # An index-like column is numeric with (nearly) one distinct value per row AND looks like
    # an actual index: integer-valued, non-decreasing, and roughly consecutive (e.g. a serial
    # 1..N or 'month_num' 1..12). This is an ordered AXIS, not a metric/entity, so it must not
    # count toward the "many numeric metric columns" test that triggers WIDE mode. Requiring
    # the index SHAPE (not just near-uniqueness) avoids misreading real metric columns whose
    # values happen to be distinct (e.g. 0.10, 0.14, 9.80) as an index.
    def _is_index_like(h):
        if h not in numeric_cols:
            return False
        if len(data) < 3 or len(set(cols[h])) < 0.95 * len(data):
            return False
        nums = [_parse_number(v) for v in cols[h]]
        nums = [x for x in nums if x is not MISSING]
        if len(nums) < 0.95 * len(data):
            return False
        if any(abs(x - round(x)) > 1e-9 for x in nums):   # must be integer-valued
            return False
        ints = [round(x) for x in nums]
        if ints != sorted(ints):                           # must be non-decreasing
            return False
        span = ints[-1] - ints[0]
        # roughly consecutive: the range is close to the count (no huge gaps)
        return span <= 2 * len(ints)

    index_like = [h for h in numeric_cols if _is_index_like(h)]
    metric_numeric = [h for h in numeric_cols if h not in index_like]

    # decide shape
    metric_by_name = [h for h in header if any(w in h.lower() for w in _METRIC_WORDS)]

    # WIDE only when there are 2+ numeric columns that could each be a metric/entity (i.e.
    # excluding index columns), and none is obviously named as the metric. A table like
    # (index, label, one_value) has just one non-index numeric column, so it is LONG.
    if metric_col is None and len(metric_numeric) >= 2 and len(metric_by_name) == 0:
        # WIDE: many numeric columns, none obviously "the metric" -> entities are those columns.
        # Index columns: caller hint > categorical columns > default to the FIRST column
        # (near-universal convention for a wide results table: leading col is the grid axis).
        if index_cols is not None:
            idx_cols = list(index_cols)
        elif cat_cols:
            idx_cols = list(cat_cols)
        else:
            idx_cols = [header[0]]
        entity_cols = [h for h in numeric_cols if h not in idx_cols]
        if not entity_cols:                        # safety: fall back to treating last col as metric (LONG)
            metric_col = numeric_cols[-1]
        else:
            rows = []
            for r_i in range(len(data)):
                base = {c: _coerce(cols[c][r_i]) for c in idx_cols}
                for ec in entity_cols:
                    rows.append({**base, "method": ec, "value": _parse_number(cols[ec][r_i])})
            axes = {}
            for c in idx_cols:
                axes[c] = _make_axis(c, [_coerce(x) for x in cols[c]], orders)
            axes["method"] = Axis("method", "unordered_cat")
            lib = True if lower_is_better is None else lower_is_better
            return TidyTable(name, axes, entity_axis="method", metric_name="value",
                             lower_is_better=lib, rows=rows)

    # LONG: pick metric col. Prefer a name that looks like a metric; otherwise the last
    # NON-index numeric column (an index like 'month_num' is an axis, never the metric).
    mcol = metric_col or (metric_by_name[0] if metric_by_name
                          else (metric_numeric[-1] if metric_numeric else numeric_cols[-1]))
    axis_cols = [h for h in header if h != mcol]
    # Drop caller-ignored columns and redundant continuous covariates: a numeric column
    # with (nearly) one distinct value per row is a per-row attribute (e.g. an uncertainty
    # 'unc' or an id), NOT an analysis axis. Keep it only if it's the sole axis.
    ignore = set(ignore_cols or [])
    if len(axis_cols) > 1:
        near_unique_numeric = [h for h in axis_cols
                               if h in numeric_cols and h != entity_col
                               and len(set(cols[h])) >= 0.95 * len(data)]
        # keep the FIRST such column as the primary index; drop the rest (redundant covariates
        # like 'unc' that pair 1:1 with the index and are per-row attributes, not axes).
        for h in near_unique_numeric[1:]:
            ignore.add(h)
        # Drop a categorical column that is a 1:1 LABEL ALIAS of a numeric index axis (e.g.
        # 'month' spelling out 'month_num'): one distinct label per row AND a numeric index
        # column is present. It is a human-readable name for the index, not an independent
        # entity axis, so keeping it would spuriously create an entity/win-reversal.
        has_numeric_index = any(h in index_like for h in axis_cols if h not in ignore)
        if has_numeric_index:
            for h in axis_cols:
                if (h not in ignore and h in cat_cols and h != entity_col
                        and len(set(cols[h])) >= 0.95 * len(data)):
                    ignore.add(h)
    axis_cols = [h for h in axis_cols if h not in ignore]
    ecol = entity_col
    if ecol is None:
        # entity = the categorical axis with the most levels (methods) if any.
        # An explicitly-ordered column (order hint or lexicon match) is a real ordered axis,
        # NOT an entity/competitor — exclude such columns from entity auto-detection.
        cat_axis = [h for h in axis_cols if h in cat_cols and _make_axis(h, [_coerce(x) for x in cols[h]], orders).kind != "ordered_cat"]
        ecol = max(cat_axis, key=lambda h: len(set(cols[h]))) if cat_axis else None
    rows = []
    for r_i in range(len(data)):
        row = {c: _coerce(cols[c][r_i]) for c in axis_cols}
        row["value"] = _parse_number(cols[mcol][r_i])
        rows.append(row)
    axes = {}
    for c in axis_cols:
        kind_vals = [_coerce(x) for x in cols[c]]
        ax = _make_axis(c, kind_vals, orders)
        # only force unordered-entity when the column isn't an explicitly ordered axis
        if c == ecol and ax.kind != "ordered_cat":
            ax = Axis(c, "unordered_cat")
        axes[c] = ax
    lib = lower_is_better
    if lib is None:
        lib = any(w in mcol.lower() for w in _LOWER_BETTER) or True
    return TidyTable(name, axes, entity_axis=ecol, metric_name=mcol,
                     lower_is_better=lib, rows=rows)


def _median_scale(values):
    nums = [abs(_parse_number(v)) for v in values]
    nums = [x for x in nums if x is not MISSING and x != 0]
    if not nums:
        return None
    nums.sort()
    return nums[len(nums) // 2]


def _columns_are_homogeneous(cols, headers):
    """True if the given numeric columns look like the SAME metric measured for different
    entities (similar median scale) rather than DIFFERENT metrics (e.g. accuracy vs time vs
    ratio). Same-metric columns collapse into one entity axis; different-metric columns must
    be analyzed separately. Heuristic: medians within ~1 order of magnitude."""
    scales = [_median_scale(cols[h]) for h in headers]
    scales = [s for s in scales if s]
    if len(scales) < 2:
        return True
    lo, hi = min(scales), max(scales)
    return lo > 0 and (hi / lo) < 10


def read_tables(path, **kwargs):
    """Like read_csv, but returns a LIST of TidyTables. For a wide table whose numeric
    columns are DIFFERENT metrics (different scales, e.g. accuracy/time/ratio), each metric
    column becomes its own single-metric table sharing the index axes, so the detectors never
    compare unlike quantities. For a homogeneous wide table (same metric per entity) or a
    normal long table, this returns the single table read_csv would return.

    This is the entry point the PDF / LaTeX article pipeline uses, where wide multi-metric
    tables are common."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        data = [row for row in reader if any(c.strip() for c in row)]
    cols = {h: [row[i] if i < len(row) else "" for row in data]
            for i, h in enumerate(header)}

    numeric_cols = [h for h in header if _looks_numeric(cols[h])]
    cat_cols = [h for h in header if h not in numeric_cols]
    metric_by_name = [h for h in header if any(w in h.lower() for w in _METRIC_WORDS)]

    # identify index-like columns the same way read_csv does (integer, sorted, consecutive)
    def _idx_like(h):
        if h not in numeric_cols or len(data) < 3 or len(set(cols[h])) < 0.95 * len(data):
            return False
        nums = [_parse_number(v) for v in cols[h]]
        nums = [x for x in nums if x is not MISSING]
        if len(nums) < 0.95 * len(data) or any(abs(x - round(x)) > 1e-9 for x in nums):
            return False
        ints = [round(x) for x in nums]
        return ints == sorted(ints) and (ints[-1] - ints[0]) <= 2 * len(ints)

    metric_numeric = [h for h in numeric_cols if not _idx_like(h)]

    # Split when there are 2+ metric columns that are heterogeneous (different scales =>
    # different metrics). This holds whether or not the columns are metric-named: a table
    # with 'accuracy' and 'time' columns has two DIFFERENT metrics and must be split.
    if (kwargs.get("metric_col") is None and len(metric_numeric) >= 2
            and not _columns_are_homogeneous(cols, metric_numeric)):
        # A monotonic numeric column (a sweep variable like 0.0,0.1,0.2,... or a year) is the
        # ORDERED AXIS the metrics are measured against, not itself a metric. Keep it as an
        # index column so every split metric table retains its axis (otherwise each metric
        # would have no axis and nothing to look along).
        def _is_sweep_axis(h):
            nums = [_parse_number(v) for v in cols[h]]
            nums = [x for x in nums if x is not MISSING]
            if len(nums) < 0.95 * len(data) or len(nums) < 4:
                return False
            if len(set(nums)) < 0.9 * len(nums):   # a metric repeats; an axis is ~distinct
                return False
            asc = all(nums[i] <= nums[i + 1] for i in range(len(nums) - 1))
            desc = all(nums[i] >= nums[i + 1] for i in range(len(nums) - 1))
            return asc or desc

        sweep_axes = [h for h in metric_numeric if _is_sweep_axis(h)]
        true_metrics = [h for h in metric_numeric if h not in sweep_axes]
        if len(true_metrics) < 2:
            return [read_csv(path, **kwargs)]   # not really multi-metric once axis removed
        index_cols = [h for h in header if h not in true_metrics]  # labels + sweep axes
        tables = []
        for mc in true_metrics:
            # build a one-metric CSV: the index/label/axis columns + this single metric
            import tempfile
            keep = index_cols + [mc]
            tmp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
            w = csv.writer(tmp)
            w.writerow(keep)
            for r_i in range(len(data)):
                w.writerow([cols[c][r_i] for c in keep])
            tmp.close()
            try:
                t = read_csv(tmp.name, metric_col=mc,
                             name=(kwargs.get("name") or path.split("/")[-1]) + f" [{mc}]")
                tables.append(t)
            except Exception:
                pass
            finally:
                os.unlink(tmp.name)
        if tables:
            return tables

    # otherwise: one table (homogeneous wide, or normal long)
    return [read_csv(path, **kwargs)]


def _coerce(x):
    n = _parse_number(x)
    return n if n is not MISSING else (x.strip() if isinstance(x, str) else x)


def _make_axis(name, values, orders):
    if name in orders:
        return Axis(name, "ordered_cat", order=orders[name])
    if _looks_numeric([str(v) for v in values]):
        return Axis(name, "numeric")
    order = _infer_order(list(dict.fromkeys(values)))
    if order:
        return Axis(name, "ordered_cat", order=order)
    return Axis(name, "unordered_cat")
