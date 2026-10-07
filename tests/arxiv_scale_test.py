"""arXiv-scale test for the AnomalyNerd PDF pipeline.

Samples N random arXiv papers (quantitative categories, more likely to contain results
tables), downloads each PDF, runs analyze_pdf on it, and reports:
  - COVERAGE: how many PDFs had at least one readable results table
  - FINDINGS: pattern anomalies + statistical/digit findings per paper
  - the honest 'could not check' tally

This mirrors the HallucinationNerd random-arXiv test in spirit: no cherry-picking, report
what the tool actually does on papers in the wild, including the (expected) cases where
tables are images or layouts the extractor cannot read.
"""
from __future__ import annotations
import argparse, json, os, random, sys, time, urllib.request, urllib.parse, re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from anomalynerd.analyze_pdf import analyze_pdf

ARXIV_API = "http://export.arxiv.org/api/query"
# quantitative categories most likely to have numeric results tables
CATEGORIES = ["stat.ML", "cs.LG", "cs.CL", "econ.EM", "q-bio.QM", "physics.data-an"]


def sample_arxiv_ids(n, seed=0, categories=None):
    """Pull candidate IDs from several categories, shuffle, take n."""
    random.seed(seed)
    ids = []
    for cat in (categories or CATEGORIES):
        start = random.randint(0, 200)
        q = urllib.parse.urlencode({
            "search_query": f"cat:{cat}", "start": start,
            "max_results": 15, "sortBy": "lastUpdatedDate", "sortOrder": "descending"})
        try:
            with urllib.request.urlopen(f"{ARXIV_API}?{q}", timeout=30) as r:
                xml = r.read().decode("utf-8", "ignore")
        except Exception as e:
            print(f"  (category {cat}: query failed {type(e).__name__})")
            continue
        for m in re.finditer(r"<id>http://arxiv\.org/abs/([^<]+)</id>", xml):
            ids.append(m.group(1).split("v")[0])
        time.sleep(3)   # be polite to the arXiv API
    random.shuffle(ids)
    # dedupe preserving order
    seen = set(); uniq = []
    for i in ids:
        if i not in seen:
            seen.add(i); uniq.append(i)
    return uniq[:n]


def download_pdf(arxiv_id, outdir):
    path = os.path.join(outdir, f"{arxiv_id.replace('/', '_')}.pdf")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    url = f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AnomalyNerd-test/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())
        return path if os.path.getsize(path) > 1000 else None
    except Exception as e:
        print(f"  download failed for {arxiv_id}: {type(e).__name__}")
        return None


def download_source_tex(arxiv_id, outdir):
    """Fetch the arXiv e-print source (tar.gz or gzipped tex) and return one concatenated
    .tex string of every .tex file inside (so appendices in separate files are included)."""
    import tarfile, gzip, io
    # reuse the previously extracted .tex if present (skips the network on re-runs)
    cached = os.path.join(outdir, f"{arxiv_id.replace('/', '_')}.tex")
    if os.path.exists(cached) and os.path.getsize(cached) > 50:
        return cached
    raw = os.path.join(outdir, f"{arxiv_id.replace('/', '_')}.src")
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AnomalyNerd-test/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read()
    except Exception as e:
        print(f"  source download failed for {arxiv_id}: {type(e).__name__}")
        return None
    # try tar.gz first
    texts = []
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.lower().endswith(".tex"):
                    f = tf.extractfile(m)
                    if f:
                        texts.append(f.read().decode("utf-8", "ignore"))
    except Exception:
        # maybe a single gzipped .tex
        try:
            texts.append(gzip.decompress(blob).decode("utf-8", "ignore"))
        except Exception:
            return None
    if not texts:
        return None
    combined = "\n".join(texts)
    outp = os.path.join(outdir, f"{arxiv_id.replace('/', '_')}.tex")
    with open(outp, "w", encoding="utf-8") as f:
        f.write(combined)
    return outp


def _coverage(res):
    usable = sum(1 for t in res.parse.tables if t.status in ("ok", "partial"))
    pattern = [f for fl in res.table_flags.values() for f in fl]
    hi_med = [f for f in pattern if f.priority in ("HIGH", "MEDIUM")]
    stat_hits = [s for s in res.stat_findings if s.status == "finding"]
    return usable, len(hi_med), len(stat_hits), len(res.parse.stat_strings)


def run(n=20, seed=0, outdir="/tmp/pdf_dev/arxiv_test"):
    from anomalynerd.analyze_pdf import analyze_source
    os.makedirs(outdir, exist_ok=True)
    print(f"Sampling {n} random arXiv papers from {CATEGORIES} ...")
    ids = sample_arxiv_ids(n, seed=seed)
    print(f"Got {len(ids)} candidate IDs.\n")

    rows = []
    for i, aid in enumerate(ids, 1):
        print(f"[{i}/{len(ids)}] {aid}", flush=True)
        row = {"id": aid}
        # ---- SOURCE (LaTeX) path ----
        texp = download_source_tex(aid, outdir)
        if texp:
            try:
                r = analyze_source(texp)
                u, hm, sh, ss = _coverage(r)
                row["src_tables"] = u; row["src_flags"] = hm
                row["src_stats"] = sh; row["src_stat_strings"] = ss
            except Exception as e:
                row["src_tables"] = f"err:{type(e).__name__}"
        else:
            row["src_tables"] = "no_source"
        time.sleep(1)
        # ---- PDF path (for comparison) ----
        pdf = download_pdf(aid, outdir)
        if pdf:
            try:
                r = analyze_pdf(pdf)
                u, hm, sh, ss = _coverage(r)
                row["pdf_tables"] = u; row["pdf_flags"] = hm
            except Exception as e:
                row["pdf_tables"] = f"err:{type(e).__name__}"
        else:
            row["pdf_tables"] = "dl_fail"
        rows.append(row)
        time.sleep(2)

    # ---- summary ----
    def _ok(v): return isinstance(v, int)
    src_cov = sum(1 for r in rows if _ok(r.get("src_tables")) and r["src_tables"] > 0)
    pdf_cov = sum(1 for r in rows if _ok(r.get("pdf_tables")) and r["pdf_tables"] > 0)
    src_flag = sum(1 for r in rows if _ok(r.get("src_flags")) and r["src_flags"] > 0)
    print("\n" + "=" * 70)
    print("ARXIV-SCALE TEST  —  SOURCE (LaTeX) vs PDF coverage")
    print("=" * 70)
    print(f"Papers attempted           : {len(rows)}")
    print(f"Readable tables via SOURCE : {src_cov}  ({100*src_cov/max(1,len(rows)):.0f}%)")
    print(f"Readable tables via PDF    : {pdf_cov}  ({100*pdf_cov/max(1,len(rows)):.0f}%)")
    print(f"Papers with a hi/med flag  : {src_flag} (source path)")
    print("\nPer-paper (source tables / pdf tables | source hi-med flags):")
    for r in rows:
        print(f"  {r['id']:16} src={str(r.get('src_tables')):>8} "
              f"pdf={str(r.get('pdf_tables')):>8}  flags={r.get('src_flags','-')}")

    out = os.path.join(outdir, "arxiv_source_vs_pdf.json")
    with open(out, "w") as f:
        json.dump({"n": n, "seed": seed, "rows": rows}, f, indent=2)
    print(f"\nFull results: {out}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    run(n=args.n, seed=args.seed)
