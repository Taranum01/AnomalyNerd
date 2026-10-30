import csv, math
import pytest
from anomalynerd.ingest import read_csv, read_tables, _parse_number
from anomalynerd.model import MISSING, is_num
from anomalynerd.stat_tests import digit_tests

def write(tmp_path,header,rows):
    p=tmp_path/'table.csv'
    with p.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(header);w.writerows(rows)
    return str(p)

@pytest.mark.parametrize('token',['inf','-inf','1e999'])
def test_nonfinite_excluded(token):
    assert _parse_number(token) is MISSING
    assert not is_num(float(token))

def test_accuracy_direction_default(tmp_path):
    t=read_csv(write(tmp_path,['method','accuracy'],[['A',.9],['B',.8]]))
    assert not t.lower_is_better

def test_monotonic_named_metrics_not_lost(tmp_path):
    p=write(tmp_path,['threshold','accuracy','latency'],[[i/100,.5+i/1000,100+i] for i in range(100)])
    tables=read_tables(p)
    assert sorted(t.metric_name for t in tables)==['accuracy','latency']
    assert all('threshold' in t.axes for t in tables)
    assert not next(t for t in tables if t.metric_name=='accuracy').lower_is_better

def test_same_scale_different_metrics_split(tmp_path):
    p=write(tmp_path,['method','accuracy','loss'],[[f'M{i}',.8+(i%7)/100,.3+(i%5)/100] for i in range(100)])
    assert sorted(t.metric_name for t in read_tables(p))==['accuracy','loss']

def test_raw_terminal_precision_preserved(tmp_path):
    p=write(tmp_path,['idx','value'],[[i,f'{1+i/100:.3f}'] for i in range(100)])
    t=read_csv(p,metric_col='value')
    assert t.rows[0].get('_raw')=='1.000'
    result=next(x for x in digit_tests(t) if x.kind=='terminal_digit')
    assert result.status=='finding' and result.evidence['n']==100

def test_bounded_values_not_benford_accusation(tmp_path):
    p=write(tmp_path,['idx','accuracy'],[[i,f'{.8+(i%10)/100:.2f}'] for i in range(100)])
    result=next(x for x in digit_tests(read_csv(p)) if x.kind=='benford')
    assert result.status=='not_applicable'

@pytest.mark.parametrize('token,expected', [
    ('0.91 [0.88, 0.94]', 0.91),
    ('0.91 (0.03)', 0.91),
    ('8×10^{-3}', 0.008),
    ('8 \\times 10^3', 8000.0),
    ('8 10^-3', 0.008),
])
def test_numeric_cells_keep_central_value(token, expected):
    assert _parse_number(token) == expected


def test_bracketed_interval_column_is_numeric(tmp_path):
    p = write(tmp_path, ['method', 'accuracy'], [
        ['A', '0.91 [0.88, 0.94]'],
        ['B', '0.87 [0.84, 0.90]'],
        ['C', '0.84 [0.81, 0.87]'],
    ])
    t = read_csv(p, metric_col='accuracy')
    assert [r['value'] for r in t.rows] == [0.91, 0.87, 0.84]
    assert [r['_raw'] for r in t.rows][0] == '0.91 [0.88, 0.94]'

@pytest.mark.parametrize('name,lower', [
    ('Human-Eval.pass@1', False), ('TruthfulQA ↑', False), ('ToxiGen ↓', True),
    ('BLEU', False), ('ROUGE-L', False), ('Top-1 err.', True), ('Top-1 Acc.', False),
    ('ELI5 PPL', True), ('Sentiment accuracy', False), ('RMSE', True), ('CoLA', True)])
def test_metric_direction_from_name(tmp_path, name, lower):
    t = read_csv(write(tmp_path, ['method', name], [['A', 1.5], ['B', 2.5], ['C', 3.5]]))
    assert t.lower_is_better is lower

@pytest.mark.parametrize('header,values,expected', [
    ('col1', ['7B', '30B', '7B', '40B', '70B'], True),
    ('#param.', ['28M', '50M', '89M', '197M', '937M'], True),
    ('params', ['86 M', '304 M', '632 M', '6801 M'], True),
    ('col1', ['7B', '30B', '-', '70B'], True),
    ('Params', ['7B', '13B', '65.3', '70B'], False),
    ('Accuracy', ['7B', '30B', '65B'], False),
    ('col2', ['18.3', '25.0', '0.6', '29.9'], False)])
def test_param_count_column(tmp_path, header, values, expected):
    from anomalynerd.analyze_pdf import _is_param_count_column
    rows = [[f'm{i}', v] for i, v in enumerate(values)]
    t = read_csv(write(tmp_path, ['method', header], rows), metric_col=header)
    assert _is_param_count_column(t) is expected


def test_dash_label_is_not_an_entity():
    from anomalynerd.model import TidyTable, Axis
    from anomalynerd.detectors import detect_duplicates_and_flatness
    rows = [{"enc": e, "speedup": s, "value": v} for e, s, v in
            [("L", "-", 8.0), ("H", "-", 8.0), ("L", "2.8x", 8.0), ("H", "3.5x", 1.0)]]
    t = TidyTable("t", {"enc": Axis("enc", "unordered_cat"), "speedup": Axis("speedup", "unordered_cat")},
                  entity_axis="speedup", metric_name="depth", lower_is_better=True, rows=rows)
    assert not [f for f in detect_duplicates_and_flatness(t) if f.coords.get("speedup") == "-"]


def test_no_flatness_across_dev_and_test_rows():
    from anomalynerd.model import TidyTable, Axis
    from anomalynerd.detectors import detect_duplicates_and_flatness
    rows = [{"split": s, "method": m, "value": v} for s, m, v in
            [("Dev PPL", "A", 21.6), ("Test PPL", "A", 21.6), ("Dev PPL", "B", 20.7), ("Test PPL", "B", 20.0)]]
    t = TidyTable("t", {"split": Axis("split", "unordered_cat"), "method": Axis("method", "unordered_cat")},
                  entity_axis="method", metric_name="value", lower_is_better=True, rows=rows)
    assert not [f for f in detect_duplicates_and_flatness(t) if f.type == "flatness"]


def test_flatness_still_fires_across_real_settings():
    from anomalynerd.model import TidyTable, Axis
    from anomalynerd.detectors import detect_duplicates_and_flatness
    rows = [{"loss": s, "method": m, "value": v} for s, m, v in
            [("CE loss", "A", 21.6), ("Focal loss", "A", 21.6), ("CE loss", "B", 20.7), ("Focal loss", "B", 20.0)]]
    t = TidyTable("t", {"loss": Axis("loss", "unordered_cat"), "method": Axis("method", "unordered_cat")},
                  entity_axis="method", metric_name="value", lower_is_better=True, rows=rows)
    assert [f for f in detect_duplicates_and_flatness(t) if f.type == "flatness"]


def test_side_by_side_tables_are_split(tmp_path):
    header = ["Gender.Descriptor", "Gender.%Doc", "Religion.Descriptor", "Religion.%Doc"]
    rows = [["female", "50.0%", "christian", "33.2%"], ["male", "39.1%", "religious", "28.8%"],
            ["feminine", "5.4%", "spiritual", "20.6%"]]
    tables = read_tables(write(tmp_path, header, rows))
    assert len(tables) == 2
    assert all(len(t.rows) == 3 for t in tables)


def test_total_row_left_out_of_checks():
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [["Batch", "Comparisons", "Turns"]] + [[str(i), str(1000 * i), "3.9"] for i in range(1, 8)] \
        + [["Total", "28000", "3.9"]]
    et = _assess_and_tidy(rows, 1, 0)
    assert et.status in ("ok", "partial")
    assert all(r.get("Batch") != "Total" for td in et.tidies for r in td.rows)
    assert any("total row" in c for c in et.caveats)


@pytest.mark.parametrize('cell,fused', [('29.13 35.25', True), ('7B 30B', True), ('89 91', True),
                                        ('1 024', False), ('8.3 (+1.9)', False), ('29.13', False), ('10.1% 24.5% 28.2%', True)])
def test_fused_cell_detection(cell, fused):
    from types import SimpleNamespace as NS
    from anomalynerd.analyze_pdf import _has_fused_cells
    assert _has_fused_cells(NS(rows=[["m", cell]])) is fused


def test_fused_cells_split_back_into_rows():
    from anomalynerd.pdf_ingest import _unfuse_rows
    down, n = _unfuse_rows([["MPT", "7B", "0.25 0.23"], ["", "30B", ""], ["Falcon", "7B", "0.26"]])
    assert n == 1 and [r[2] for r in down] == ["0.25", "0.23", "0.26"]
    split, n = _unfuse_rows([["MPT", "7B 30B", "29.13 35.25"], ["Falcon", "7B", "25.95"]])
    assert n == 2 and split[:2] == [["MPT", "7B", "29.13"], ["MPT", "30B", "35.25"]]
    kept, n = _unfuse_rows([["MPT", "7B", "29.13 35.25"], ["Falcon", "40B", "25.95"]])
    assert n == 0 and kept[0][2] == "29.13 35.25"
    up, n = _unfuse_rows([["YOLOR-E6", "683.2G", ""], ["YOLOR-D6", "935.6G", "45 34"]])
    assert n == 1 and [r[2] for r in up] == ["45", "34"]
