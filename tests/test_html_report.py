from anomalynerd.html_report import render_report
from anomalynerd.model import Flag, TidyTable


def test_report_escapes_all_table_content():
    payload = '<img src=x onerror="alert(1)">'
    table = TidyTable(payload, {}, None, payload, False, [])
    flag = Flag(
        payload,
        payload,
        payload,
        {payload: payload},
        {payload: payload},
        1,
        1,
        "HIGH",
        payload,
    )
    report = render_report(table, [flag], payload)
    assert payload not in report
    assert "&lt;img" in report
    assert 'data-priority="HIGH"' in report
    assert "<script src=" not in report


def test_report_empty_state_and_priority_counts():
    table = TidyTable("Empty", {}, None, "score", False, [])
    report = render_report(table, [])
    assert "No findings flagged for this table." in report
    assert report.count("<b>0</b>") == 3
    assert "0 shown" in report
