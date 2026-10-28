"""
AnomalyNerd web UI (FastAPI + Jinja2).

Upload a results table (CSV or Excel) or an article (PDF, LaTeX or Word); the existing AnomalyNerd analysis
runs and the browser shows the flagged anomalies per table, with the "what could not be
checked" coverage shown up front.

SECURITY NOTE (important): this is a LOCAL, single-user demo UI. It binds to 127.0.0.1 only,
so it is NOT reachable from other machines, and it has no authentication. It accepts file
uploads and runs analysis on them. Do NOT expose it to a network or the internet as-is: that
would require adding authentication, input hardening, and a review of the upload path first.
Run it only on your own machine.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# AnomalyNerd analysis entry points (reuse the existing, tested code; no logic duplicated here)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from anomalynerd.analyze_pdf import analyze_source, _loc    # noqa: E402
from anomalynerd.ingest import read_csv                     # noqa: E402
from anomalynerd.analyze import analyze                     # noqa: E402

MAX_UPLOAD_BYTES = 25 * 1024 * 1024        # 25 MB upload cap
ALLOWED_EXT = {".csv", ".pdf", ".tex", ".docx", ".xlsx"}

app = FastAPI(title="AnomalyNerd", description="Results-table anomaly flagging")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


def _flag_view(flag, table_name):
    """Turn a Flag into a plain dict for the JSON response / JS rendering."""
    return {
        "type": flag.type,
        "priority": flag.priority,          # HIGH | MEDIUM | LOW  -> used as the filter category
        "axis": flag.axis,
        "coords": flag.coords,
        "suggestion": flag.suggestion,
        "table": table_name,
    }


def _analyze_csv(path, expect_flat):
    """Single-table CSV path: one tidy table -> flags."""
    t = read_csv(path)
    flags = analyze(t, expect_flat=expect_flat or None)
    tables = [{
        "name": t.name or "table",
        "metric": t.metric_name,
        "flags": [_flag_view(f, t.name or "table") for f in flags],
    }]
    return {"source_kind": "csv", "tables": tables, "could_not_check": []}


def _analyze_article(path, expect_flat):
    """PDF / LaTeX / Word / Excel path: reuse analyze_source, then shape for the template."""
    res = analyze_source(path, expect_flat=expect_flat or None)
    tables = []
    for key, flags in res.table_flags.items():
        page, idx = (key[0], key[1]) if isinstance(key, tuple) and len(key) >= 2 else (0, 0)
        where = _loc(res.source_kind, page, idx)   # "page 3 (#0)", "table 2", "sheet 1", ...
        tables.append({
            "name": where,
            "metric": "",
            "flags": [_flag_view(f, where) for f in flags],
        })
    return {
        "source_kind": res.source_kind,
        "tables": tables,
        "could_not_check": list(res.could_not_check),
    }


@app.post("/analyze")
async def analyze_upload(
    file: UploadFile = File(...),
    expect_flat: str = Form(""),
):
    name = file.filename or "upload"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Use CSV, PDF, LaTeX (.tex), Word (.docx) or Excel (.xlsx).")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (limit 25 MB).")

    flat_axes = [a.strip() for a in expect_flat.split(",") if a.strip()]

    # write to a temp file the analyzers can open, then always clean it up
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        tmp.write(data)
        tmp.close()
        if ext == ".csv":
            result = _analyze_csv(tmp.name, flat_axes)
        else:
            result = _analyze_article(tmp.name, flat_axes)
    except Exception as e:
        raise HTTPException(500, f"Could not analyze this file: {type(e).__name__}: {e}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    # counts for the clickable category summary
    all_flags = [f for t in result["tables"] for f in t["flags"]]
    counts = {
        "HIGH": sum(1 for f in all_flags if f["priority"] == "HIGH"),
        "MEDIUM": sum(1 for f in all_flags if f["priority"] == "MEDIUM"),
        "LOW": sum(1 for f in all_flags if f["priority"] == "LOW"),
        "CANNOT": len(result["could_not_check"]),
    }
    return {
        "filename": name,
        "source_kind": result["source_kind"],
        "tables": result["tables"],
        "could_not_check": result["could_not_check"],
        "n_tables": len(result["tables"]),
        "n_flags": len(all_flags),
        "counts": counts,
    }


if __name__ == "__main__":
    import uvicorn
    # LOCAL ONLY: bind to loopback so the app is not reachable from other machines.
    uvicorn.run(app, host="127.0.0.1", port=8000)
