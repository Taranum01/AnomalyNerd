"""Turn one check off and re-collect the flags on the labelled review papers.

    REVIEW_PDFS=<pdf_dir> python validation/pdf_flag_review/ablate.py <check> <out.json>

<check> is a detector name from anomalynerd.bounded.DETECTORS, 'unit_mismatch', 'stats',
or 'none' (everything on). Runs the development and held-out papers. With the review cache
(REVIEW_CACHE, default ~/.cache/anomalynerd_review) a run takes about two minutes.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REVIEW = os.path.join(REPO, "validation", "pdf_flag_review")
PDFS = os.environ.get("REVIEW_PDFS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdfs"))
CACHE = os.environ.get("REVIEW_CACHE", os.path.expanduser("~/.cache/anomalynerd_review"))

if __name__ == "__main__":
    check, out = sys.argv[1], sys.argv[2]
    sys.path.insert(0, REPO); sys.path.insert(0, REVIEW)
    import docling_cache
    docling_cache.enable(CACHE)
    ckpt = docling_cache.detector_dir(CACHE)
    from anomalynerd import bounded, analyze_pdf
    if check in bounded.DETECTORS:
        bounded.DETECTORS = tuple(d for d in bounded.DETECTORS if d != check)
        if check == "detect_copied_rows":
            analyze_pdf._copied_rows_raw = lambda table, **kwargs: []   # the raw-cell version of the same check
    elif check == "unit_mismatch":
        analyze_pdf._unit_mismatches = lambda table, min_cells=5: []
    elif check == "stats":
        analyze_pdf.statcheck = lambda strings: []
        analyze_pdf.digit_tests = lambda tidy: []
        analyze_pdf.grim = lambda *a, **k: []
        analyze_pdf.grimmer = lambda *a, **k: []
    elif check != "none":
        raise SystemExit(f"unknown check {check}")
    flags = []
    for listing in ("papers.json", "heldout_papers.json"):
        for p in json.load(open(os.path.join(REVIEW, listing))):
            res = analyze_pdf.analyze_source(os.path.join(PDFS, p["name"] + ".pdf"), checkpoint_dir=ckpt)
            for key, fl in res.table_flags.items():
                for f in fl:
                    flags.append({"paper": p["name"], "page": key[0], "priority": f.priority,
                                  "type": f.type, "suggestion": f.suggestion})
            for s in res.stat_findings:
                if s.status == "finding":
                    flags.append({"paper": p["name"], "page": None, "priority": s.severity.upper(),
                                  "type": "stat:" + s.kind, "suggestion": s.detail})
    json.dump(flags, open(out, "w"), indent=1, ensure_ascii=False)
    print(check, len(flags), "flags", flush=True)
