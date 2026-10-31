"""End to end through parse_pdf: a page whose pdfplumber table has fused cells is rebuilt from the
text layer with the real header and every data row (the first one included)."""
import pytest
from anomalynerd import pdf_ingest

COLW = 50


def _line(y, label, cells, x0=10):
    ws = [{"text": t, "x0": x0, "x1": x0 + 8 * len(t), "top": y} for t in label.split()]
    for j, c in enumerate(cells):
        if not c:
            continue
        x = 150 + j * COLW
        ws.append({"text": c, "x0": x, "x1": x + 8 * len(c), "top": y})
    return ws


def _page_words(header_lines, rows):
    words, y = [], 10
    for lab, cells in header_lines:
        words += _line(y, lab, cells); y += 12
    for lab, cells in rows:
        words += _line(y, lab, cells); y += 12
    return words


FUSED = [["Model", "en", "de", "avg"], ["mBERT", "94.0 85.7", "89.9", "70.1"], ["XLM", "91.0 82.0", "86.5", "71.2"],
         ["XLM-R", "95.0 88.0", "91.5", "72.3"], ["mT5-Base", "93.0 87.0", "90.0", "73.4"],
         ["a", "1.0 2.0", "3.0", "4.0"], ["b", "1.5 2.5", "3.5", "4.5"], ["c", "5.0 6.0", "7.0", "8.0"]]


class _Page:
    def __init__(self, words):
        self.words = words

    def extract_text(self):
        return "text"

    def extract_tables(self, settings=None):
        return [] if settings else [FUSED]

    def extract_words(self, **kw):
        return self.words


class _Pdf:
    def __init__(self, words):
        self.pages = [_Page(words)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _parse(monkeypatch, words):
    monkeypatch.setattr(pdf_ingest.pdfplumber, "open", lambda p: _Pdf(words))
    monkeypatch.setattr(pdf_ingest, "_try_docling", lambda p: ([], None))
    return pdf_ingest.parse_pdf("x.pdf", fused_recovery=True)


def _text_layer(parse):
    return [t for t in parse.tables if t.backend == "text-layer"]


SIMPLE_HDR = [("Model", ["en", "de", "avg"])]
SIMPLE_ROWS = [("mBERT", ["94.0", "85.7", "89.9"]), ("XLM", ["91.0", "82.0", "86.5"]),
               ("XLM-R", ["95.0", "88.0", "91.5"]), ("mT5-Base", ["93.0", "87.0", "90.0"])]
GRP_HDR = [("", ["Law", "", "Bio", ""]), ("Model", ["Direct", "CoT", "Direct", "CoT"])]
GRP_ROWS = [("davinci", ["37.7", "43.5", "50.0", "50.0"]), ("babbage", ["30.0", "31.0", "40.0", "41.0"]),
            ("curie", ["33.0", "35.0", "44.0", "45.0"]), ("ada", ["20.0", "21.0", "22.0", "23.0"])]


@pytest.fixture
def simple(monkeypatch):
    return _text_layer(_parse(monkeypatch, _page_words(SIMPLE_HDR, SIMPLE_ROWS)))


def test_a_table_is_recovered(simple):
    assert len(simple) == 1


def test_header_names_are_the_printed_ones(simple):
    assert simple[0].header == ["Model", "en", "de", "avg"]


def test_first_data_row_is_kept_as_data(simple):
    assert simple[0].rows[0] == ["mBERT", "94.0", "85.7", "89.9"]


def test_first_data_row_is_not_the_header(simple):
    assert simple[0].header[0] == "Model" and "mBERT" not in simple[0].header


def test_all_data_rows_are_kept(simple):
    assert [r[0] for r in simple[0].rows] == ["mBERT", "XLM", "XLM-R", "mT5-Base"]


def test_last_data_row_is_kept(simple):
    assert simple[0].rows[-1] == ["mT5-Base", "93.0", "87.0", "90.0"]


def test_no_fused_cell_is_left(simple):
    assert not any(pdf_ingest.is_fused(c) for r in simple[0].rows for c in r)


def test_first_row_is_in_the_analysed_values(simple):
    vals = {(r["Model"], r["method"]): r["value"] for r in simple[0].tidy.rows}
    assert vals[("mBERT", "en")] == 94.0 and len(vals) == 12


def test_direct_cot_pairs_keep_their_group_names(monkeypatch):
    t = _text_layer(_parse(monkeypatch, _page_words(GRP_HDR, GRP_ROWS)))
    assert len(t) == 1
    assert t[0].header == ["Model", "Law Direct", "Law CoT", "Bio Direct", "Bio CoT"]
    assert t[0].rows[0] == ["davinci", "37.7", "43.5", "50.0", "50.0"]


def test_direct_cot_first_row_is_in_the_analysed_values(monkeypatch):
    t = _text_layer(_parse(monkeypatch, _page_words(GRP_HDR, GRP_ROWS)))[0]
    assert len(t.tidy.rows) == 16 and t.tidy.rows[0]["Model"] == "davinci"


def test_a_row_with_a_missing_value_is_refused_not_guessed(monkeypatch):
    rows = SIMPLE_ROWS[:2] + [("XLM-R", ["95.0", "88.0"])] + SIMPLE_ROWS[3:]
    parse = _parse(monkeypatch, _page_words(SIMPLE_HDR, rows))
    assert _text_layer(parse) == []
    assert any(pdf_ingest.is_fused(c) for t in parse.tables for r in t.rows for c in r)
