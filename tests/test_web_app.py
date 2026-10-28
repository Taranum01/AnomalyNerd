"""Smoke tests for the local web UI (web/app.py). Skipped when the optional web dependencies
(fastapi, httpx) are not installed, so the core suite runs without them."""
import csv
import importlib.util
import io
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "web", "app.py")


def _load_app():
    spec = importlib.util.spec_from_file_location("anomalynerd_web_app", APP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def web():
    return _load_app()


@pytest.fixture(scope="module")
def client(web):
    return TestClient(web.app)


def _example_bytes(name):
    with open(os.path.join(ROOT, "examples", name), "rb") as f:
        return f.read()


def test_home_page_lists_all_input_formats(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'accept=".csv,.xlsx,.pdf,.tex,.docx"' in r.text


def test_csv_upload_returns_known_trend(client):
    r = client.post("/analyze", files={"file": ("lottery.csv", _example_bytes("draft_lottery_1970.csv"), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_kind"] == "csv"
    assert any(f["type"] == "trend" for t in body["tables"] for f in t["flags"])
    assert body["counts"]["HIGH"] + body["counts"]["MEDIUM"] + body["counts"]["LOW"] == body["n_flags"]


def test_xlsx_upload_returns_outlier_located_by_sheet(client):
    openpyxl = pytest.importorskip("openpyxl")
    rows = list(csv.reader(io.StringIO(_example_bytes("newcomb_speed_of_light.csv").decode())))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(rows[0])
    for r in rows[1:]:
        ws.append([float(x) for x in r])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/analyze", files={"file": ("newcomb.xlsx", buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_kind"] == "xlsx"
    outliers = [f for t in body["tables"] for f in t["flags"] if f["type"] == "point_outlier"]
    assert outliers and all(f["table"] == "sheet 1" for f in outliers)


def test_unsupported_type_is_rejected(client):
    r = client.post("/analyze", files={"file": ("tool.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    assert "Unsupported file type" in r.json()["detail"]


def test_oversize_upload_is_rejected(web, client, monkeypatch):
    monkeypatch.setattr(web, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/analyze", files={"file": ("big.csv", b"a,b\n" * 20, "text/csv")})
    assert r.status_code == 413


def test_server_binds_loopback_only():
    """The UI has no authentication and accepts uploads, so it must only be reachable from the
    local machine. Guard the entry point against a wildcard bind being introduced."""
    src = open(APP_PATH).read()
    assert 'host="127.0.0.1"' in src
    assert "0.0.0.0" not in src
    assert 'host="::"' not in src


def test_statistics_checks_are_shown_and_counted(client):
    """A wrong reported p-value in a Word file appears as a HIGH flag card (counted and
    filterable), and a correct one is listed as checked out."""
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("We report t(10) = 5.04, p = .0005 and a second test t(28) = 2.20, p = .45.")
    t = d.add_table(rows=4, cols=2)
    for i, (a, b) in enumerate([("x", "y"), ("1", "10"), ("2", "12"), ("3", "15")]):
        t.cell(i, 0).text, t.cell(i, 1).text = a, b
    buf = io.BytesIO()
    d.save(buf)
    r = client.post("/analyze", files={"file": ("report.docx", buf.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 200, r.text
    body = r.json()
    stat_cards = [f for t in body["tables"] for f in t["flags"] if f["type"] == "statcheck"]
    assert len(stat_cards) == 1 and stat_cards[0]["priority"] == "HIGH"
    assert "0.45" in stat_cards[0]["suggestion"] and "0.036" in stat_cards[0]["suggestion"]
    assert body["counts"]["HIGH"] >= 1
    assert any("statcheck" in s and "consistent" in s for s in body["stat_ok"])
    # the statistics pseudo-table is not counted as an analyzed table
    assert all(t["name"] != "statistics in the text" for t in body["tables"][: body["n_tables"]])


def test_csv_table_uses_uploaded_file_name(client):
    r = client.post("/analyze", files={"file": ("lottery.csv", _example_bytes("draft_lottery_1970.csv"), "text/csv")})
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tables"]}
    assert names == {"lottery"}, names
