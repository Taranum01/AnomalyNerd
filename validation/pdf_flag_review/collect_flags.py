"""Re-collect every flag AnomalyNerd raises on the 22 papers of the PDF flag review.

    python validation/pdf_flag_review/collect_flags.py [download_dir] [--heldout | --fresh]

Downloads each paper (arXiv or PLOS, public), runs the article pipeline, and writes
flags.json (heldout_flags.json with --heldout, fresh_flags.json with --fresh) next to this
script. Compare it with labels.json or heldout_labels.json, which hold the hand judgments.
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

PLOS = {"doi:10.1371/journal.pone.0287795": "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0287795&type=printable",
        "doi:10.1371/journal.pmed.1003583": "https://journals.plos.org/plosmedicine/article/file?id=10.1371/journal.pmed.1003583&type=printable"}


def url_for(source):
    if source.startswith("arXiv:"):
        return "https://arxiv.org/pdf/" + source.split(":", 1)[1]
    return PLOS[source]


def main():
    from anomalynerd.analyze_pdf import analyze_source
    args = [a for a in sys.argv[1:] if a not in ("--heldout", "--fresh")]
    heldout = "--heldout" in sys.argv
    fresh = "--fresh" in sys.argv
    prefix = "heldout_" if heldout else ("fresh_" if fresh else "")
    out_dir = args[0] if args else os.path.join(HERE, "pdfs")
    os.makedirs(out_dir, exist_ok=True)
    papers = json.load(open(os.path.join(HERE, prefix + "papers.json")))
    flags = []
    for p in papers:
        path = os.path.join(out_dir, p["name"] + ".pdf")
        if not os.path.exists(path):
            req = urllib.request.Request(url_for(p["source"]), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
                f.write(r.read())
        res = analyze_source(path)
        for key, fl in res.table_flags.items():
            for f in fl:
                flags.append({"paper": p["name"], "page": key[0], "priority": f.priority,
                              "type": f.type, "suggestion": f.suggestion})
        for s in res.stat_findings:
            if s.status == "finding":
                flags.append({"paper": p["name"], "page": None, "priority": s.severity.upper(),
                              "type": "stat:" + s.kind, "suggestion": s.detail})
        print(f"{p['name']}: {sum(1 for x in flags if x['paper'] == p['name'])} flags", flush=True)
    out = prefix + "flags.json"
    json.dump(flags, open(os.path.join(HERE, out), "w"), indent=1, ensure_ascii=False)
    print(f"total {len(flags)} flags -> {out}")


if __name__ == "__main__":
    main()
