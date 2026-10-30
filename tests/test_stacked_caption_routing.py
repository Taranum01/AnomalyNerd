"""A pdfplumber table that swallows stacked tables (caption rows inside the body) must also be
read by the layout model, so the real results table is analyzed on the ordinary path."""
import os
from types import SimpleNamespace

import pytest

from anomalynerd.pdf_ingest import has_stacked_caption

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _t(*rows):
    return SimpleNamespace(rows=[list(r) for r in rows])


# ---- predicate: positives and negatives -------------------------------------------------
def test_full_table_caption_cell_matches():
    assert has_stacked_caption(_t(["a", "b"], ["Table8: D", "e"]))


def test_caption_with_lost_leading_letters_matches():
    assert has_stacked_caption(_t(["le15:Hyperparame", "ter"]))


def test_spaced_caption_number_matches():
    assert has_stacked_caption(_t(["x"], ["Table 9.", "y"]))


def test_prose_mentioning_a_table_does_not_match():
    assert not has_stacked_caption(_t(["Table 8 shows the results", "x"]))


def test_plain_numeric_table_does_not_match():
    assert not has_stacked_caption(_t(["Model", "MNLI", "QNLI"], ["BERT", "86.6", "92.3"]))


def test_empty_and_none_rows_do_not_match():
    assert not has_stacked_caption(SimpleNamespace(rows=None))
    assert not has_stacked_caption(_t())


# ---- end to end on the single-page slices (ordinary analyze_source path) -----------------
@pytest.fixture(scope="module")
def roberta():
    from anomalynerd.analyze_pdf import analyze_source
    return analyze_source(os.path.join(FIX, "roberta_1907.11692_p13.pdf"))


@pytest.fixture(scope="module")
def lora():
    from anomalynerd.analyze_pdf import analyze_source
    return analyze_source(os.path.join(FIX, "lora_2106.09685_p23.pdf"))


def _flags(r):
    return [f for L in r.table_flags.values() for f in L if f.type == "point_outlier"]


def test_roberta_slice_gets_a_layout_model_table(roberta):
    assert any(getattr(t, "backend", None) == "docling" and t.n_cols == 9 for t in roberta.parse.tables)


def test_roberta_cola_flag_is_listed_low_not_high(roberta):
    cola = [f for f in _flags(roberta) if "63.6" in f.suggestion]
    assert len(cola) == 1 and cola[0].priority == "LOW"


def test_roberta_other_flags_keep_their_priority(roberta):
    others = [f for f in _flags(roberta) if "63.6" not in f.suggestion]
    assert others and all(f.priority == "HIGH" for f in others)


def test_lora_slice_gets_layout_model_tables(lora):
    assert any(getattr(t, "backend", None) == "docling" for t in lora.parse.tables)


def test_lora_prefixembed_flag_is_listed_low(lora):
    f = [x for x in _flags(lora) if "37.6" in x.suggestion]
    assert len(f) == 1 and f[0].priority == "LOW"


def test_lora_mnli_flag_stays_high(lora):
    f = [x for x in _flags(lora) if "84.9" in x.suggestion]
    assert len(f) == 1 and f[0].priority == "HIGH"
