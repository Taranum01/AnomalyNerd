import unittest
from anomalynerd.derived_metrics import check
class Test(unittest.TestCase):
 def call(self,**kw):
  args=dict(before=100,after=112,printed_percent=12,decimals=1,base='before',formula_source='Synthetic caption: percent=(after-before)/before*100',cells=['synthetic:A1','synthetic:B1','synthetic:C1'],inputs_exact=True);args.update(kw);return check(**args)
 def test_consistent(self):self.assertEqual(self.call()['status'],'CONSISTENT')
 def test_mismatch(self):self.assertEqual(self.call(printed_percent=9)['status'],'REVIEW_WARNING')
 def test_rounding(self):self.assertEqual(self.call(after=112.04)['status'],'CONSISTENT')
 def test_boundary_abstains_from_tie_rule(self):self.assertEqual(self.call(after=112.05)['status'],'CONSISTENT')
 def test_after_base(self):self.assertEqual(self.call(before=80,after=100,printed_percent=20,base='after')['status'],'CONSISTENT')
 def test_zero(self):self.assertEqual(self.call(before=0)['status'],'ABSTAIN')
 def test_negative_base(self):self.assertEqual(self.call(before=-1)['status'],'ABSTAIN')
 def test_ambiguous(self):self.assertEqual(self.call(base=None)['status'],'ABSTAIN')
 def test_no_source(self):self.assertEqual(self.call(formula_source=None)['status'],'ABSTAIN')
 def test_no_cells(self):self.assertEqual(self.call(cells=None)['status'],'ABSTAIN')
 def test_inexact(self):self.assertEqual(self.call(inputs_exact=False)['status'],'ABSTAIN')
 def test_nan(self):self.assertEqual(self.call(before='NaN')['status'],'ABSTAIN')
 def test_precision(self):self.assertEqual(self.call(decimals=True)['status'],'ABSTAIN')
 def test_decrease(self):self.assertEqual(self.call(after=88,printed_percent=-12)['status'],'CONSISTENT')
 def test_bool_input(self):self.assertEqual(self.call(before=True)['status'],'ABSTAIN')
 def test_extreme_input(self):self.assertEqual(self.call(before='1e999999')['status'],'ABSTAIN')
 def test_excess_digits(self):self.assertEqual(self.call(before='1'*101)['status'],'ABSTAIN')
 def test_precision_mismatch(self):self.assertEqual(self.call(printed_percent='12.04')['status'],'ABSTAIN')
 def test_bad_provenance(self):self.assertEqual(self.call(cells=['one'])['status'],'ABSTAIN')
 def test_mutable_precision(self):
  from decimal import getcontext
  old=getcontext().prec
  try:
   getcontext().prec=3
   self.assertEqual(self.call(after='112.049')['status'],'CONSISTENT')
  finally:getcontext().prec=old
 def test_string_exact_not_accepted(self):self.assertEqual(self.call(inputs_exact='false')['status'],'ABSTAIN')
if __name__=='__main__':unittest.main()
