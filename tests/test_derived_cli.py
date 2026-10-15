import contextlib,io,json,tempfile,unittest
from pathlib import Path
from anomalynerd.cli import main
from anomalynerd.derived_report import load_checks
class DerivedCLI(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.csv=self.root/'table.csv';self.csv.write_text('method,score\na,1\nb,2\nc,3\nd,4\ne,5\n')
  self.schema=self.root/'schema.json';self.schema.write_text(json.dumps([dict(before='100',after='112',printed_percent='9.0',decimals=1,base='before',inputs_exact=True,formula_source='Synthetic formula',cells=['A1','B1','C1']),dict(before='100',after='112',printed_percent='9.0',decimals=1)]))
 def tearDown(self):self.tmp.cleanup()
 def run_cli(self,*args):
  out,err=io.StringIO(),io.StringIO()
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=main([str(self.csv),'--metric','score',*args])
  return code,out.getvalue(),err.getvalue()
 def test_default_no_derived(self):self.assertNotIn('Derived arithmetic',self.run_cli()[1])
 def test_json_default_no_derived(self):self.assertNotIn('derived_checks',json.loads(self.run_cli('--json')[1]))
 def test_optin_summary(self):
  text=self.run_cli('--verbose-derived','--derived-schema',str(self.schema))[1]
  self.assertIn('1 review warning(s), 0 consistent, 1 abstained',text);self.assertNotIn('Synthetic formula',text)
 def test_detail_explicit(self):self.assertIn('Synthetic formula',self.run_cli('--verbose-derived','--derived-schema',str(self.schema),'--derived-details')[1])
 def test_json_separate(self):
  default=json.loads(self.run_cli('--json')[1]);verbose=json.loads(self.run_cli('--json','--verbose-derived','--derived-schema',str(self.schema))[1]);self.assertEqual(default['flags'],verbose['flags']);self.assertEqual(len(verbose['derived_checks']['results']),2)
 def test_requires_optin(self):
  with self.assertRaises(SystemExit):self.run_cli('--derived-schema',str(self.schema))
 def test_requires_schema(self):
  with self.assertRaises(SystemExit):self.run_cli('--verbose-derived')
 def test_invalid_no_partial_report(self):
  self.schema.write_text('{}');code,out,err=self.run_cli('--verbose-derived','--derived-schema',str(self.schema));self.assertEqual(code,2);self.assertEqual(out,'');self.assertIn('Error',err)
 def test_unknown_fields(self):
  self.schema.write_text('[{"evil":true}]')
  with self.assertRaises(ValueError):load_checks(self.schema)
 def test_limit(self):
  self.schema.write_text(json.dumps([{}]*101))
  with self.assertRaises(ValueError):load_checks(self.schema)
