"""Caption comparison for AnomalyNerd (a secondary test of how well generated captions match real ones).

Goal: compare \ourapproach's generated caption against a paper's real caption, but ONLY on
the claims the table itself can verify (trends, direction, best method, specific values,
level shifts). Real captions also carry context the numbers cannot give --- dataset names,
"bold = best", experimental setup --- which we deliberately ignore, because the table cannot
confirm or deny them.

Design requirements:
  - OBJECTIVE and TRANSPARENT: the rule that decides which claims are "checkable" is explicit
    and shown (see CHECKABLE_CLAIM_SPEC below and the extractor functions). Nothing is a
    black box.
  - Human captions are often poor or terse, so agreement with the real caption is NOT the
    success metric; this is a descriptive comparison, not a score the tool is optimized against.

What we extract from each caption is a small set of normalized, checkable CLAIM TUPLES:
  ("direction", axis_or_metric, "increase"|"decrease")   -- a stated trend/monotone direction
  ("best", entity)                                        -- a named best/winner
  ("shift", )                                             -- a stated level shift / regime change
  ("outlier", )                                           -- a stated outlier / anomaly
  ("value", number)                                       -- a specific numeric value mentioned
We then report which checkable claims the two captions share, and which each states alone.
"""
from __future__ import annotations
import re

# The transparent specification of what counts as a "checkable" claim. This is shown to the
# user / reader so the filtering is auditable. Each entry is a claim
# TYPE plus the surface cues that signal it in free text.
CHECKABLE_CLAIM_SPEC = {
    "direction": {
        "increase": [r"increas", r"\brises?\b", r"\bgrows?\b", r"\bhigher\b", r"upward", r"\bgain"],
        "decrease": [r"decreas", r"\bfalls?\b", r"\bdrops?\b", r"\blower\b", r"downward", r"declin", r"reduc"],
    },
    "shift": [r"level shift", r"regime", r"shifts? to", r"\bjumps?\b", r"step change", r"\bbreak"],
    "outlier": [r"outlier", r"anomal", r"stands? out", r"\bspike", r"extreme"],
    "best": [r"\bbest\b", r"outperform", r"\bwins?\b", r"superior", r"\btop\b", r"highest", r"lowest"],
}


def _extract_checkable(text: str) -> set:
    """Deterministic, rule-based extraction of checkable claim tuples from a caption.
    Transparent by construction: it applies the regex cues in CHECKABLE_CLAIM_SPEC."""
    t = (text or "").lower()
    claims = set()
    # direction (trend)
    for d, cues in CHECKABLE_CLAIM_SPEC["direction"].items():
        if any(re.search(c, t) for c in cues):
            claims.add(("direction", d))
    # shift / outlier / best (presence)
    for kind in ("shift", "outlier", "best"):
        if any(re.search(c, t) for c in CHECKABLE_CLAIM_SPEC[kind]):
            claims.add((kind,))
    # specific numeric values. Strip obvious NON-data numbers that the table cannot be about:
    # a leading "Table N" / "Figure N" index, and 4-digit years (1900-2099). These are
    # metadata, not table values, so counting them would be noise.
    t2 = re.sub(r"\b(table|figure|fig|tab)\.?\s*\d+", " ", t)
    for num in re.findall(r"-?\d+\.?\d*", t2):
        try:
            v = float(num)
        except ValueError:
            continue
        if 1900 <= v <= 2099 and float(num).is_integer():   # a year -> metadata, skip
            continue
        claims.add(("value", round(v, 2)))
    return claims


def compare_captions(generated: str, real: str) -> dict:
    """Compare two captions on CHECKABLE claims only. Returns a transparent report:
    the claim sets, their overlap, and what each caption states alone. Numbers are compared
    rounded to 2 decimals so minor rounding does not count as disagreement."""
    g = _extract_checkable(generated)
    r = _extract_checkable(real)
    shared = g & r
    gen_only = g - r
    real_only = r - g
    # agreement = fraction of the REAL caption's checkable claims that the generated one also makes
    recall = len(shared) / len(r) if r else None
    precision = len(shared) / len(g) if g else None
    return {
        "generated_claims": sorted(map(str, g)),
        "real_claims": sorted(map(str, r)),
        "shared": sorted(map(str, shared)),
        "generated_only": sorted(map(str, gen_only)),
        "real_only": sorted(map(str, real_only)),
        "checkable_recall": recall,      # of what the real caption checkably says, how much we also said
        "checkable_precision": precision,
        "note": ("Comparison is on checkable claims only (trends, best, shift, outlier, "
                 "numbers); un-inferable context (dataset names, formatting conventions) is "
                 "ignored. Human captions vary in quality, so this is descriptive, not a score."),
    }


def format_comparison(gen: str, real: str) -> str:
    rep = compare_captions(gen, real)
    L = ["Caption comparison (checkable claims only)",
         "=" * 48,
         f"Generated: {gen}",
         f"Real:      {real}",
         "",
         f"Shared checkable claims : {rep['shared'] or '(none)'}",
         f"Only in generated       : {rep['generated_only'] or '(none)'}",
         f"Only in real caption    : {rep['real_only'] or '(none)'}"]
    if rep["checkable_recall"] is not None:
        L.append(f"Checkable-claim recall  : {rep['checkable_recall']:.0%} "
                 f"(of the real caption's checkable claims)")
    L.append("")
    L.append(rep["note"])
    return "\n".join(L)
