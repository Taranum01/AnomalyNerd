"""Copied rows, low-precision duplicates, derived-quantity rows and settings tables."""
import random

from anomalynerd.analyze import analyze
from anomalynerd.detectors import _sig_digits
from anomalynerd.ingest import read_csv


def _write(tmp_path, header, rows, name="t.csv"):
    p = tmp_path / name
    p.write_text("\n".join([",".join(header)] + [",".join(str(c) for c in r) for r in rows]))
    return str(p)


def _random_table(tmp_path, seed=3, decimals=2, copy=None, n_rows=8, n_cols=6):
    rng = random.Random(seed)
    rows = [[f"model{i}"] + [round(rng.uniform(40, 90), decimals) for _ in range(n_cols)] for i in range(n_rows)]
    if copy:
        src, dst = copy
        rows[dst][1:] = rows[src][1:]
    return _write(tmp_path, ["name"] + [f"task{j}" for j in range(n_cols)], rows)


def test_copied_row_is_flagged(tmp_path):
    flags = analyze(read_csv(_random_table(tmp_path, copy=(1, 4))))
    copies = [f for f in flags if f.detail.get("kind") == "row_copy"]
    assert copies and copies[0].priority in ("HIGH", "MEDIUM")
    assert "model4" in copies[0].suggestion and "model1" in copies[0].suggestion


def test_untouched_table_has_no_copied_row(tmp_path):
    flags = analyze(read_csv(_random_table(tmp_path)))
    assert not [f for f in flags if f.detail.get("kind") == "row_copy"]


def test_copied_row_with_coarse_numbers_is_not_flagged(tmp_path):
    # integer percentages in a narrow range repeat by chance, so three equal cells prove nothing
    rows = [["a", 50, 51, 52, 53], ["b", 50, 51, 52, 54], ["c", 49, 53, 51, 50], ["d", 52, 50, 53, 51]]
    flags = analyze(read_csv(_write(tmp_path, ["name", "t1", "t2", "t3", "t4"], rows)))
    assert not [f for f in flags if f.detail.get("kind") == "row_copy"]


def test_repeated_constant_row_is_not_a_copy(tmp_path):
    rows = [["a", 0.03, 0.03, 0.03, 0.03, 0.03], ["b", 0.03, 0.03, 0.03, 0.03, 0.03],
            ["c", 0.07, 0.08, 0.06, 0.09, 0.05], ["d", 0.11, 0.12, 0.10, 0.13, 0.09]]
    flags = analyze(read_csv(_write(tmp_path, ["name", "t1", "t2", "t3", "t4", "t5"], rows)))
    assert not [f for f in flags if f.detail.get("kind") == "row_copy"]


def test_significant_digits():
    assert _sig_digits(0.01) == 1 and _sig_digits(0.002) == 1
    assert _sig_digits(0.1596) == 4 and _sig_digits(72.27) == 4 and _sig_digits(4.03) == 3


def test_low_precision_value_repeated_is_not_a_duplicate(tmp_path):
    rows = [[f"n={n}", 0.01, 0.01, 0.02 + 0.001 * n, 0.05] for n in range(1, 8)]
    flags = analyze(read_csv(_write(tmp_path, ["size", "m1", "m2", "m3", "m4"], rows)))
    assert not [f for f in flags if f.type == "duplicate" and f.detail.get("value") == 0.01]


def _raw_table(rows):
    from types import SimpleNamespace
    return SimpleNamespace(rows=rows, header=["name"] + [f"c{j}" for j in range(len(rows[0]) - 1)], index_on_page=0)


def test_raw_table_copied_row_is_flagged():
    from anomalynerd.analyze_pdf import _copied_rows_raw
    rows = [["A", "0.2861", "-0.6707", "-0.5972", "-0.4643"], ["B", "0.3097", "-0.6518", "-0.6661", "-0.4533"],
            ["C", "0.2861", "-0.6707", "-0.5972", "-0.4643"], ["D", "0.1100", "-0.5018", "-0.3661", "-0.2533"]]
    flags = _copied_rows_raw(_raw_table(rows))
    assert len(flags) == 1 and flags[0].detail["target"] == "C" and flags[0].detail["copied_from"] == "A"


def test_raw_table_without_copy_or_with_coarse_numbers_is_quiet():
    from anomalynerd.analyze_pdf import _copied_rows_raw
    coarse = [["A", "50", "51", "52"], ["B", "50", "51", "52"], ["C", "49", "53", "51"], ["D", "52", "50", "53"]]
    assert _copied_rows_raw(_raw_table(coarse)) == []
    distinct = [["A", "0.2861", "-0.6707", "-0.5972"], ["B", "0.3097", "-0.6518", "-0.6661"], ["C", "0.1100", "-0.5018", "-0.3661"]]
    assert _copied_rows_raw(_raw_table(distinct)) == []


def test_architecture_table_with_shared_design_choices_is_quiet():
    from anomalynerd.analyze_pdf import _copied_rows_raw
    rows = [["BERT-Base", "12", "768", "3072", "12", "110"], ["BERT-Large", "24", "1024", "4096", "16", "335"],
            ["RoBERTa", "24", "1024", "4096", "16", "355"], ["XLM-R Base", "12", "768", "3072", "12", "270"],
            ["XLM-R", "24", "1024", "4096", "16", "550"], ["mBERT", "12", "768", "3072", "12", "172"]]
    assert _copied_rows_raw(_raw_table(rows)) == []
