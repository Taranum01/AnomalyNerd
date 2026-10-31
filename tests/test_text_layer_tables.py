from anomalynerd.text_layer_tables import recover


def _words(lines):
    out = []
    for i, ln in enumerate(lines):
        for j, t in enumerate(ln.split()):
            out.append({"text": t, "x0": 10 + j * 40, "x1": 40 + j * 40, "top": 10 + i * 12})
    return out


HDR = "Model en de avg"
ROWS = ["A 1.0 2.0 1.5", "B 3.0 4.0 3.5", "C 5.0 6.0 5.5", "D 7.0 8.0 7.5"]


def test_recovers_uniform_block():
    tabs, notes = recover(_words([HDR] + ROWS))
    assert len(tabs) == 1 and tabs[0][0] == ["Model", "en", "de", "avg"]
    assert tabs[0][3] == ["C", "5.0", "6.0", "5.5"] and notes == []


def test_row_with_a_missing_value_refuses_the_page():
    tabs, notes = recover(_words([HDR] + ROWS + ["E 9.0 9.5"]))
    assert tabs == [] and "different number of values" in notes[0]


def test_no_header_means_no_table():
    tabs, notes = recover(_words(ROWS))
    assert tabs == [] and "no header" in notes[0]


def test_duplicate_row_labels_are_kept_apart():
    rows = ["A 1.0 2.0 1.5", "B 3.0 4.0 3.5", "A 5.0 6.0 5.5", "D 7.0 8.0 7.5"]
    tabs, notes = recover(_words([HDR] + rows))
    assert len(tabs) == 1 and len({r[0] for r in tabs[0][1:]}) == 4


def test_numbers_inside_the_header_line_are_not_a_header():
    tabs, notes = recover(_words(["Model 2020 2021 2022"] + ROWS[:0] + ["A 1.0 2.0 1.5"] * 0))
    assert tabs == []
