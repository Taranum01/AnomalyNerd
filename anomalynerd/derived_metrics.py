"""Explicit-schema percentage-change check, no inference or automatic edits."""
from decimal import Decimal, DecimalException, localcontext

def check(*, before, after, printed_percent, decimals, base=None,
          formula_source=None, cells=None, inputs_exact=False):
    evidence={'source_cells':cells,'formula_source':formula_source,'base':base,
              'printed_percent':str(printed_percent),'rounding_decimals':decimals}
    def out(status,reason,**kw):return dict(status=status,reason=reason,evidence=evidence,**kw)
    if (not isinstance(formula_source, str) or not formula_source.strip()
            or not isinstance(cells, (list, tuple)) or len(cells) != 3
            or not all(isinstance(c, str) and c.strip() for c in cells)
            or base not in {'before', 'after'}):
        return out('ABSTAIN','formula_base_or_provenance_unresolved')
    if inputs_exact is not True:return out('ABSTAIN','input_rounding_unknown')
    if type(decimals) is not int or not 0<=decimals<=8:
        return out('ABSTAIN','invalid_precision')
    try:
        values=(before,after,printed_percent)
        if any(isinstance(v, bool) for v in values):
            return out('ABSTAIN','invalid_numeric_input')
        b,a,p=map(lambda x:Decimal(str(x)),values)
        if not all(x.is_finite() for x in (b,a,p)):
            return out('ABSTAIN','invalid_numeric_input')
        if any(abs(x.adjusted()) > 100 or len(x.as_tuple().digits) > 100 for x in (b,a,p)):
            return out('ABSTAIN','numeric_range_exceeded')
        d=b if base=='before' else a
        if d<=0:return out('ABSTAIN','nonpositive_base')
        with localcontext() as ctx:
            ctx.prec=250
            expected=(a-b)/d*100
            tolerance=Decimal(10)**(-decimals)/2
            if p != p.quantize(Decimal(10)**(-decimals)):
                return out('ABSTAIN','printed_precision_mismatch')
            # Inclusive boundary avoids assuming a rounding tie convention.
            consistent=abs(expected-p)<=tolerance
        return out('CONSISTENT' if consistent else 'REVIEW_WARNING',
                   'within_printed_rounding' if consistent else 'declared_formula_mismatch',
                   expected_percent=str(expected),rounding_tolerance=str(tolerance),
                   warning_only=True)
    except (DecimalException,ValueError,TypeError):return out('ABSTAIN','invalid_numeric_input')
