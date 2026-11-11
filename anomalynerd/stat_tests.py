"""Statistical and digit tests for AnomalyNerd PDF analysis.

Three checks that complement the scientific-pattern detectors, each borrowed from the
research-integrity literature:

    statcheck  - recompute a reported p-value from its t, F, r, chi-square, or z
                 statistic and flag inconsistencies (Nuijten & Epskamp 2016).
    GRIM       - check whether a reported mean is arithmetically achievable for an
                 integer-item measure and the stated sample size (Brown & Heathers 2017).
    Benford /  - check the leading / terminal digit distribution of a numeric column
    terminal     for gross departures from what is expected.

Honesty requirement: each test returns a result object that says either what it
checked and found, OR that it was NOT APPLICABLE and WHY (e.g. no p-values reported, no
mean+N triples, too few values for a digit test). Nothing is silently skipped.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import math, re
from collections import Counter

from scipy import stats as _st

from .model import MISSING
from .ingest import _parse_number


@dataclass
class StatFinding:
    """One result from a statistical check: its kind, status, readable detail, severity and supporting numbers."""
    kind: str                 # "statcheck" | "grim" | "benford" | "terminal_digit"
    status: str               # "finding" | "ok" | "not_applicable"
    detail: str               # human-readable result or reason
    severity: str = "info"    # "high" | "medium" | "low" | "info"
    evidence: dict = field(default_factory=dict)


# ----------------------------------------------------------------- statcheck
from .stat_patterns import _T, _F, _R, _CHI2, _Z


def _reported_p(num_str: str) -> float:
    """Convert a reported p-value string to a float. A leading "." is read as "0."."""
    s = num_str.strip()
    if s.startswith("."):
        s = "0" + s
    return float(s)


def statcheck(stat_strings: list) -> list:
    """Recompute each reported p from its statistic. Flag when the recomputed p falls on
    the other side of .05 from the reported p (the consequential kind of inconsistency),
    or differs materially for an exact '=' report."""
    findings = []
    checked = 0
    for s in stat_strings:
        m = _T.search(s)
        if m:
            df = float(m.group(1)); tval = abs(float(m.group(2)))
            op = m.group(3); p_rep = _reported_p(m.group(4))
            p_calc = 2 * _st.t.sf(tval, df) if math.isfinite(tval) and math.isfinite(df) else math.nan
            checked += 1
            findings.append(_compare_p("t", s, p_rep, op, p_calc))
            continue
        m = _F.search(s)
        if m:
            d1 = float(m.group(1)); d2 = float(m.group(2)); fval = float(m.group(3))
            op = m.group(4); p_rep = _reported_p(m.group(5))
            p_calc = (_st.f.sf(fval, d1, d2)
                      if all(math.isfinite(x) for x in (fval, d1, d2)) and fval >= 0
                      else math.nan)
            checked += 1
            findings.append(_compare_p("F", s, p_rep, op, p_calc))
            continue
        m = _R.search(s)
        if m:
            dfr = float(m.group(1)); rval = abs(float(m.group(2)))
            op = m.group(3); p_rep = _reported_p(m.group(4))
            p_calc = math.nan
            if math.isfinite(dfr) and dfr > 0 and math.isfinite(rval) and rval <= 1:
                p_calc = 0.0 if rval == 1 else 2 * _st.t.sf(
                    rval * math.sqrt(dfr / (1 - rval**2)), dfr)
            checked += 1
            findings.append(_compare_p("r", s, p_rep, op, p_calc))
            continue
        m = _CHI2.search(s)
        if m:
            df = float(m.group(1)); chi2_value = float(m.group(2))
            op = m.group(3); p_rep = _reported_p(m.group(4))
            p_calc = (_st.chi2.sf(chi2_value, df)
                      if math.isfinite(chi2_value) and math.isfinite(df) and chi2_value >= 0
                      else math.nan)
            checked += 1
            findings.append(_compare_p("chi-square", s, p_rep, op, p_calc))
            continue
        m = _Z.search(s)
        if m:
            z_value = float(m.group(1))
            op = m.group(2); p_rep = _reported_p(m.group(3))
            p_calc = 2 * _st.norm.sf(abs(z_value)) if math.isfinite(z_value) else math.nan
            checked += 1
            findings.append(_compare_p("z", s, p_rep, op, p_calc))
    if checked == 0:
        findings.append(StatFinding(
            "statcheck", "not_applicable",
            "no reported test statistics with p-values (t(df)=.., F(df,df)=.., r(df)=.., "
            "chi-square(df)=.., z=..) were found in the body text, so no p-value could be "
            "rechecked"))
    return findings


def _compare_p(kind, s, p_rep, op, p_calc) -> StatFinding:
    """Compare a reported p-value with the recomputed one and return a StatFinding."""
    if not all(math.isfinite(p) and 0 <= p <= 1 for p in (p_rep, p_calc)):
        return StatFinding(
            "statcheck", "not_applicable",
            f"cannot recheck {kind}-test: invalid statistic, degrees of freedom, or p-value",
            evidence={"reported": s, "operator": op})
    ev = {"reported": s, "p_reported": round(p_rep, 4), "p_recomputed": round(p_calc, 4),
          "operator": op}
    rep_sig = (p_rep < 0.05) if op == "=" else (p_rep <= 0.05 if op == "<" else False)
    calc_sig = p_calc < 0.05

    if op == "<":
        # claim: true p is below p_rep. Violated if recomputed p is clearly above it.
        if p_calc > p_rep + 0.01:
            sev = "high" if (rep_sig and not calc_sig) else "medium"
            return StatFinding("statcheck", "finding",
                f"reported {kind}-test p<{p_rep:g} but recomputed p={p_calc:.3f}, which is "
                f"not below {p_rep:g} — worth checking", severity=sev, evidence=ev)
        return StatFinding("statcheck", "ok",
            f"{kind}-test p<{p_rep:g} is consistent with recomputed p={p_calc:.3f}", evidence=ev)

    if op == ">":
        if p_calc < p_rep - 0.01:
            return StatFinding("statcheck", "finding",
                f"reported {kind}-test p>{p_rep:g} but recomputed p={p_calc:.3f}, which is "
                f"not above {p_rep:g} — worth checking", severity="medium", evidence=ev)
        return StatFinding("statcheck", "ok",
            f"{kind}-test p>{p_rep:g} is consistent with recomputed p={p_calc:.3f}", evidence=ev)

    # op == "="
    if abs(p_calc - p_rep) > 0.01 and (rep_sig != calc_sig):
        return StatFinding("statcheck", "finding",
            f"reported {kind}-test p={p_rep:g} but recomputed p={p_calc:.3f}; this changes "
            f"significance at .05 — worth checking", severity="high", evidence=ev)
    if abs(p_calc - p_rep) > 0.02:
        return StatFinding("statcheck", "finding",
            f"reported {kind}-test p={p_rep:g} but recomputed p={p_calc:.3f}; they differ — "
            f"worth checking", severity="medium", evidence=ev)
    return StatFinding("statcheck", "ok",
        f"{kind}-test p={p_rep:g} is consistent with recomputed p={p_calc:.3f}", evidence=ev)


def _numeric_range_unchecked(kind, reported):
    """Preserve the report when a supported statistic exceeds float arithmetic."""
    return StatFinding(kind, "not_applicable",
        "reported values exceed the supported numeric range for this arithmetic; "
        "not checked (this is not a scientific anomaly finding)",
        evidence={"reported": reported})


# ----------------------------------------------------------------- GRIM
_MSDN = re.compile(r"\bM\s*=\s*(-?\d+\.?\d*)\s*,?\s*SD\s*=\s*(\d+\.?\d*)\s*,?\s*N\s*=\s*(\d+)", re.I)


def grim(stat_strings: list) -> list:
    """GRIM: for a mean of integer-valued items over N observations, M*N must round to an
    integer. If no M=..,N=.. is reported, say so."""
    findings = []
    checked = 0
    for s in stat_strings:
        m = _MSDN.search(s)
        if not m:
            continue
        mean = float(m.group(1)); n = int(m.group(3))
        decimals = len((m.group(1).split(".")[1]) if "." in m.group(1) else "")
        if n <= 0 or n > 1000 or decimals == 0:
            continue
        checked += 1
        # the nearest achievable mean at this N and decimal precision
        try:
            nearest = round(mean * n) / n
        except (OverflowError, ValueError):
            findings.append(_numeric_range_unchecked("grim", s))
            continue
        # epsilon as in grimmer: at the exact half-unit boundary (e.g. 0.75
        # reported as 0.8 with N=4) float error must not flip ok to finding
        if abs(nearest - mean) > 0.5 * 10**(-decimals) + 1e-9:
            findings.append(StatFinding("grim", "finding",
                f"reported mean {mean} with N={n} is not achievable for an integer-item "
                f"measure (nearest achievable is {nearest:.{decimals}f}) — worth checking",
                severity="medium", evidence={"reported": s, "mean": mean, "n": n,
                                             "nearest_achievable": round(nearest, decimals)}))
        else:
            findings.append(StatFinding("grim", "ok",
                f"mean {mean} is achievable for N={n}", evidence={"reported": s}))
    if checked == 0:
        findings.append(StatFinding("grim", "not_applicable",
            "no 'M=.., SD=.., N=..' triples with a decimal mean were found in the body text, "
            "and GRIM only applies to integer-item measures, so no mean plausibility could "
            "be checked"))
    return findings


# ----------------------------------------------------------------- GRIMMER
def grimmer(stat_strings: list) -> list:
    """GRIMMER (the SD companion to GRIM): for integer-item data with mean M, SD, and N, the
    sum of squared deviations N_minus_1 * SD^2 must correspond to an integer set of items that
    also produces the reported mean. We apply the standard necessary condition: the implied
    sum of squared values must be (near) an integer achievable by integer items with the fixed
    grand total round(M*N). Only runs when M, SD, N and a decimal SD are present, and only when
    the mean already passes GRIM (otherwise GRIM reports it). Says not-applicable otherwise."""
    findings = []
    checked = 0
    for s in stat_strings:
        m = _MSDN.search(s)
        if not m:
            continue
        mean = float(m.group(1)); sd = float(m.group(2)); n = int(m.group(3))
        sd_dec = len((m.group(2).split(".")[1]) if "." in m.group(2) else "")
        if n < 2 or n > 1000 or sd_dec == 0 or sd < 0:
            continue
        try:
            mean_dec = len((m.group(1).split(".")[1]) if "." in m.group(1) else "")
            if mean_dec and abs(round(mean * n) / n - mean) > 0.5 * 10 ** (-mean_dec) + 1e-9:
                continue  # GRIM already flags this; don't double-report
            T = round(mean * n)                 # integer grand total of the items
            mean_exact = T / n
            ss_dev = (sd ** 2) * (n - 1)        # sum of squared deviations implied by SD
            base_sumsq = round(ss_dev + n * mean_exact ** 2)   # nearest integer sum of x_i^2
            nearest_ss = base_sumsq - n * mean_exact ** 2
            nearest_sd = math.sqrt(max(0.0, nearest_ss / (n - 1)))
            step = 0.5 * 10 ** (-sd_dec)
        except (OverflowError, ValueError):
            checked += 1
            findings.append(_numeric_range_unchecked("grimmer", s))
            continue
        checked += 1
        if abs(nearest_sd - sd) > step + 1e-9:
            findings.append(StatFinding("grimmer", "finding",
                f"reported SD={sd} with mean={mean}, N={n} is not achievable for integer-item "
                f"data (nearest achievable SD is {nearest_sd:.{sd_dec}f}) — worth checking",
                severity="medium",
                evidence={"reported": s, "sd": sd, "n": n,
                          "nearest_achievable_sd": round(nearest_sd, sd_dec)}))
        else:
            findings.append(StatFinding("grimmer", "ok",
                f"SD={sd} is achievable for mean={mean}, N={n}", evidence={"reported": s}))
    if checked == 0:
        findings.append(StatFinding("grimmer", "not_applicable",
            "no integer-item 'M=.., SD=.., N=..' triples with a decimal SD were found (or the "
            "mean already fails GRIM), so no SD plausibility could be checked"))
    return findings


# ----------------------------------------------------------------- Benford / terminal digit
def _numeric_column_values(tidy):
    """All finite metric values from a tidy table."""
    out = []
    for r in tidy.rows:
        v = r.get("value")
        if v is not MISSING and isinstance(v, (int, float)) and math.isfinite(v):
            out.append(float(v))
    return out


_BENFORD = {d: math.log10(1 + 1/d) for d in range(1, 10)}


def digit_tests(tidy, min_values: int = 30) -> list:
    """Leading-digit (Benford) and terminal-digit uniformity checks on one table's metric
    values. Benford expects log-spaced leading digits for data spanning orders of magnitude;
    the terminal digit of 'enough' measured values should be roughly uniform. Both need many
    values; if too few, say so rather than report a meaningless statistic."""
    vals = _numeric_column_values(tidy)
    nz = [abs(v) for v in vals if v != 0]
    findings = []
    if len(nz) < min_values:
        findings.append(StatFinding("benford", "not_applicable",
            f"only {len(nz)} nonzero numeric values in this table; a digit-distribution test "
            f"needs at least {min_values} to be meaningful"))
        findings.append(StatFinding("terminal_digit", "not_applicable",
            f"only {len(nz)} numeric values in this table; a terminal-digit test needs at "
            f"least {min_values} to be meaningful"))
        return findings
    # Benford is not appropriate for narrow/bounded scales such as accuracy.
    # Also require adequate expected counts for every chi-square cell.
    if max(nz) / min(nz) < 100 or len(nz) * _BENFORD[9] < 5:
        findings.append(StatFinding("benford", "not_applicable",
            "values do not span two orders of magnitude or expected digit counts are below five"))
    else:
        # leading digit
        lead = []
        for v in nz:
            d = int(str(f"{v:.10e}")[0])   # first significant digit
            if 1 <= d <= 9:
                lead.append(d)
        obs = Counter(lead); n = len(lead)
        exp = {d: _BENFORD[d] * n for d in range(1, 10)}
        chi2 = sum((obs.get(d, 0) - exp[d])**2 / exp[d] for d in range(1, 10))
        p = _st.chi2.sf(chi2, df=8)
        if p < 0.01:
            findings.append(StatFinding("benford", "finding",
                f"leading-digit distribution departs from Benford's law (chi2={chi2:.1f}, "
                f"p={p:.3f}); may be natural for bounded data, but worth a look",
                severity="low", evidence={"n": n, "chi2": round(chi2, 2), "p": round(p, 4)}))
        else:
            findings.append(StatFinding("benford", "ok",
                f"leading-digit distribution is consistent with Benford's law (p={p:.2f})",
                evidence={"n": n}))

    # terminal digit: the LAST reported digit of measured values should be roughly uniform
    # over 0-9. A strong departure can signal rounding habits or fabrication. We use only
    # values that carry decimals (so the last digit is genuinely a measured digit).
    with_dec = []
    for r in tidy.rows:
        v = r.get("value")
        raw = r.get("_raw")
        if isinstance(v, (int, float)) and math.isfinite(v) and isinstance(raw, str):
            # Preserve reported precision, including terminal zero. Never infer
            # the reported digit from a binary float's repr or exponent.
            m = re.fullmatch(r"[+-]?\d*\.(\d+)", raw.strip())
            if m:
                with_dec.append(int(m.group(1)[-1]))
    if len(with_dec) >= max(min_values, 50):
        obs2 = Counter(with_dec); n2 = len(with_dec)
        exp2 = n2 / 10.0
        chi2b = sum((obs2.get(d, 0) - exp2) ** 2 / exp2 for d in range(10))
        p2 = _st.chi2.sf(chi2b, df=9)
        if p2 < 0.01:
            findings.append(StatFinding("terminal_digit", "finding",
                f"terminal (last) digit distribution is non-uniform (chi2={chi2b:.1f}, "
                f"p={p2:.3f}); can reflect rounding habits, but worth a look",
                severity="low", evidence={"n": n2, "chi2": round(chi2b, 2), "p": round(p2, 4)}))
        else:
            findings.append(StatFinding("terminal_digit", "ok",
                f"terminal-digit distribution is roughly uniform (p={p2:.2f})",
                evidence={"n": n2}))
    else:
        findings.append(StatFinding("terminal_digit", "not_applicable",
            f"only {len(with_dec)} values preserve a reported decimal digit; a terminal-digit "
            f"test needs at least {max(min_values, 50)} to be meaningful"))
    return findings
