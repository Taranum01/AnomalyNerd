"""Cross-table inconsistency findings must reach every report surface.

Regression tests for the bug where the engine computed cross_table_flags but the
machine-readable JSON report (pdf_cli --json, and with it the saved corpus review
reports) and the local web UI silently dropped them: a paper whose two tables
contradicted each other produced a text report with a HIGH finding, an empty JSON
consumer view and a web page showing zero flags.
"""
import importlib.util
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from anomalynerd.analyze_pdf import analyze_source            # noqa: E402
from anomalynerd.pdf_cli import main as pdf_main              # noqa: E402
from anomalynerd.corpus_cli import _coverage, _valid_report   # noqa: E402

CONTRADICTION_TEX = r"""
\begin{table}
\caption{Main results}
\begin{tabular}{lr}
Method & RMSE \\
A & 1.61 \\
B & 2.10 \\
\end{tabular}
\end{table}
\begin{table}
\caption{Ablation}
\begin{tabular}{lr}
Method & RMSE \\
A & 1.83 \\
B & 2.10 \\
\end{tabular}
\end{table}
"""

CONSISTENT_TEX = r"""
\begin{table}
\begin{tabular}{lr}
Method & RMSE \\
A & 1.61 \\
\end{tabular}
\end{table}
\begin{table}
\begin{tabular}{lr}
Method & RMSE \\
A & 1.61 \\
\end{tabular}
\end{table}
"""


@pytest.fixture(scope="module")
def contradiction(tmp_path_factory):
    p = tmp_path_factory.mktemp("xt") / "paper.tex"
    p.write_text(CONTRADICTION_TEX)
    return str(p)


@pytest.fixture(scope="module")
def consistent(tmp_path_factory):
    p = tmp_path_factory.mktemp("xtok") / "paper.tex"
    p.write_text(CONSISTENT_TEX)
    return str(p)


def _json_report(capsys, path):
    assert pdf_main([path, "--json"]) == 0
    out = capsys.readouterr().out
    return json.loads(out)   # json.loads rejects NaN/Infinity tokens


# ---- pdf_cli --json ---------------------------------------------------------

def test_json_report_includes_cross_table_flags(capsys, contradiction):
    rep = _json_report(capsys, contradiction)
    assert "cross_table_flags" in rep, "JSON report dropped the cross-table findings"
    assert len(rep["cross_table_flags"]) >= 1
    text = json.dumps(rep["cross_table_flags"])
    assert "1.61" in text and "1.83" in text


def test_json_cross_table_flags_match_engine(capsys, contradiction):
    rep = _json_report(capsys, contradiction)
    res = analyze_source(contradiction)
    assert len(rep["cross_table_flags"]) == len(res.cross_table_flags)
    assert all(f["type"] == "cross_table_inconsistency" for f in rep["cross_table_flags"])


def test_json_consistent_paper_has_empty_cross_table_flags(capsys, consistent):
    rep = _json_report(capsys, consistent)
    assert rep["cross_table_flags"] == []


def test_json_report_is_strictly_serializable(capsys, contradiction):
    assert pdf_main([contradiction, "--json"]) == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    json.dumps(parsed, allow_nan=False)   # raises if any float is inf/NaN


def test_text_report_still_shows_cross_table_section(capsys, contradiction):
    assert pdf_main([contradiction]) == 0
    out = capsys.readouterr().out
    assert "Cross-table consistency" in out


# ---- web UI -----------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location(
        "anomalynerd_web_app", os.path.join(ROOT, "web", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return TestClient(mod.app), mod


def _upload(client_pair, path, name):
    client, _ = client_pair
    with open(path, "rb") as f:
        r = client.post("/analyze", files={"file": (name, f, "application/x-tex")})
    assert r.status_code == 200, r.text
    return r.json()


def test_web_response_shows_cross_table_flag(client, contradiction):
    body = _upload(client, contradiction, "paper.tex")
    _, mod = client
    xt = [t for t in body["tables"] if t["name"] == mod.XT_TABLE]
    assert xt, "web response dropped the cross-table findings"
    assert any(f["type"] == "cross_table_inconsistency" for f in xt[0]["flags"])


def test_web_counts_include_cross_table_priority(client, contradiction):
    body = _upload(client, contradiction, "paper.tex")
    n_xt_high = sum(1 for t in body["tables"] for f in t["flags"]
                    if f["type"] == "cross_table_inconsistency" and f["priority"] == "HIGH")
    assert n_xt_high >= 1
    assert body["counts"]["HIGH"] >= n_xt_high
    assert body["counts"]["HIGH"] + body["counts"]["MEDIUM"] + body["counts"]["LOW"] == body["n_flags"]


def test_web_consistent_paper_has_no_cross_table_section(client, consistent):
    body = _upload(client, consistent, "paper.tex")
    _, mod = client
    assert all(t["name"] != mod.XT_TABLE for t in body["tables"])


def test_web_n_tables_excludes_cross_table_section(client, contradiction):
    body = _upload(client, contradiction, "paper.tex")
    _, mod = client
    n_real = sum(1 for t in body["tables"] if t["name"] not in (mod.STATS_TABLE, mod.XT_TABLE))
    assert body["n_tables"] == n_real


def test_web_csv_upload_has_no_cross_table_section(client):
    client_obj, mod = client
    csv_bytes = b"method,rmse\nA,1.61\nB,2.10\n"
    r = client_obj.post("/analyze", files={"file": ("t.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert r.status_code == 200
    assert all(t["name"] != mod.XT_TABLE for t in r.json()["tables"])


# ---- corpus review (saved reports are the pdf_cli JSON) ----------------------

def _base_report(source):
    return {
        "path": str(source), "pages": 1, "tables_detected": 1, "tables_extraction_usable": 1,
        "metric_slices_completed": 1,
        "extraction": [{"status": "ok"}], "pattern_flags": [], "cross_table_flags": [],
        "stat_findings": [{"status": "ok"}], "could_not_check": [], "takeaways": [],
        "detector_runs": {"k": [{"status": "completed"}]}, "coverage_unit": "regions",
    }


def test_corpus_valid_report_requires_cross_table_flags_list(tmp_path):
    source = tmp_path / "a.tex"
    source.write_text("x")
    assert _valid_report(_base_report(source), source)
    broken = _base_report(source)
    del broken["cross_table_flags"]
    assert not _valid_report(broken, source)


def test_corpus_valid_report_rejects_non_list_cross_table_flags(tmp_path):
    source = tmp_path / "a.tex"
    source.write_text("x")
    broken = _base_report(source)
    broken["cross_table_flags"] = "not-a-list"
    assert not _valid_report(broken, source)


def test_corpus_coverage_counts_cross_table_flags(tmp_path):
    source = tmp_path / "a.tex"
    source.write_text("x")
    rep = _base_report(source)
    assert _coverage(rep)["cross_table_flags"] == 0
    rep["cross_table_flags"] = [{"type": "cross_table_inconsistency"}]
    assert _coverage(rep)["cross_table_flags"] == 1
