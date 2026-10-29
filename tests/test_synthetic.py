"""Synthetic tables with KNOWN anomalies — each detector must fire on the right one."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from anomalynerd.model import TidyTable, Axis
from anomalynerd.analyze import analyze

def rows_from_grid(p1s, methods, fn):
    rows = []
    for p1 in p1s:
        for m in methods:
            rows.append({"p1": p1, "method": m, "value": fn(p1, m)})
    return rows

def test_numeric_jump_and_win_reversal():
    # MethodA-like: method A perfect at low p1 then explodes; winner flips A->B->C
    def fn(p1, m):
        table = {
            (0.12,"A"):1.6,(0.15,"A"):196.0,(0.20,"A"):209.0,(0.25,"A"):214.0,
            (0.12,"B"):86.0,(0.15,"B"):20.0,(0.20,"B"):145.0,(0.25,"B"):136.0,
            (0.12,"C"):48.0,(0.15,"C"):61.0,(0.20,"C"):72.0,(0.25,"C"):84.0,
        }
        return table[(p1,m)]
    t = TidyTable("toy", {"p1":Axis("p1","numeric"),"method":Axis("method","unordered_cat")},
                  entity_axis="method", metric_name="RMSE", lower_is_better=True,
                  rows=rows_from_grid([0.12,0.15,0.20,0.25],["A","B","C"],fn))
    flags = analyze(t)
    types = {f.type for f in flags}
    assert "numeric_jump" in types, "should catch A's 1.6->196 jump"
    assert "win_reversal" in types, "winner A->B->C should be flagged"
    jump = [f for f in flags if f.type=="numeric_jump"][0]
    assert jump.detail["from"]==0.12 and jump.detail["to"]==0.15
    print("  jump suggestion:", jump.suggestion)
    print("  OK numeric_jump + win_reversal")

def test_duplicate_and_flatness():
    # same specific value in two places; and method Z flat across p1
    rows = [
        # X varies across p1 (12.34, 379.180, 379.180) -> the repeat at 0.2 & 0.3 is the copy/paste
        {"p1":0.1,"method":"X","value":12.34},{"p1":0.2,"method":"X","value":379.180},{"p1":0.3,"method":"X","value":379.180},
        # Z is flat across p1 -> flatness, NOT duplicate
        {"p1":0.1,"method":"Z","value":5.0},{"p1":0.2,"method":"Z","value":5.0},{"p1":0.3,"method":"Z","value":5.0},
    ]
    t = TidyTable("toy2", {"p1":Axis("p1","numeric"),"method":Axis("method","unordered_cat")},
                  entity_axis="method", metric_name="RMSE", lower_is_better=True, rows=rows)
    flags = analyze(t)
    types = {f.type for f in flags}
    assert "duplicate" in types, "379.180 twice should flag"
    assert "flatness" in types, "Z constant across p1 should flag"
    print("  OK duplicate + flatness")

def test_cat_exception():
    # ordered knowledge: 'both' should beat 'none' always, except one case
    order = ["both","none"]
    rows = []
    cases = {"c1":(10,20),"c2":(15,25),"c3":(12,22),"c4":(30,18)}  # c4 is the exception
    for c,(vb,vn) in cases.items():
        rows.append({"case":c,"know":"both","value":vb})
        rows.append({"case":c,"know":"none","value":vn})
    t = TidyTable("toy3", {"case":Axis("case","unordered_cat"),
                           "know":Axis("know","ordered_cat",order=order)},
                  entity_axis=None, metric_name="RMSE", lower_is_better=True, rows=rows)
    flags = analyze(t)
    assert any(f.type=="cat_exception" for f in flags), "the one c4 exception should flag"
    print("  OK cat_exception")

def test_no_win_reversal_when_one_entry_per_level():
    # each method has its own axis value (a score column read as an axis): no contest
    rows = [{"cola": c, "method": m, "value": v}
            for c, m, v in [(60.5, "A", 80.5), (67.8, "B", 88.1), (69.1, "C", 89.0),
                            (70.2, "D", 89.1), (71.7, "E", 89.4)]]
    t = TidyTable("one_per_level", {"cola": Axis("cola", "numeric"),
                                    "method": Axis("method", "unordered_cat")},
                  entity_axis="method", metric_name="score", lower_is_better=False, rows=rows)
    from anomalynerd.detectors import detect_win_reversals
    assert detect_win_reversals(t) == []

if __name__ == "__main__":
    test_numeric_jump_and_win_reversal()
    test_duplicate_and_flatness()
    test_cat_exception()
    print("ALL SYNTHETIC TESTS PASSED")
