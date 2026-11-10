"""Ambiguous CSV columns must not silently overwrite or discard values."""
import tempfile
import unittest
from pathlib import Path
from anomalynerd.ingest import read_csv

class CSVColumnsTests(unittest.TestCase):
    def parse(self,text,**kwargs):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'fixture.csv';p.write_text(text,encoding='utf-8')
            return read_csv(str(p),**kwargs)
    def reject(self,text):
        with self.assertRaisesRegex(ValueError,'(?i)(column|header|row)'):
            self.parse(text)
    def test_duplicate_metric_names_rejected(self):self.reject('method,score,score\nA,1,100\nB,2,200\nC,3,300\n')
    def test_duplicate_axis_names_rejected(self):self.reject('method,method,score\nA,X,1\nB,Y,2\nC,Z,3\n')
    def test_duplicate_empty_headers_rejected(self):self.reject(',,score\nA,X,1\nB,Y,2\nC,Z,3\n')
    def test_single_empty_header_remains_supported(self):
        t=self.parse('method,,score\nA,X,1\nB,Y,2\nC,Z,3\n',metric_col='score')
        self.assertEqual([r['value'] for r in t.rows],[1,2,3])
    def test_unique_whitespace_header_is_not_silently_normalized(self):
        t=self.parse('method, ,score\nA,X,1\nB,Y,2\nC,Z,3\n',metric_col='score')
        self.assertEqual([r['value'] for r in t.rows],[1,2,3])
    def test_extra_numeric_cells_rejected(self):self.reject('method,score\nA,1,100\nB,2,200\nC,3,300\n')
    def test_extra_text_cell_rejected(self):self.reject('method,score\nA,1,note\nB,2\nC,3\n')
    def test_extra_empty_cell_rejected(self):self.reject('method,score\nA,1,\nB,2\nC,3\n')
    def test_quoted_comma_is_one_cell(self):
        t=self.parse('method,score\n"A, variant",1\nB,2\nC,3\n',metric_col='score')
        self.assertEqual(t.rows[0]['method'],'A, variant')
    def test_leading_comments_and_bom_remain_supported(self):
        t=self.parse('\ufeff# description\n\nmethod,score\nA,1\nB,2\nC,3\n',metric_col='score')
        self.assertEqual([r['value'] for r in t.rows],[1,2,3])
    def test_short_rows_keep_missing_value_handling(self):
        t=self.parse('method,score\nA,1\nB\nC,3\n',metric_col='score')
        self.assertEqual(len(t.rows),3)
    def test_blank_rows_do_not_create_extra_cells(self):
        t=self.parse('method,score\nA,1\n\nB,2\nC,3\n',metric_col='score')
        self.assertEqual(len(t.rows),3)

    def test_multiple_table_entrypoint_rejects_duplicate_headers(self):
        from anomalynerd.ingest import read_tables
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'fixture.csv';p.write_text('method,score,score\nA,1,100\nB,2,200\nC,3,300\n')
            with self.assertRaisesRegex(ValueError,'(?i)header'):read_tables(str(p))
    def test_web_upload_rejects_ambiguous_header_without_findings(self):
        import importlib.util
        from fastapi.testclient import TestClient
        path=Path(__file__).resolve().parents[1]/'web'/'app.py'
        spec=importlib.util.spec_from_file_location('csv_integrity_web',path)
        app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)
        with TestClient(app.app) as client:
            response=client.post('/analyze',files={'file':('bad.csv',b'method,score,score\nA,1,100\nB,2,200\nC,3,300\n','text/csv')})
        self.assertEqual(response.status_code,500)
        self.assertNotIn('tables',response.json())
        self.assertIn('header',response.json()['detail'].lower())

if __name__=='__main__':unittest.main()
