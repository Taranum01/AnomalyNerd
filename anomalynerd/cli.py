"""AnomalyNerd CLI — the single entry point.

Usage:
    python -m anomalynerd.cli FILE.csv [options]

Examples:
    python -m anomalynerd.cli examples/halley_ozone_october.csv --metric ozone_DU --lower-better
    python -m anomalynerd.cli examples/co2_annual.csv --metric mean --higher-better --ignore unc
    python -m anomalynerd.cli results.csv --order "knowledge=Both known,p_1 known,p_2 known,Both unknown"
    python -m anomalynerd.cli results.csv --json           # machine-readable output

The tool POINTS at what deserves a closer look; it does not diagnose or re-run anything.
"""
import argparse, json, sys
from .ingest import read_csv
from .analyze import analyze


_PRIO_MARK = {"HIGH": "!!", "MEDIUM": "! ", "LOW": "  "}


def _fmt_report(flags, table_name):
    if not flags:
        return f"AnomalyNerd: no anomalies flagged in '{table_name}'."
    lines = [f"AnomalyNerd report for '{table_name}'  ({len(flags)} flag(s))", "=" * 64]
    counts = {}
    for f in flags:
        counts[f.priority] = counts.get(f.priority, 0) + 1
    lines.append("Summary: " + ", ".join(f"{k}={counts[k]}" for k in ("HIGH", "MEDIUM", "LOW") if k in counts))
    lines.append("")
    for f in flags:
        mark = _PRIO_MARK.get(f.priority, "  ")
        head = f"{mark}[{f.priority}] {f.type}"
        if f.axis:
            head += f" along '{f.axis}'"
        if f.coords:
            ctx = ", ".join(f"{k}={v}" for k, v in f.coords.items())
            head += f"  ({ctx})"
        lines.append(head)
        lines.append(f"      {f.suggestion}")
    return "\n".join(lines)


def _parse_orders(spec):
    """--order "axis=a,b,c;axis2=x,y" -> {axis:[a,b,c], ...}"""
    orders = {}
    if not spec:
        return orders
    for part in spec.split(";"):
        if "=" in part:
            ax, vals = part.split("=", 1)
            orders[ax.strip()] = [v.strip() for v in vals.split(",")]
    return orders


def main(argv=None):
    ap = argparse.ArgumentParser(prog="anomalynerd", description="Flag anomalies worth investigating in a results table.")
    ap.add_argument("csv", help="Path to the CSV results table")
    ap.add_argument("--metric", help="Name of the metric column (auto-detected if omitted)")
    ap.add_argument("--entity", help="Name of the competitor/entity column (e.g. 'method')")
    ap.add_argument("--lower-better", dest="lower", action="store_true", help="Lower metric is better (RMSE/error)")
    ap.add_argument("--higher-better", dest="higher", action="store_true", help="Higher metric is better (accuracy)")
    ap.add_argument("--ignore", nargs="*", default=None, help="Columns to ignore (e.g. uncertainty)")
    ap.add_argument("--order", help='Conceptual order for categorical axes: "axis=a,b,c;axis2=x,y"')
    ap.add_argument("--expect-increasing", nargs="*", default=None, help="Axes where the metric should increase")
    ap.add_argument("--expect-decreasing", nargs="*", default=None, help="Axes where the metric should decrease")
    ap.add_argument("--expect-flat", nargs="*", default=None, help="Axes that should be flat/random (a trend there is a real anomaly, e.g. a lottery)")
    ap.add_argument("--caption", nargs="?", const="medium", default=None,
                    choices=["short", "medium", "detailed"],
                    help="Generate a grounded caption for the table (optionally: short/medium/detailed; default medium)")
    ap.add_argument("--caption-words", type=int, default=None,
                    help="Target word budget for the caption (overrides --caption level)")
    ap.add_argument("--compare-caption", default=None,
                    help="Compare the generated caption against this real caption (checkable claims only)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a text report")
    args = ap.parse_args(argv)

    lower = True if args.lower else (False if args.higher else None)
    try:
        t = read_csv(args.csv, metric_col=args.metric, entity_col=args.entity,
                     lower_is_better=lower, orders=_parse_orders(args.order),
                     ignore_cols=args.ignore)
    except KeyError as e:
        import csv as _csv
        with open(args.csv, newline="") as _f:
            header = next(_csv.reader(_f))
        print(f"Error: column {e} not found. Available columns: {', '.join(header)}")
        print("Tip: pass one with --metric, and use --ignore for columns to skip.")
        return 2

    expected = {}
    for a in (args.expect_increasing or []):
        expected[a] = "increasing"
    for a in (args.expect_decreasing or []):
        expected[a] = "decreasing"

    flags = analyze(t, expected_monotonic=expected or None, expect_flat=args.expect_flat)

    # caption feature (generate, and optionally compare to a real caption)
    want_caption = args.caption is not None or args.caption_words is not None or args.compare_caption is not None
    caption = None
    if want_caption:
        from .caption import generate_caption
        caption = generate_caption(t, flags, level=(args.caption or "medium"),
                                   word_budget=args.caption_words)

    if args.json:
        out = {"table": t.name, "metric": t.metric_name,
               "lower_is_better": t.lower_is_better,
               "flags": [f.to_dict() for f in flags]}
        if caption is not None:
            out["caption"] = caption
        if args.compare_caption is not None:
            from .caption_compare import compare_captions
            out["caption_comparison"] = compare_captions(caption, args.compare_caption)
        print(json.dumps(out, indent=2))
    else:
        print(_fmt_report(flags, t.name))
        if caption is not None:
            print("\nCaption:\n  " + caption)
        if args.compare_caption is not None:
            from .caption_compare import format_comparison
            print("\n" + format_comparison(caption, args.compare_caption))
    return 0


if __name__ == "__main__":
    sys.exit(main())
