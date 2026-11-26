"""Shared test helpers.

arxiv_page(name) gives a one-page PDF cut from a public arXiv paper. The pages are not stored
in the repo (several papers are under arXiv's non-exclusive licence), so the full paper is
downloaded once at a pinned version, the page is cut out with pypdfium2, and both are kept in
a local cache (ANOMALYNERD_TEST_PDFS, default ~/.cache/anomalynerd_test_pdfs). A test that
needs a page is skipped when it cannot be fetched, for example when offline.
"""
import importlib.util
import os
import urllib.request

import pytest


def require_docling():
    """Skip the calling test when the optional docling layout-model backend is absent.

    The end-to-end PDF-routing tests need docling's table model; without it the pipeline
    honestly reports it could not read those tables, so the pinned expectations cannot
    hold. Importing docling is heavy (torch), so probe for it without importing."""
    if importlib.util.find_spec("docling") is None:
        pytest.skip("docling is not installed (optional layout backend; pip install docling)")

# slice name -> (arXiv id with pinned version, 1-based page)
ARXIV_PAGES = {
    "roberta_1907.11692_p13": ("1907.11692v1", 13),
    "lora_2106.09685_p23": ("2106.09685v2", 23),
    "flan_2210.11416_p46": ("2210.11416v5", 46),
    "pythia_2304.01373_p21": ("2304.01373v2", 21),
    "xlmr_1911.02116_p12": ("1911.02116v2", 12),
}


def _cache_dir():
    d = os.environ.get("ANOMALYNERD_TEST_PDFS") or os.path.join(
        os.path.expanduser("~"), ".cache", "anomalynerd_test_pdfs")
    os.makedirs(d, exist_ok=True)
    return d


def fetch_arxiv_page(name):
    """Path to the one-page PDF `name` from ARXIV_PAGES; pytest.skip if it cannot be made."""
    arxiv_id, page = ARXIV_PAGES[name]
    d = _cache_dir()
    out = os.path.join(d, name + ".pdf")
    if os.path.exists(out):
        return out
    try:
        import pypdfium2 as pdfium
    except ImportError:
        pytest.skip("pypdfium2 is not installed (it comes with docling)")
    full = os.path.join(d, arxiv_id + ".pdf")
    try:
        if not os.path.exists(full):
            req = urllib.request.Request("https://arxiv.org/pdf/" + arxiv_id,
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            with open(full + ".tmp", "wb") as f:
                f.write(data)
            os.replace(full + ".tmp", full)
        new = pdfium.PdfDocument.new()
        new.import_pages(pdfium.PdfDocument(full), [page - 1])
        new.save(out + ".tmp")
        os.replace(out + ".tmp", out)
    except Exception as exc:
        pytest.skip(f"could not fetch arXiv:{arxiv_id} page {page} ({type(exc).__name__})")
    return out


@pytest.fixture
def arxiv_page():
    return fetch_arxiv_page
