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
    ap.add_argument("--detector-seconds", type=float, default=4.0)
    ap.add_argument("--checkpoint-dir", default=None)
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a text report")
    args = ap.parse_args(argv)

    try:
        res = analyze_source(args.pdf, expect_flat=args.expect_flat, detector_seconds=args.detector_seconds, checkpoint_dir=args.checkpoint_dir)
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
            "tables_extraction_usable": sum(1 for t in res.parse.tables if t.status in ("ok", "partial")),
            "metric_slices_completed": sum(bool(runs) and all(r['status']=='completed' for r in runs) for runs in res.detector_runs.values()),
            "coverage_unit": "detected extraction regions, not verified true table count",
            "flag_interpretation": "unverified review suggestions, not research errors",
            "detector_runs": {str(k):v for k,v in res.detector_runs.items()},
            "extraction": [{"page":t.page,"index":t.index_on_page,"status":t.status,"reason":t.reason,"header":t.header,"rows":t.rows,"source_text":getattr(t,"source_text",None),"source_path":getattr(t,"source_path",None),"source_line":getattr(t,"source_line",None),"source_line_basis":getattr(t,"source_line_basis",None),"original_header":getattr(t,"original_header",t.header),"source_locations":getattr(t,"source_locations",[]),"source_location_status":getattr(t,"source_location_status",None)} for t in res.parse.tables],
            "included_files": getattr(res.parse,"included_files",[]),
            "include_issues": getattr(res.parse,"include_issues",[]),
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
