"""HTML input: tables are read exactly (spans expanded), prose is scanned for reported
statistics, and unreadable files are reported honestly. No new dependency is added."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from anomalynerd.html_ingest import parse_html                 # noqa: E402
from anomalynerd.analyze_pdf import analyze_source, format_report  # noqa: E402
from anomalynerd.corpus_cli import SUPPORTED                   # noqa: E402

BASIC = """<!doctype html><html><body>
<p>The study reports t(28)=2.40, p=.02 for the main effect.</p>
<table>
<tr><th>Method</th><th>RMSE</th></tr>
<tr><td>ARIMA</td><td>86.44</td></tr>
<tr><td>MethodB</td><td>48.72</td></tr>
</table>
</body></html>"""

SPANS = """<table>
<tr><th>Setting</th><th colspan="2">Score</th></tr>
<tr><td rowspan="2">low</td><td>A</td><td>1.5</td></tr>
<tr><td>B</td><td>2.5</td></tr>
<tr><td>high</td><td>A</td><td>3.5</td></tr>
</table>"""


def _write(tmp_path, body, name="page.html"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


def test_reads_a_simple_table(tmp_path):
    res = parse_html(_write(tmp_path, BASIC))
    usable = [t for t in res.tables if t.status in ("ok", "partial")]
    assert usable and any("RMSE" in (t.header or []) for t in usable)


def test_body_text_statistics_are_collected(tmp_path):
    res = parse_html(_write(tmp_path, BASIC))
    assert any("t(28)" in s for s in res.stat_strings)


def test_end_to_end_statcheck_runs_on_prose(tmp_path):
    res = analyze_source(_write(tmp_path, BASIC))
    kinds = {s.kind for s in res.stat_findings}
    assert "statcheck" in kinds
    assert res.source_kind == "html"


def test_colspan_is_expanded(tmp_path):
    res = parse_html(_write(tmp_path, SPANS))
    t = res.tables[0]
    assert t.header[0] == "Setting"
    assert all(h.startswith("Score") for h in t.header[1:])


def test_rowspan_is_expanded_down(tmp_path):
    res = parse_html(_write(tmp_path, SPANS))
    rows = [r for t in res.tables for r in t.rows]
    assert ["low", "A", "1.5"] in rows
    assert ["low", "B", "2.5"] in rows   # the spanned 'low' is carried into the next row


def test_rowspan_of_three_covers_three_rows(tmp_path):
    html = """<table>
<tr><th>S</th><th>V</th></tr>
<tr><td rowspan="3">x</td><td>1</td></tr>
<tr><td>2</td></tr>
<tr><td>3</td></tr>
<tr><td>y</td><td>4</td></tr>
</table>"""
    res = parse_html(_write(tmp_path, html))
    rows = [r for t in res.tables for r in t.rows]
    assert rows[:3] == [["x", "1"], ["x", "2"], ["x", "3"]]
    assert rows[3] == ["y", "4"]


def test_multiple_tables_all_read(tmp_path):
    html = BASIC + "<table><tr><th>M</th><th>Acc</th></tr><tr><td>A</td><td>0.9</td></tr></table>"
    res = parse_html(_write(tmp_path, html))
    assert len(res.tables) == 2


def test_page_without_tables_says_so(tmp_path):
    res = parse_html(_write(tmp_path, "<html><body><p>No tables here.</p></body></html>"))
    assert res.tables == []
    assert any("no tables" in n for n in res.notes)


def test_script_and_style_content_is_ignored(tmp_path):
    html = ("<html><head><style>td { color: red }</style>"
            "<script>var t(99)=1; p=.99;</script></head><body>" + BASIC + "</body></html>")
    res = parse_html(_write(tmp_path, html))
    assert all("99" not in s for s in res.stat_strings)


def test_malformed_html_does_not_crash(tmp_path):
    res = parse_html(_write(tmp_path, "<table><tr><td>1<td>2<tr><td>3</table><p>tail"))
    assert res is not None and res.n_pages >= 0


def test_declared_charset_is_honoured(tmp_path):
    p = tmp_path / "latin.html"
    body = ('<html><head><meta charset="iso-8859-1"></head><body>'
            "<table><tr><th>M</th><th>V</th></tr><tr><td>caf\xe9</td><td>1.5</td></tr>"
            "<tr><td>a</td><td>2.5</td></tr><tr><td>b</td><td>3.5</td></tr></table>"
            "</body></html>")
    p.write_bytes(body.encode("latin-1"))
    res = parse_html(str(p))
    cells = [c for t in res.tables for r in t.rows for c in r]
    assert any("caf" in c for c in cells)


def test_detectors_run_on_html_table(tmp_path):
    html = """<table><tr><th>p1</th><th>value</th></tr>
<tr><td>0.10</td><td>1.6</td></tr><tr><td>0.12</td><td>1.7</td></tr>
<tr><td>0.14</td><td>196</td></tr><tr><td>0.16</td><td>209</td></tr><tr><td>0.18</td><td>214</td></tr>
</table>"""
    res = analyze_source(_write(tmp_path, html))
    assert any(f.type == "numeric_jump" for flags in res.table_flags.values() for f in flags)


def test_report_labels_html_source(tmp_path):
    res = analyze_source(_write(tmp_path, BASIC))
    assert "HTML document" in format_report(res)


def test_json_report_via_cli(tmp_path):
    path = _write(tmp_path, BASIC)
    out = subprocess.run([sys.executable, "-m", "anomalynerd.pdf_cli", path, "--json"],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    rep = json.loads(out.stdout)
    assert rep["tables_detected"] == 1


def test_htm_extension_accepted(tmp_path):
    res = analyze_source(_write(tmp_path, BASIC, name="page.htm"))
    assert res.source_kind == "html"


def test_corpus_supports_html():
    assert ".html" in SUPPORTED and ".htm" in SUPPORTED


def test_web_upload_accepts_html(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import importlib.util
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location(
        "anomalynerd_web_app_html", os.path.join(ROOT, "web", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    client = TestClient(mod.app)
    r = client.post("/analyze", files={"file": ("page.html", BASIC.encode(), "text/html")})
    assert r.status_code == 200, r.text
    assert r.json()["source_kind"] == "html"
