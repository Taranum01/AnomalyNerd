"""20 deterministic synthetic regression cases for before/after CLI evidence."""
import json,tempfile
from pathlib import Path
from anomalynerd.latex_ingest import parse_latex,_clean_cell
from anomalynerd.pdf_ingest import _assess_and_tidy

def run():
    out=[]
    for i,rule in enumerate([r'\cmidrule(lr){2-10}',r'\cmidrule[1pt](lr){2-10}',r'\cmidrule(r){1-3}',r'\cline{1-4}',r'\midrule[1pt]']):
        val=_clean_cell(rule+' X');out.append({'id':'rule'+str(i),'pass':val=='X','value':val})
    for i in range(5):
        table=r'\begin{tabular}{lr}Method & Score \\ A & VALUE \\ B & 8 \\ \end{tabular}'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/f'child{i}.tex').write_text(table.replace('VALUE',str(i+1)));(p/'main.tex').write_text('\\input{child'+str(i)+'}')
            result=parse_latex(str(p/'main.tex'));out.append({'id':'include'+str(i),'pass':len(result.tables)==1})
    for i in range(5):
        source=r'\begin{tabular}{lrr}Method & \multicolumn{2}{c}{Score} \\ & A & B \\ X & 1 & 2 \\ Y & 3 & 4 \\ \end{tabular}'
        source=source.replace('Score',f'Score{i}');t=parse_latex(source).tables[0]
        out.append({'id':'span'+str(i),'pass':len(t.header)==3 and 'Score' in t.header[1] and 'Score' not in str(t.rows)})
    for i in range(5):
        h=[f'Figure {i+1}: Mean weighted','latency','comparison'];t=_assess_and_tidy([h,['A','1','2'],['B','3','4']],1,0)
        out.append({'id':'caption'+str(i),'pass':t.status=='skipped' and t.header==h})
    return out

if __name__=='__main__':print(json.dumps(run(),indent=2))
