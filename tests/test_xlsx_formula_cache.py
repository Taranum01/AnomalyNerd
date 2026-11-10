"""Synthetic XLSX through the actual parser and analyze_source. No recalculation."""
import importlib.util
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
import pytest
from openpyxl import Workbook
from anomalynerd.office_ingest import parse_xlsx
from anomalynerd.analyze_pdf import analyze_source, format_report

BASE = os.environ.get('ANOMALY_OFFICE_BASELINE')
if BASE:
    spec=importlib.util.spec_from_file_location('anomalynerd.office_ingest',BASE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import anomalynerd.office_ingest
    anomalynerd.office_ingest.parse_xlsx=module.parse_xlsx
    parse_xlsx=module.parse_xlsx

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def workbook(path, formula='=5+6', other=False):
    wb=Workbook();ws=wb.active;ws.title='Results'
    for row in [['Model','Score'],['A',10],['B',formula],['C',12]]:ws.append(row)
    if other:
        ws=wb.create_sheet('Control')
        for row in [['Model','Score'],['A',10],['B',11],['C',12]]:ws.append(row)
    wb.save(path);wb.close()
    return path

def cached(path,value,kind=None):
    with zipfile.ZipFile(path) as z:files={n:z.read(n) for n in z.namelist()}
    name='xl/worksheets/sheet1.xml';root=ET.fromstring(files[name])
    cell=root.find(".//s:c[@r='B3']",NS)
    if kind:cell.set('t',kind)
    v=cell.find('s:v',NS);v.text=str(value)
    files[name]=ET.tostring(root)
    with zipfile.ZipFile(path,'w') as z:
        for n,data in files.items():z.writestr(n,data)
    return path

def test_uncached_formula_is_not_analyzable(tmp_path):
    result=parse_xlsx(str(workbook(tmp_path/'x.xlsx')))
    assert result.tables[0].status=='skipped'
    assert result.tables[0].tidies==[]

def test_reason_names_sheet_and_coordinate(tmp_path):
    result=parse_xlsx(str(workbook(tmp_path/'x.xlsx')))
    assert any("'Results'" in n and 'B3' in n and 'recalculate' in n for n in result.notes)

def test_full_analysis_does_not_emit_false_missing_flags(tmp_path):
    result=analyze_source(str(workbook(tmp_path/'x.xlsx')))
    assert not result.table_flags.get((1,0))
    assert any('cached' in n and 'B3' in n for n in result.could_not_check)

def test_text_report_keeps_coverage_warning(tmp_path):
    result=analyze_source(str(workbook(tmp_path/'x.xlsx')))
    text=format_report(result)
    assert 'B3' in text and 'Formulas were not evaluated' in text

def test_cached_numeric_formula_remains_analyzable(tmp_path):
    result=parse_xlsx(str(cached(workbook(tmp_path/'x.xlsx'),11)))
    assert result.tables[0].status in ('ok','partial')
    assert result.tables[0].tidy.rows[1]['value']==11

def test_cached_zero_is_not_missing(tmp_path):
    result=parse_xlsx(str(cached(workbook(tmp_path/'x.xlsx'),0)))
    assert result.tables[0].status in ('ok','partial')
    assert result.tables[0].tidy.rows[1]['value']==0

def test_cached_error_is_reported_unknown(tmp_path):
    result=parse_xlsx(str(cached(workbook(tmp_path/'x.xlsx'), '#DIV/0!', 'e')))
    assert result.tables[0].status=='skipped'
    assert 'B3' in result.tables[0].reason

def test_other_sheet_is_still_checked(tmp_path):
    result=analyze_source(str(workbook(tmp_path/'x.xlsx',other=True)))
    assert [t.status for t in result.parse.tables]==['skipped','ok']
    assert not result.table_flags.get((1,0))
    assert any(k[0]==2 for k in result.detector_runs)

def test_actual_blank_is_not_formula_warning(tmp_path):
    result=parse_xlsx(str(workbook(tmp_path/'x.xlsx',formula=None)))
    assert result.tables[0].status in ('ok','partial')
    assert not any('cached' in n for n in result.notes)

def test_literal_equals_text_not_evaluated(tmp_path):
    path=workbook(tmp_path/'x.xlsx')
    from openpyxl import load_workbook
    w=load_workbook(path);w.active['B3']='=literal';w.active['B3'].data_type='s';w.save(path);w.close()
    result=parse_xlsx(str(path))
    assert not any('cached' in n for n in result.notes)

def test_formula_only_sheet_is_visible_skipped(tmp_path):
    path=tmp_path/'x.xlsx';w=Workbook();w.active['A1']='=1+1';w.save(path);w.close()
    result=parse_xlsx(str(path))
    assert len(result.tables)==1 and result.tables[0].status=='skipped'
    assert 'A1' in result.tables[0].reason

def test_warning_is_bounded_for_many_formulas(tmp_path):
    path=tmp_path/'x.xlsx';w=Workbook()
    for i in range(1,31):w.active.cell(i,1,'=1+1')
    w.save(path);w.close();result=parse_xlsx(str(path))
    reason=result.tables[0].reason
    assert '30 formula' in reason and '20 more' in reason and len(reason)<400

def test_header_formula_is_also_unknown(tmp_path):
    path=workbook(tmp_path/'x.xlsx',formula=11)
    from openpyxl import load_workbook
    w=load_workbook(path);w.active['B1']='="Score"';w.save(path);w.close()
    result=parse_xlsx(str(path))
    assert result.tables[0].status=='skipped' and 'B1' in result.tables[0].reason
