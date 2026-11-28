"""HTML input for AnomalyNerd.

Reads every <table> out of a local .html/.htm file (an exported results page, a blog post, an
arXiv HTML paper) and scans the text outside the tables for reported statistics, exactly like
the Word path. Like Word and Excel, an HTML table stores real cells, so extraction is exact:
colspan/rowspan spans are expanded to the grid a reader sees, and each table passes the same
quality gate (pdf_ingest._assess_and_tidy, structured=True). Tables that are too small or have
no numeric column are reported as not analyzed, never dropped silently.

Only the Python standard library is used (html.parser), so this adds no dependency. Nothing is
fetched: remote images, scripts, stylesheets and links in the file are ignored, and relative
references are never followed.
"""
from __future__ import annotations
import re
from html.parser import HTMLParser

from .pdf_ingest import PdfParse, _assess_and_tidy, _extract_stat_strings
from .office_ingest import _trim_grid, _finalize

_SKIP_TAGS = {"script", "style", "noscript", "template"}


class _TableHTMLParser(HTMLParser):
    """Collect <table> grids (spans expanded) and the prose text outside any table."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []          # list of finished grids (list[list[str]])
        self.text_parts = []      # prose outside tables
        self._skip_depth = 0      # inside script/style/...
        self._table_depth = 0     # >0 inside a table (nested tables are read flat)
        self._grid = None         # grid under construction
        self._spans = None        # pending rowspans: col -> (rows_left, text)
        self._row = None          # current row cells: list of (col, text) fills
        self._cell = None         # current cell text parts
        self._cell_span = (1, 1)  # colspan, rowspan of the current cell
        self._col = 0

    # -- helpers --------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "table":
            if self._table_depth == 0:
                self._grid = []
                self._spans = {}
            self._table_depth += 1
            return
        if self._table_depth == 0 or self._grid is None:
            return
        if tag == "tr":
            self._row = []
            self._col = 0
        elif tag in ("td", "th") and self._row is not None:
            a = dict(attrs)
            try:
                colspan = max(1, min(1000, int(a.get("colspan", "1"))))
                rowspan = max(1, min(1000, int(a.get("rowspan", "1"))))
            except ValueError:
                colspan = rowspan = 1
            self._cell = []
            self._cell_span = (colspan, rowspan)

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
            if self._table_depth == 0 and self._grid is not None:
                grid = _trim_grid([r for r in self._grid])
                if grid:
                    self.tables.append(grid)
                self._grid = None
                self._spans = None
            return
        if self._table_depth == 0 or self._grid is None:
            return
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            colspan, rowspan = self._cell_span
            # find the next free column, honouring spans carried down from above
            col = self._col
            while col in self._spans:
                col += 1
            for dc in range(colspan):
                if rowspan > 1:
                    # counted down at the END of each following row, so the origin row
                    # itself is not consumed
                    self._spans[col + dc] = (rowspan, text)
            while len(self._row) <= col + colspan - 1:
                self._row.append("")
            for dc in range(colspan):
                self._row[col + dc] = text
            self._col = col + colspan
            self._cell = None
        elif tag == "tr" and self._row is not None:
            # materialize the row: carried spans fill their columns, blanks elsewhere
            width = max([len(self._row)] + [c + 1 for c in self._spans] + [0])
            cells = [""] * width
            for i, v in enumerate(self._row):
                cells[i] = v
            done = []
            for c in sorted(self._spans):
                left, text = self._spans[c]
                if c < width and not cells[c]:
                    cells[c] = text
                left -= 1
                if left <= 0:
                    done.append(c)
                else:
                    self._spans[c] = (left, text)
            for c in done:
                del self._spans[c]
            self._grid.append(cells)
            self._row = None

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._table_depth == 0:
            self.text_parts.append(data)


def _read_text(path: str) -> str:
    """Decode the file: charset from a <meta> tag when declared, else UTF-8. Bad bytes are
    replaced, never fatal."""
    with open(path, "rb") as f:
        raw = f.read()
    head = raw[:4096].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?\s*([a-zA-Z0-9_\-]+)', head)
    encoding = m.group(1) if m else "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def parse_html(path: str) -> PdfParse:
    """Parse a local HTML file: every <table> becomes a candidate results table, and the prose
    between tables is scanned for reported statistics."""
    notes = []
    parser = _TableHTMLParser()
    try:
        parser.feed(_read_text(path))
        parser.close()
    except Exception as exc:   # html.parser is lenient; guard the unexpected honestly
        return PdfParse(path=path, n_pages=0, tables=[], stat_strings=[],
                        notes=[f"the HTML could not be parsed ({type(exc).__name__}); not analyzed"])
    raw_tables = [(ti, grid) for ti, grid in enumerate(parser.tables, start=1)]
    text = "\n".join(t for t in (p.strip() for p in parser.text_parts) if t)
    stats = _extract_stat_strings(text) if text else []
    return _finalize(path, raw_tables, stats, notes,
                     n_pages=len(raw_tables), text_pages=1 if text else 0)
