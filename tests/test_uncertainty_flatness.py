"""A constant uncertainty column must not raise a MEDIUM flatness flag; other constant columns still do."""
import os
from anomalynerd.ingest import read_csv
from anomalynerd.analyze import analyze
from anomalynerd.detectors import _is_uncertainty_label

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def test_co2_constant_uncertainty_column_is_low():
    flags = analyze(read_csv(os.path.join(EX, "co2_annual.csv")))
    flat = [f for f in flags if f.type == "flatness"]
    assert flat and all(f.priority == "LOW" for f in flat)
    assert not [f for f in flags if f.priority in ("HIGH", "MEDIUM")]


def test_other_constant_column_still_medium(tmp_path):
    text = open(os.path.join(EX, "co2_annual.csv")).read().replace("year,mean,unc", "year,mean,weight", 1)
    p = tmp_path / "t.csv"
    p.write_text(text)
    flat = [f for f in analyze(read_csv(str(p))) if f.type == "flatness"]
    assert flat and all(f.priority == "MEDIUM" for f in flat)


def test_uncertainty_label_matching():
    for ok in ("unc", "Uncertainty", "std err", "stderr", "SD", "standard error"):
        assert _is_uncertainty_label(ok)
    for no in ("score", "error rate", "method A", "mean", None, 3):
        assert not _is_uncertainty_label(no)
