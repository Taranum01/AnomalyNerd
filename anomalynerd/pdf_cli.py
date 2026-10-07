"""CLI for AnomalyNerd PDF analysis:  python -m anomalynerd.pdf_cli FILE.pdf"""
from __future__ import annotations
import argparse, sys, json

from .analyze_pdf import analyze_source, format_report


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="anomalynerd-pdf",
        description="Find anomalies in the results tables of a PDF or LaTeX article, run statistical "
                    "and digit checks, and report what could not be checked.")
    ap.add_argument("pdf", help="Path to the article (.pdf or .tex)")
    ap.add_argument("--expect-flat", nargs="*", default=None,
                    help="Axis names that should be flat/random (a trend there is a real "
                         "anomaly, e.g. a lottery)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a text report")
    args = ap.parse_args(argv)

    try:
        res = analyze_source(args.pdf, expect_flat=args.expect_flat)
    except FileNotFoundError:
        print(f"error: file not found: {args.pdf}", file=sys.stderr); return 2
    except Exception as e:
        print(f"error: could not analyze PDF: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        out = {
            "path": res.path,
            "pages": res.parse.n_pages,
            "tables_detected": len(res.parse.tables),
            "tables_analyzed": sum(1 for t in res.parse.tables
                                   if t.status in ("ok", "partial")),
            "pattern_flags": [f.to_dict() for flags in res.table_flags.values()
                              for f in flags],
            "stat_findings": [{"kind": s.kind, "status": s.status, "severity": s.severity,
                               "detail": s.detail, "evidence": s.evidence}
                              for s in res.stat_findings],
            "could_not_check": res.could_not_check,
        }
        print(json.dumps(out, indent=2))
    else:
        print(format_report(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
