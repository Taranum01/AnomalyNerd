"""Word (.docx) and Excel (.xlsx) input: tables go through the same quality gate as PDF tables,
and known datasets produce their known findings through the full analyze_source path."""
import csv
import os

import pytest

docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")

from anomalynerd.analyze_pdf import analyze_source, format_report
from anomalynerd.office_ingest import parse_docx, parse_xlsx

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def _rows(name):
    with open(os.path.join(EX, name)) as f:
        return list(csv.reader(f))


def _write_docx(path, grids, paragraph=None):
    d = docx.Document()
    if paragraph:
        d.add_paragraph(paragraph)
    for grid in grids:
        t = d.add_table(rows=len(grid), cols=len(grid[0]))
        for i, r in enumerate(grid):
            for j, v in enumerate(r):
                t.cell(i, j).text = v
    d.save(path)


def _write_xlsx(path, grid, numeric=True, extra_empty_sheet=False):
    wb = openpyxl.Workbook()
    ws = wb.active
    for i, r in enumerate(grid):
        if i == 0 or not numeric:
            ws.append(r)
        else:
            out = []
            for x in r:
                try:
                    out.append(float(x))
                except ValueError:
                    out.append(x)
            ws.append(out)
    if extra_empty_sheet:
        wb.create_sheet("empty")
    wb.save(path)


def test_docx_trend_found_and_nonnumeric_table_reported(tmp_path):
    p = tmp_path / "paper.docx"
    _write_docx(p, [_rows("draft_lottery_1970.csv"),
                    [["Name", "Role"], ["Alice", "lead"], ["Bob", "member"], ["Cara", "member"]]],
                paragraph="We found an effect, t(28) = 2.20, p = .036, in the main analysis.")
    res = analyze_source(str(p))
    assert res.source_kind == "docx"
    assert [t.status for t in res.parse.tables] == ["ok", "skipped"]
    assert any(f.type == "trend" for fl in res.table_flags.values() for f in fl)
    # the text-only table is reported, not silently dropped
    assert any(c.startswith("table 2") and "no numeric column" in c for c in res.could_not_check)
    # reported statistics in Word body text are found like PDF body text
    assert any("t(28) = 2.20" in s for s in res.parse.stat_strings)
    assert format_report(res).splitlines()[0].startswith("AnomalyNerd Word document report")


def test_xlsx_outlier_found_and_short_values_not_rejected(tmp_path):
    """Newcomb's values are short ('28', '-44'); the PDF fragmented-figure heuristic would reject
    them, but a real spreadsheet grid must be analyzed."""
    p = tmp_path / "data.xlsx"
    _write_xlsx(p, _rows("newcomb_speed_of_light.csv"), extra_empty_sheet=True)
    res = analyze_source(str(p))
    assert res.source_kind == "xlsx"
    assert [t.status for t in res.parse.tables] == ["ok"]
    assert any(f.type == "point_outlier" for fl in res.table_flags.values() for f in fl)
    assert "sheet 'empty' is empty" in res.parse.notes
    assert format_report(res).splitlines()[0].startswith("AnomalyNerd Excel workbook report")


def test_xlsx_too_small_sheet_still_rejected(tmp_path):
    """structured=True skips only the PDF-fragmentation checks; the size gate still applies."""
    p = tmp_path / "tiny.xlsx"
    _write_xlsx(p, [["a", "b"], ["1", "2"]])
    parse = parse_xlsx(str(p))
    assert parse.tables[0].status not in ("ok", "partial")
    assert "too small" in parse.tables[0].reason


def test_docx_with_no_tables_reports_it(tmp_path):
    p = tmp_path / "prose.docx"
    _write_docx(p, [], paragraph="Just prose, no tables here at all.")
    parse = parse_docx(str(p))
    assert parse.tables == []
    assert "no tables were found in this file" in parse.notes
