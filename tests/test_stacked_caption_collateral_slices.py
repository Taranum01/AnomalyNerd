"""Pinned end-to-end outputs on real single-page slices that must be unaffected by the
stacked-caption routing (same flag types/priorities and table backends as before the change,
measured with and without the routing on this base). One run per slice; not whole papers."""
import os

import pytest

FIX = os.path.join(os.path.dirname(__file__), "fixtures")

PINS = {
    # slice: (flag (type, priority) list, sorted table backends)
    "pythia_2304.01373_p21": ([("level_shift", "LOW"), ("trend", "LOW"), ("trend", "LOW")], ["pdfplumber"]),
    "xlmr_1911.02116_p12": ([("point_outlier", "HIGH")] * 3, ["docling", "pdfplumber"]),
    "flan_2210.11416_p46": ([("point_outlier", "HIGH")], ["docling", "docling", "pdfplumber"]),
}


@pytest.mark.parametrize("name", sorted(PINS))
def test_slice_flags_unchanged(name):
    from anomalynerd.analyze_pdf import analyze_source
    r = analyze_source(os.path.join(FIX, name + ".pdf"))
    flags = sorted((f.type, f.priority) for L in r.table_flags.values() for f in L)
    assert flags == sorted(PINS[name][0])


@pytest.mark.parametrize("name", ["pythia_2304.01373_p21"])
def test_clean_table_slice_does_not_trigger_the_layout_model(name):
    from anomalynerd.analyze_pdf import analyze_source
    r = analyze_source(os.path.join(FIX, name + ".pdf"))
    assert sorted(getattr(t, "backend", None) for t in r.parse.tables) == PINS[name][1]
