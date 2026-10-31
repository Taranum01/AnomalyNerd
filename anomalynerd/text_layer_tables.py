"""Recover a numeric table from the PDF text layer, row by row, with an alignment proof.

Used only for a page whose tables both extractors left unusable (fused cells). Words are
grouped into lines; a data line is a label followed by nothing but numbers. A table is
returned ONLY when every data line in its block carries exactly the same count of numbers
and that count equals the number of header labels. Any mismatch refuses the whole block, so
no value is ever shifted into a neighbouring column or split from a joined cell.
"""
import re

_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_SIZE = re.compile(r"^\d+(\.\d+)?[MBK]$")
_BLOCK_START = re.compile(r"^(Table|Figure|Fig\.)\s*\d+", re.I)


def _lines(words, tol=2.5):
    out = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1][0] - w["top"]) <= tol:
            out[-1][1].append(w)
        else:
            out.append([w["top"], [w]])
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in out]


def _split_data(line):
    toks = [w["text"] for w in line]
    i = len(toks)
    while i > 0 and _NUM.match(toks[i - 1]):
        i -= 1
    return toks[:i], toks[i:]


def r_lines(run, blk):
    """The word lists of a run's data lines, last-k words only (the numbers)."""
    k = len(run[0][2])
    return [(i, blk[i][-k:]) for i, _, _ in run]


def _aligned(header_words, line_words):
    """True when each header word sits over its column: its centre is within 45% of the column
    pitch of the median centre of that column's numbers. A caption or section line with the
    same word count fails this."""
    k = len(header_words)
    cols = []
    for c in range(k):
        xs = sorted((ws[c]["x0"] + ws[c]["x1"]) / 2 for ws in line_words)
        cols.append(xs[len(xs) // 2])
    pitch = min((b - a for a, b in zip(cols, cols[1:])), default=0)
    if pitch <= 0:
        return False
    return all(abs((h["x0"] + h["x1"]) / 2 - cols[c]) <= 0.45 * pitch
               for c, h in enumerate(header_words))


def _names(hw, blk, hdr_i, k):
    names = [w["text"] for w in hw][-k:]
    if len(set(names)) == len(names):
        return names
    period = next((i for i in range(1, k) if names[i] == names[0]), k)
    groups = [list(range(g, min(g + period, k))) for g in range(0, k, period)]
    colw = hw[-k:]
    centres = [sum((colw[c]["x0"] + colw[c]["x1"]) / 2 for c in grp) / len(grp) for grp in groups]
    labels = [[] for _ in groups]
    for ln in blk[max(0, hdr_i - 4):hdr_i]:
        if len(ln) < 2:
            continue
        for w in ln:
            if _NUM.match(w["text"]):
                continue
            cx = (w["x0"] + w["x1"]) / 2
            g = min(range(len(groups)), key=lambda g: abs(centres[g] - cx))
            labels[g].append(w["text"])
    full = []
    for g, grp in enumerate(groups):
        prefix = " ".join(labels[g]) or f"group{g + 1}"
        full += [f"{prefix} {names[c]}" for c in grp]
    return full


def recover(words):
    """Return (tables, notes). Each table is [header] + data rows (lists of str).

    A run is a stretch of data lines with the same count k of numbers. It becomes a table only
    with >= 4 rows and a header line of k labels above it. A shorter run touching an accepted run is a row that lost or gained a value: the page is
    refused, nothing is guessed. Other short runs are reported in notes, never merged."""
    lines = _lines(words)
    blocks, cur = [], []
    for ln in lines:
        if _BLOCK_START.match(" ".join(w["text"] for w in ln)):
            blocks.append(cur); cur = []
        else:
            cur.append(ln)
    blocks.append(cur)
    tables, notes = [], []
    for blk in blocks:
        data = []
        for i, ln in enumerate(blk):
            lab, nums = _split_data(ln)
            if len(nums) >= 2 and lab:
                data.append((i, lab, nums))
        runs = []
        for d in data:
            if runs and len(runs[-1][0][2]) == len(d[2]):
                runs[-1].append(d)
            else:
                runs.append([d])
        good = [r for r in runs if len(r) >= 4]
        for ri, r in enumerate(runs):
            if len(r) >= 4:
                continue
            near = [runs[x] for x in (ri - 1, ri + 1) if 0 <= x < len(runs)]
            if any(len(n) >= 4 for n in near):
                return [], ["a line next to a table carries a different number of values "
                            "than the table's rows; refused"]
            notes.append(f"{len(r)} line(s) with {len(r[0][2])} numbers not tabulated")
        for r in good:
            k = len(r[0][2])
            first = r[0][0]
            hdr_i = None
            for j in range(first - 1, -1, -1):
                toks = [w["text"] for w in blk[j]]
                if any(_NUM.match(t) for t in toks):
                    break
                if len(toks) in (k, k + 1) and _aligned(blk[j][-k:], [d[1] for d in r_lines(r, blk)]):
                    hdr_i = j; break
            if hdr_i is None:
                notes.append("a numeric block has no header line with one label per column; refused")
                continue
            header = ["Model"] + _names(blk[hdr_i], blk, hdr_i, k)
            rows, seen, last_size = [], set(), ""
            for i, lab, nums in r:
                if _SIZE.match(lab[0]):
                    last_size = lab[0]
                label = " ".join(lab)
                if label in seen:
                    sec = ""
                    for j in range(i - 1, hdr_i, -1):
                        if not any(_NUM.match(w["text"]) for w in blk[j]):
                            sec = " ".join(w["text"] for w in blk[j])[:12]; break
                    label = f"{label} [{last_size or sec}]"
                seen.add(label)
                rows.append([label] + nums)
            if len(seen) != len(rows):
                notes.append("row labels are not unique; refused")
                continue
            tables.append([header] + rows)
    return tables, notes
