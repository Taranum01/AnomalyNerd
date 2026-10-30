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
import csv, os, re, math, tempfile
from .model import TidyTable, Axis, MISSING

_METRIC_WORDS = ("rmse", "mae", "mse", "error", "err", "loss", "accuracy", "acc",
                 "score", "auc", "f1", "precision", "recall", "value", "runtime",
                 "time", "latency")
_LOWER_BETTER = ("rmse", "mae", "mse", "error", "err", "loss", "runtime", "time", "latency")
# Whole-word metric names for direction inference (matched against the name's words).
_LOWER_BETTER_WORDS = set(_LOWER_BETTER) | {"errors", "ppl", "perplexity", "wer", "cer", "fid"}
_HIGHER_BETTER_SUBSTR = ("accuracy", "acc", "auc", "f1", "precision", "recall", "score")
_HIGHER_BETTER_WORDS = {"bleu", "rouge", "pass", "top", "map", "miou", "iou", "em"}
_MISSING_TOKENS = {"", "--", "-", "n/a", "na", "nan", "none", "null"}

_ORDER_LEXICON = [
    ["low", "medium", "med", "high"],
    ["none", "some", "all"],
    ["both known", "p_1 known", "p_2 known", "both unknown"],
    ["small", "medium", "large"],
    ["cold", "warm", "hot"],
]


def _parse_number(s: str):
    """Turn a table cell into a number. Returns MISSING for blanks and missing-value tokens. Handles commas, percent signs, unicode minus and scientific notation written as in papers."""
    if s is None:
        return MISSING
    t = s.strip().lower()
    if t in _MISSING_TOKENS:
        return MISSING
    t2 = s.strip().replace(",", "").replace("%", "")
    t2 = t2.replace("−", "-")  # unicode minus

    # Scientific notation appears in papers as 8x10^3, 8×10^{-3}, or
    # 8 \times 10^{-3}. Normalize those forms before the float parse. LaTeX cleaning can
    # leave the multiplication command as whitespace ("8 10^-3"), so accept that shape too.
    sci = re.match(
        r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:[×x]|\\times)?\s*10\^\{?(-?\d+)\}?$",
        t2,
        flags=re.I,
    )
    if sci:
        t2 = f"{sci.group(1)}e{sci.group(2)}"

    # Magnitude suffixes common in ML papers: "1k" / "300K" (thousand), "1.5M" (million),
    # "175B" (billion). Without this, a column like 1k, 2k, ... 10k is read as text labels, so
    # its order is lost and most checks cannot run. Deliberately conservative: only a plain
    # number immediately followed by k/K, M or B, nothing else in the cell. Lowercase m and b
    # are NOT accepted (metres, minutes, bits), and neither is "G"/"T" or a trailing word.
    mag = re.match(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s?([kKMB])$", t2)
    if mag:
        mult = {"k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9}[mag.group(2)]
        return float(mag.group(1)) * mult

    # Mean-plus-uncertainty cells often carry a bracketed interval or parenthesized SD:
    # "0.91 [0.88, 0.94]" or "0.91 (0.03)". Keep the central estimate as the value; the
    # original string remains available in each row's _raw field.
    central = re.match(
        r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*(?:\[[^\]]+\]|\([^)]*\))\s*$",
        t2,
        flags=re.I,
    )
    if central:
        t2 = central.group(1)
    try:
        number = float(t2)
        return number if math.isfinite(number) else MISSING
    except ValueError:
        return MISSING


def _looks_numeric(values):
    """True when at least 80% of the non-missing values parse as numbers."""
    got = 0; total = 0
    for v in values:
        if v is None or str(v).strip().lower() in _MISSING_TOKENS:
            continue
        total += 1
        if _parse_number(v) is not MISSING:
            got += 1
    return total > 0 and got / total >= 0.8


def _infer_order(levels):
    """Return the levels in their natural order (for example low, medium, high) when they fit a known word list, otherwise None."""
    lows = [str(l).strip().lower() for l in levels]
    for lex in _ORDER_LEXICON:
        if set(lows) <= set(lex):
            idx = {v: i for i, v in enumerate(lex)}
            return sorted(levels, key=lambda x: idx[str(x).strip().lower()])
    return None


def _read_csv_rows(path):
    """Return (header, data_rows) for a CSV file. Data portals (NOAA GML, for example) put a
    licence or description block of '#' comment lines above the header; only LEADING comment and
    blank lines are skipped, so a '#' inside the table (a "# layers" cell, say) is untouched."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = f.readlines()
    start = 0
    while start < len(lines) and (not lines[start].strip() or lines[start].lstrip().startswith("#")):
        start += 1
    reader = csv.reader(lines[start:])
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError(f"{path}: no header row found (the file is empty or only comments)")
    data = [row for row in reader if any(c.strip() for c in row)]
    return header, data


def _last_numeric(numeric_cols, name, header):
    """The default metric column (the last numeric one). When there is none, raise a clear error
    instead of an IndexError, so a table with nothing to measure is reported, not crashed on."""
    if not numeric_cols:
        raise ValueError(f"{name}: no numeric column found, so there is nothing to analyze "
                         f"(columns: {', '.join(header)})")
    return numeric_cols[-1]


def read_csv(path, metric_col=None, entity_col=None, lower_is_better=None,
             name=None, orders=None, index_cols=None, ignore_cols=None):
    """Read a CSV results table into a TidyTable. Column roles (metric, entity, axes) are detected unless given."""
    header, data = _read_csv_rows(path)
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
        # An index-like numeric column (a rank/row number such as '#' or 'No.': sorted,
        # consecutive integers) is a grid axis, never an entity. Keep it as an index column so
        # it is NOT melted into the entity set as a bogus "method".
        for h in index_like:
            if h not in idx_cols:
                idx_cols.append(h)
        entity_cols = [h for h in numeric_cols if h not in idx_cols]
        if not entity_cols:                        # safety: fall back to treating last col as metric (LONG)
            metric_col = _last_numeric(numeric_cols, name, header)
        else:
            rows = []
            for r_i in range(len(data)):
                base = {c: _coerce(cols[c][r_i]) for c in idx_cols}
                for ec in entity_cols:
                    rows.append({**base, "method": ec, "value": _parse_number(cols[ec][r_i]), "_raw": cols[ec][r_i]})
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
                          else (metric_numeric[-1] if metric_numeric else _last_numeric(numeric_cols, name, header)))
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
        row["_raw"] = cols[mcol][r_i]
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
        lib = _infer_lower_is_better(mcol)
    return TidyTable(name, axes, entity_axis=ecol, metric_name=mcol,
                     lower_is_better=lib, rows=rows)


def _infer_lower_is_better(name):
    """Guess the metric direction from its column name: an explicit arrow wins, then error-type
    words (loss, error, perplexity), then score-type words (accuracy, BLEU, pass@k); a name
    with none of these keeps the old default of lower-is-better."""
    if "↑" in name:
        return False
    if "↓" in name:
        return True
    low = name.lower()
    words = set(re.findall(r"[a-z]+", low))
    if words & _LOWER_BETTER_WORDS:
        return True
    if any(w in low for w in _HIGHER_BETTER_SUBSTR) or words & _HIGHER_BETTER_WORDS:
        return False
    return True


def _median_scale(values):
    """Median of the absolute non-zero values, used as a typical size for the column. Returns None if there are none."""
    parsed = [_parse_number(v) for v in values]
    nums = [abs(x) for x in parsed if x is not MISSING and x != 0]
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


def _side_by_side_groups(header, cols):
    """Detect several small tables printed side by side and read as one: every header is
    'Group.Sub', at least two groups repeat the same sub-columns, and each group has its own
    text (label) column and numeric column. Returns [(group, [(sub, header), ...]), ...]
    or None."""
    if len(header) < 4 or not all("." in h for h in header):
        return None
    groups = {}
    for h in header:
        prefix, sub = h.rsplit(".", 1)
        groups.setdefault(prefix, []).append((sub, h))
    if len(groups) < 2:
        return None
    subs = [tuple(s for s, _ in m) for m in groups.values()]
    if len(set(subs)) != 1 or len(subs[0]) < 2:
        return None
    for members in groups.values():
        kinds = [_looks_numeric(cols[h]) for _, h in members]
        if all(kinds) or not any(kinds):
            return None
    return list(groups.items())


def read_tables(path, **kwargs):
    """Like read_csv, but returns a LIST of TidyTables. For a wide table whose numeric
    columns are DIFFERENT metrics (different scales, e.g. accuracy/time/ratio), each metric
    column becomes its own single-metric table sharing the index axes, so the detectors never
    compare unlike quantities. For a homogeneous wide table (same metric per entity) or a
    normal long table, this returns the single table read_csv would return.

    This is the entry point the PDF / LaTeX article pipeline uses, where wide multi-metric
    tables are common."""
    header, data = _read_csv_rows(path)
    cols = {h: [row[i] if i < len(row) else "" for row in data]
            for i, h in enumerate(header)}

    groups = _side_by_side_groups(header, cols)
    if groups and kwargs.get("metric_col") is None:
        tables = []
        base = kwargs.get("name") or path.split("/")[-1]
        for prefix, members in groups:
            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
                writer = csv.writer(tmp)
                writer.writerow([sub for sub, _ in members])
                writer.writerows([[cols[h][i] for _, h in members] for i in range(len(data))])
            try:
                options = dict(kwargs)
                options["name"] = f"{base} [{prefix}]"
                tables.extend(read_tables(tmp.name, **options))
            finally:
                os.unlink(tmp.name)
        return tables

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
    named_metrics = [h for h in numeric_cols if h in metric_by_name or any(w in h.lower().split("_") for w in ("ratio", "count", "metric"))]
    # Named metrics are outputs, even when monotonic or similar in scale.
    # The old sweep heuristic incorrectly removed improving accuracy curves.
    if kwargs.get("metric_col") is None and len(named_metrics) >= 2:
        shared = [h for h in header if h not in named_metrics]
        tables = []
        for mc in named_metrics:
            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tmp:
                writer = csv.writer(tmp)
                writer.writerow(shared + [mc])
                writer.writerows([[cols[c][i] for c in shared + [mc]] for i in range(len(data))])
            try:
                options = dict(kwargs)
                options.update(metric_col=mc, name=(kwargs.get("name") or path.split("/")[-1]) + f" [{mc}]")
                tables.append(read_csv(tmp.name, **options))
            finally:
                os.unlink(tmp.name)
        return tables


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
    """Convert a cell to a number when it parses as one, otherwise return the stripped text."""
    n = _parse_number(x)
    return n if n is not MISSING else (x.strip() if isinstance(x, str) else x)


def _make_axis(name, values, orders):
    """Choose the axis type: ordered by the caller, numeric, ordered by a known word list, or unordered."""
    if name in orders:
        return Axis(name, "ordered_cat", order=orders[name])
    if _looks_numeric([str(v) for v in values]):
        return Axis(name, "numeric")
    order = _infer_order(list(dict.fromkeys(values)))
    if order:
        return Axis(name, "ordered_cat", order=order)
    return Axis(name, "unordered_cat")
