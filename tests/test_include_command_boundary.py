"""Project expansion matches complete TeX input/include control words only."""
from pathlib import Path
import pytest
from anomalynerd.latex_ingest import expand_project

@pytest.mark.parametrize('command', [
    r'\includegraphics{figure}',
    r'\includegraphics[width=\textwidth]{figure}',
    r'\includegraphics [scale=0.6] {figure}',
    r'\includeonly{appendix}',
    r'\inputencoding{utf8}',
    r'\inputminted{python}{example.py}',
    r'\includestandalone{diagram}',
    r'\inputextra{child}',
    r'\includeExtra{child}',
    r'\inputZ{child}',
])
def test_longer_control_word_is_preserved(tmp_path, command):
    main = tmp_path / 'main.tex'
    main.write_text('before\n' + command + '\nafter')
    expanded, included, issues = expand_project(str(main))
    assert expanded == main.read_text()
    assert included == ['main.tex']
    assert issues == []

@pytest.mark.parametrize('command', [r'\input{child}',r'\include{child}',r'\input child',r'\include child.tex',r'\input{child.tex}',r'\include {child}'])
def test_real_include_still_expands(tmp_path, command):
    main = tmp_path / 'main.tex'
    main.write_text(command)
    (tmp_path / 'child.tex').write_text('child content')
    expanded, included, issues = expand_project(str(main))
    assert expanded == 'child content'
    assert included == ['main.tex','child.tex']
    assert issues == []

def test_real_missing_include_is_not_suppressed(tmp_path):
    main = tmp_path / 'main.tex'
    main.write_text(r'\input{absent}')
    expanded, included, issues = expand_project(str(main))
    assert issues == ['unreadable include: absent.tex']
