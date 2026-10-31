"""parse_pdf routing with a fake pdfplumber and a fake layout model (neither is installed/needed)."""
from anomalynerd import pdf_ingest
from anomalynerd.pdf_ingest import _assess_and_tidy, is_fused

CLEAN = [["Model", "a", "b"], ["x", "1.0", "2.0"], ["y", "3.0", "4.0"], ["z", "5.0", "6.0"]]
CLEAN2 = [["Name", "c", "d"], ["p", "7.0", "8.0"], ["q", "9.0", "1.5"], ["r", "2.5", "3.5"]]
# three numbers per cell cannot be repaired by the pair splitter
FUSED = [["Model", "a", "b", "c", "d"], ["x", "1.0 3.0 9.0", "2.0 4.0 8.0", "1.3", "2.7"], ["y", "1.1", "2.2", "3.1", "4.2"],
         ["z", "5.0", "6.0", "5.5", "6.5"], ["w", "7.0", "8.0", "7.5", "8.5"]]
LAYOUT = [["Model", "a", "b"], ["x", "1.5", "2.5"], ["y", "3.5", "4.5"], ["z", "5.5", "6.5"]]


class _Page:
    def __init__(self, tables):
        self._tables = tables

    def extract_text(self):
        return "text"

    def extract_tables(self, settings=None):
        return [] if settings else self._tables


class _Pdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(monkeypatch, page_tables, layout_rows, fused_recovery=True):
    calls = []
    monkeypatch.setattr(pdf_ingest.pdfplumber, "open", lambda p: _Pdf([_Page(page_tables)]))

    def fake_layout(path):
        calls.append(path)
        return [_assess_and_tidy(r, 1, i) for i, r in enumerate(layout_rows)], None
    monkeypatch.setattr(pdf_ingest, "_try_docling", fake_layout)
    return pdf_ingest.parse_pdf("x.pdf", fused_recovery=fused_recovery), calls


def _fused_left(parse):
    return [t for t in parse.tables if any(is_fused(c) for r in t.rows for c in r)]


def test_fused_pdfplumber_table_is_replaced_by_layout_table(monkeypatch):
    parse, calls = _run(monkeypatch, [FUSED], [LAYOUT])
    assert calls == ["x.pdf"]
    assert not _fused_left(parse)
    assert any(t.rows[0][1:] == ["1.5", "2.5"] for t in parse.tables)


def test_fused_table_is_kept_when_layout_model_returns_nothing(monkeypatch):
    parse, calls = _run(monkeypatch, [FUSED], [])
    assert calls == ["x.pdf"]
    assert _fused_left(parse)


def test_clean_tables_do_not_call_the_layout_model(monkeypatch):
    parse, calls = _run(monkeypatch, [CLEAN], [LAYOUT])
    assert calls == []
    assert len(parse.tables) == 1


def test_clean_table_beside_a_fused_one_is_kept_once(monkeypatch):
    parse, calls = _run(monkeypatch, [CLEAN2, FUSED], [CLEAN2, LAYOUT])
    assert calls == ["x.pdf"]
    heads = [t.header[0] for t in parse.tables]
    assert heads.count("Name") == 1
    assert not _fused_left(parse)


def test_default_is_off_and_never_calls_the_layout_model_for_a_fused_table(monkeypatch):
    monkeypatch.delenv("ANOMALYNERD_FUSED_RECOVERY", raising=False)
    parse, calls = _run(monkeypatch, [FUSED], [LAYOUT], fused_recovery=None)
    assert calls == []
    assert _fused_left(parse)


def test_explicit_false_beats_the_environment(monkeypatch):
    monkeypatch.setenv("ANOMALYNERD_FUSED_RECOVERY", "1")
    parse, calls = _run(monkeypatch, [FUSED], [LAYOUT], fused_recovery=False)
    assert calls == [] and _fused_left(parse)


def test_environment_switch_turns_it_on(monkeypatch):
    monkeypatch.setenv("ANOMALYNERD_FUSED_RECOVERY", "true")
    parse, calls = _run(monkeypatch, [FUSED], [LAYOUT], fused_recovery=None)
    assert calls == ["x.pdf"] and not _fused_left(parse)


def test_unrecognised_environment_value_stays_off(monkeypatch):
    monkeypatch.setenv("ANOMALYNERD_FUSED_RECOVERY", "0")
    assert pdf_ingest.fused_recovery_enabled(None) is False
    monkeypatch.setenv("ANOMALYNERD_FUSED_RECOVERY", "maybe")
    assert pdf_ingest.fused_recovery_enabled(None) is False


def test_default_off_keeps_the_original_layout_fallback_when_no_table_is_usable(monkeypatch):
    monkeypatch.delenv("ANOMALYNERD_FUSED_RECOVERY", raising=False)
    parse, calls = _run(monkeypatch, [], [LAYOUT], fused_recovery=None)
    assert calls == ["x.pdf"]
    assert any(t.rows[0][1:] == ["1.5", "2.5"] for t in parse.tables)


def test_default_off_adds_layout_tables_beside_pdfplumber_tables_as_before(monkeypatch):
    monkeypatch.delenv("ANOMALYNERD_FUSED_RECOVERY", raising=False)
    parse, calls = _run(monkeypatch, [], [CLEAN2, LAYOUT], fused_recovery=None)
    assert calls == ["x.pdf"] and len(parse.tables) == 2
