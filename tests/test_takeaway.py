"""Suggested takeaway: built only from what the tool computed, and silent when unsure."""
import csv
import os

import pytest

from anomalynerd.analyze import analyze
from anomalynerd.ingest import read_csv, read_tables
from anomalynerd.model import Axis, TidyTable
from anomalynerd.takeaway import generate_takeaway, table_takeaway

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def write(tmp_path, header, rows, name="t.csv"):
    p = tmp_path / name
    with p.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    return str(p)


def test_expected_flat_trend_is_called_out():
    t = read_csv(os.path.join(EX, "draft_lottery_1970.csv"))
    axis = next(a for a, ax in t.axes.items() if ax.kind == "numeric")
    tk = generate_takeaway(t, analyze(t, expect_flat=[axis]))
    assert any("expected not to matter" in s for s in tk)


def test_winner_with_known_direction_says_best():
    t = read_csv(os.path.join(EX, "long.csv"))
    tk = generate_takeaway(t, analyze(t))
    assert tk[0] == "ARIMA has the best RMSE in 3 of 4 settings."


def test_unknown_direction_says_highest(tmp_path):
    t = read_csv(write(tmp_path, ["group", "score_x"], [["a", 3], ["b", 9], ["c", 5]]), metric_col="score_x")
    t.metric_name = "widgets"
    assert generate_takeaway(t, [])[0] == "b has the highest widgets (9), ahead of c (5)."


def test_explicit_direction_from_the_user_is_used(tmp_path):
    t = read_csv(write(tmp_path, ["group", "widgets"], [["a", 3], ["b", 9], ["c", 5]]),
                 metric_col="widgets", lower_is_better=True)
    assert generate_takeaway(t, [], direction_known=True)[0] == "a has the best widgets (3), ahead of c (5)."


def test_raw_observations_get_no_winner():
    t = read_csv(os.path.join(EX, "anscombe.csv"))
    assert not any("highest" in s or "best" in s for s in generate_takeaway(t, analyze(t)))


def test_a_tie_has_no_winner(tmp_path):
    t = read_csv(write(tmp_path, ["group", "accuracy"], [["a", 9], ["b", 9], ["c", 5]]))
    assert generate_takeaway(t, []) == []


def test_paper_table_is_summarised_across_result_columns(tmp_path):
    header = ["Model", "Params", "CoLA", "SST", "MRPC", "RTE"]
    rows = [["BERT", "335M", 60.6, 93.2, 88.0, 70.4], ["RoBERTa", "356M", 66.1, 95.6, 91.4, 82.7],
            ["ELECTRA", "335M", 69.3, 96.0, 90.6, 86.8]]
    tidies = read_tables(write(tmp_path, header, rows))
    tk = table_takeaway(tidies, [analyze(td) for td in tidies])
    assert tk[0].startswith("ELECTRA has the highest value in 3 of 4 result columns")
    assert "Params" not in tk[0] and "RoBERTa in MRPC" in tk[0]


def test_hyperparameter_rows_are_not_a_contest(tmp_path):
    rows = [["Learning rate", 3e-4, 1e-4, 5e-5], ["Warmup steps", 10000, 10000, 10000],
            ["Batch size", 128, 256, 2048], ["Dropout", 0.1, 0.1, 0.1]]
    t = read_csv(write(tmp_path, ["Hyperparameter", "Small", "Base", "Large"], rows))
    assert generate_takeaway(t, []) == []


def test_check_sentence_hides_internal_fields(tmp_path):
    from anomalynerd.model import Flag
    t = TidyTable("t", {"Model": Axis("Model", "unordered_cat")}, "Model", "MRPC", False,
                  [{"Model": m, "value": v} for m, v in (("a", 90.0), ("b", 91.0), ("c", 88.0))])
    f = Flag(type="point_outlier", table="t", axis=None, coords={"Model": "c", "_raw": "88.0"},
             detail={"value": 88.0}, strength=9.0, support=1.0, priority="HIGH")
    s = generate_takeaway(t, [f])[-1]
    assert "Model=c" in s and "_raw" not in s


def test_a_failing_takeaway_never_breaks_the_report(monkeypatch, tmp_path):
    from anomalynerd import analyze_pdf, takeaway
    monkeypatch.setattr(takeaway, "table_takeaway", lambda *a: 1 / 0)
    from anomalynerd.pdf_ingest import _assess_and_tidy
    from types import SimpleNamespace as NS
    et = _assess_and_tidy([["Model", "Acc"], ["a", "90.1"], ["b", "91.2"], ["c", "92.3"]], 1, 0)
    parse = NS(path="x.pdf", tables=[et], stat_strings=[], notes=[], n_pages=1, text_pages=1)
    res = analyze_pdf._analyze_parse("x.pdf", parse, None)
    assert res.takeaways == {} and analyze_pdf.format_report(res)


def test_pipeline_stores_one_takeaway_per_table():
    from anomalynerd import analyze_pdf
    from anomalynerd.pdf_ingest import _assess_and_tidy
    from types import SimpleNamespace as NS
    et = _assess_and_tidy([["Model", "Accuracy"], ["a", "90.1"], ["b", "91.2"], ["c", "92.3"]], 1, 0)
    parse = NS(path="x.pdf", tables=[et], stat_strings=[], notes=[], n_pages=1, text_pages=1)
    res = analyze_pdf._analyze_parse("x.pdf", parse, None)
    assert res.takeaways[(1, 0)][0].startswith("c has the best Accuracy")
    assert "Suggested takeaway" in analyze_pdf.format_report(res)


def test_numbers_are_not_names(tmp_path):
    t = read_csv(write(tmp_path, ["BLEU", "METEOR"], [["47.1±.1", 0.39], ["45.2", 0.38], ["44.9", 0.37]]),
                 metric_col="METEOR")
    assert generate_takeaway(t, []) == []


def test_small_low_confidence_shift_is_not_a_takeaway():
    from anomalynerd.model import Flag
    t = TidyTable("t", {"r": Axis("r", "numeric")}, None, "val loss", True,
                  [{"r": i, "value": 1.18} for i in range(8)])
    f = Flag(type="level_shift", table="t", axis="r", coords={}, strength=3, support=0.4,
             detail={"baseline_mean": 1.18, "shifted_mean": 1.16, "shift_span": [5, 7]}, priority="LOW")
    assert generate_takeaway(t, [f]) == []
    f.detail.update(baseline_mean=301.0, shifted_mean=169.0)
    assert "about 301 before, 169 after" in generate_takeaway(t, [f])[0]


def test_unclear_wide_paper_table_names_no_winner():
    rows = [{"System": s, "MNLI": m, "method": c, "value": v}
            for s, m in (("BERT", "84.6/83.4"), ("GPT", "82.1/81.4"))
            for c, v in (("QQP", 71.2), ("SST", 94.9), ("CoLA", 60.5))]
    t = TidyTable("t", {"System": Axis("System", "unordered_cat"), "MNLI": Axis("MNLI", "unordered_cat"),
                        "method": Axis("method", "unordered_cat")}, "method", "value", True, rows)
    assert generate_takeaway(t, []) == []


def test_rows_that_are_benchmarks_get_no_winner():
    rows = [{"Benchmark": b, "method": m, "value": v}
            for b, vals in (("TriviaQA", (87, 82, 86)), ("MMLU", (70, 86, 69)), ("GSM8K", (57, 92, 56)))
            for m, v in zip(("GPT-3.5", "GPT-4", "PaLM"), vals)]
    t = TidyTable("t", {"Benchmark": Axis("Benchmark", "unordered_cat"), "method": Axis("method", "unordered_cat")},
                  "method", "value", True, rows)
    assert generate_takeaway(t, []) == []


@pytest.mark.parametrize("name,setting", [("dmodel", True), ("d_model", True), ("dk", True),
                                          ("Train FLOPs", True), ("# Params", True), ("BLEU", False),
                                          ("CoLA", False), ("top-1 err.", False), ("Accuracy", False)])
def test_setting_columns(name, setting):
    from anomalynerd.takeaway import _is_setting_column
    assert _is_setting_column(name) is setting


def test_flatness_takeaway_names_the_flagged_column_not_value():
    """co2_annual: the flatness warning is about the 'unc' column, and the takeaway must say so."""
    t = read_csv(os.path.join(EX, "co2_annual.csv"))
    tk = generate_takeaway(t, analyze(t))
    check = [s for s in tk if s.startswith("Before relying on it")]
    assert check and "unc is identical across year" in check[0]
    assert "value is identical" not in check[0]
    assert "method=unc" not in check[0]
    assert tk[0].startswith("mean rises as year goes up")


def test_web_analyze_returns_takeaways():
    """The /analyze response must carry the takeaways the page renders."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import importlib.util
    from fastapi.testclient import TestClient
    root = os.path.dirname(EX)
    spec = importlib.util.spec_from_file_location("web_app_takeaway_test", os.path.join(root, "web", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with open(os.path.join(EX, "co2_annual.csv"), "rb") as f:
        r = TestClient(mod.app).post("/analyze", files={"file": ("co2_annual.csv", f.read(), "text/csv")})
    assert r.status_code == 200
    tks = r.json()["takeaways"]
    assert tks and tks[0]["table"] == "co2_annual"
    joined = " ".join(tks[0]["sentences"])
    assert "unc is identical across year" in joined and "value is identical" not in joined


@pytest.mark.parametrize("name", ["Params", "Params (M)", "#Params", "FLOPs", "Training time",
                                  "Step count", "Batch size", "GPU hours", "Learning rate",
                                  "Tokens", "d_model", "Latency (ms)", "Memory", "Layers"])
def test_cost_columns_are_settings(name):
    from anomalynerd.takeaway import _is_setting_column
    assert _is_setting_column(name)


@pytest.mark.parametrize("name", ["Sentiment", "Headline", "Timeline", "Maritime", "Overhead",
                                  "Accuracy", "SST", "F1", "Dimitrov"])
def test_result_columns_that_contain_a_cost_word_are_not_settings(name):
    from anomalynerd.takeaway import _is_setting_column
    assert not _is_setting_column(name)


def test_sentiment_column_still_gets_a_winner(tmp_path):
    tidies = read_tables(write(tmp_path, ["System", "Sentiment"], [["A", 91], ["B", 80], ["C", 75]]))
    tk = table_takeaway(tidies, [analyze(td) for td in tidies])
    assert tk and tk[0] == "A has the highest Sentiment (91), ahead of B (80)."


def test_params_column_still_has_no_winner(tmp_path):
    tidies = read_tables(write(tmp_path, ["System", "Params"], [["A", 91], ["B", 80], ["C", 75]]))
    tk = table_takeaway(tidies, [analyze(td) for td in tidies])
    assert not any("has the highest" in s for s in tk)
