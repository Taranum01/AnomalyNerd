import json
import pytest
from anomalynerd.latex_ingest import parse_latex,_parse_tabular,_clean_cell,expand_project
from anomalynerd.bounded import run_bounded

TABLE=r'\begin{tabular}{lr}Method & Score \\ A & 1 \\ B & 2 \\ \end{tabular}'

def test_project_includes_and_missing(tmp_path):
    (tmp_path/'child.tex').write_text(TABLE)
    main=tmp_path/'main.tex';main.write_text(r'\input{child}\input{missing}')
    p=parse_latex(str(main));assert len(p.tables)==1
    assert p.included_files==['main.tex','child.tex']
    assert p.tables[0].source_locations==[{'file':'child.tex','line':1}]
    assert p.tables[0].source_location_status=='exact'
    assert any('missing' in x for x in p.include_issues)

def test_cycle_and_outside_not_followed(tmp_path):
    p=tmp_path/'main.tex';p.write_text(r'\input{main}\input{../secret}')
    text,files,issues=expand_project(str(p));assert len(files)==1
    assert any('cycle' in x for x in issues)
    assert any('outside' in x for x in issues)

@pytest.mark.parametrize('rule',[r'\cmidrule(lr){2-10}',r'\cmidrule[1pt](lr){2-10}',r'\cline{2-10}',r'\midrule[1pt]'])
def test_rule_coordinates_removed(rule):assert _clean_cell(rule+' x')=='x'

def test_span_and_escaped_ampersand():
    h,r=_parse_tabular(r'{lrr}Method & \multicolumn{2}{c}{Scores} \\ & A & B \\ X\&Y & 1 & 2 \\ Z & 3 & 4 \\')
    assert len(h)==3 and r[0]==['X&Y','1','2']

def test_multirow():
    h,r=_parse_tabular(r'{llr}Group & Method & Score \\ \multirow{2}{*}{G} & A & 1 \\ & B & 2 \\')
    assert r==[['G','A','1'],['G','B','2']]

def test_ragged_not_silently_padded():
    p=parse_latex(r'\begin{tabular}{lr}A&B\\ X&1&2\\ Z&3\\\end{tabular}')
    assert p.tables[0].status=='skipped' and 'extraction failure' in p.tables[0].reason

def test_checkpoint_reused_and_timeout_explicit(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,runs=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert all(r['status']=='completed' for r in runs)
    _,again=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert again==runs
    _,short=run_bounded(tidy,seconds=.00001)
    assert all(r['status']=='timed_out' for r in short)


def test_fragmented_pdf_header_is_extraction_failure():
    from anomalynerd.pdf_ingest import _assess_and_tidy
    rows = [['Method Runtime','s (ms) Reference','gain (%)'],['A','10','12'],['B','11','13']]
    t=_assess_and_tidy(rows,1,0)
    assert t.status=='skipped' and t.original_header==rows[0]
    assert 'split across columns' in t.reason

def test_caption_figure_header_is_not_analyzed():
    from anomalynerd.pdf_ingest import _assess_and_tidy
    t=_assess_and_tidy([['Figure 5: Mean weighted','latency','comparison'],['A','1','2'],['B','3','4']],1,0)
    assert t.status=='skipped' and t.tidy is None


def test_duplicate_source_locations_are_ambiguous(tmp_path):
    (tmp_path/'a.tex').write_text(TABLE)
    (tmp_path/'b.tex').write_text(TABLE)
    p=tmp_path/'main.tex';p.write_text(r'\input{a}\input{b}')
    parsed=parse_latex(str(p))
    assert all(t.source_location_status=='ambiguous_or_unmapped' for t in parsed.tables)
    assert all(len(t.source_locations)==2 for t in parsed.tables)

def test_checkpoint_corruption_recomputed(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,first=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    files=list(tmp_path.glob('*.json'));files[0].write_text('{broken')
    _,second=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert all(r['status']=='completed' for r in second)
    assert json.loads(files[0].read_text())['status']=='completed'

def test_timeout_checkpoint_not_reused(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,runs=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    for path in tmp_path.glob('*.json'):
        record=json.loads(path.read_text());record['status']='timed_out';record['reason']='synthetic timeout';path.write_text(json.dumps(record))
    _,again=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    assert all(r['status']=='completed' for r in again)

def test_checkpoint_configuration_changes_key(tmp_path):
    tidy=parse_latex(TABLE).tables[0].tidy
    _,a=run_bounded(tidy,seconds=4,checkpoint_dir=str(tmp_path))
    _,b=run_bounded(tidy,seconds=3,checkpoint_dir=str(tmp_path))
    assert a[0]['key']!=b[0]['key']
