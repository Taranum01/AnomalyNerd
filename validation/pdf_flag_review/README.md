# Review of every flag on 22 real papers

This folder records a hand check of every flag AnomalyNerd raised on 22 public papers, read from
their PDFs. The aim is to measure how trustworthy PDF results are, not only how many tables can
be read.

- `papers.json`: the 22 papers (20 from arXiv, 2 from PLOS). Six of them produce no flags.
- `labels.json`: each of the 104 flags, with the judgment and the reason for it.
- `collect_flags.py`: downloads the papers and re-collects the flags, so the set can be compared
  with `labels.json`. It reproduces the same 104 flags.

## Method

Every flag was checked against the extracted table and, where the table could be misread,
against the PDF's own text. Each flag got one of three labels:

- **false alarm**: the flag comes from a reading error (a header row read as data, text labels
  read as values, a prose example read as a table) or compares things that should not be
  compared.
- **real, explained**: the value is read correctly and does stand out, but for a reason the table
  itself makes obvious (a baseline, the largest model, a control row, a hyperparameter).
- **real, worth a look**: the value is read correctly and stands out with no obvious reason in
  the table. These are the flags a reviewer would want to see.

The judgments are one reviewer's, made with the table and text in view. They have not been
checked by a second reviewer.

## Results

| | flags | false alarm | real, explained | real, worth a look |
|---|---|---|---|---|
| all | 104 | 31 (30%) | 50 (48%) | 23 (22%) |
| HIGH | 67 | 20 (30%) | 36 (54%) | 11 (16%) |
| MEDIUM | 29 | 9 (31%) | 11 (38%) | 9 (31%) |
| LOW | 8 | 2 (25%) | 3 (38%) | 3 (38%) |

So 70% of flags point at a value that was read correctly and really stands out, and about one in
five is worth a reviewer's attention. Priority does not yet separate the useful flags well: the
"worth a look" share is no higher for HIGH than for MEDIUM.

## An internal inconsistency it found

In the T5 paper (arXiv:1910.10683), the main-text table on adapter layers (page 30) gives the row
for d = 2048 exactly the same seven scores as the row for d = 128 (81.51, 16.62, 79.47, 63.03,
19.83, 27.50, 22.63). The appendix table (page 57) gives different values for d = 2048 (for
example 82.62 GLUE and 68.61 SuperGLUE). The duplicate-value detector flagged all seven columns.
It looks like a copied row in the main table, and we found it with no prior knowledge of it.

Other examples worth a look: two models with the identical validation score 64.13% in RoFormer
(arXiv:2104.09864, page 12); in Llama 2 (arXiv:2307.09288), one comparison model evaluated on 1869
prompts while every other was evaluated on 1917 (page 56).

## Remaining causes of false alarms

| flags | cause |
|---|---|
| 7 | blank row labels meaning "same as above" grouped as one category |
| 9 | a column of text labels (prompt source, evaluation method) treated as a measured value |
| 4 | a table of prose examples (model responses) read as a results table |
| 4 | merged cells (exponents such as 2^29 collapsed to "2 29") garble the row labels |
| 2 | an architecture table's text resolutions treated as competing results |
| 2 | a batch index treated as a measured value |
| 3 | other single cases (side-by-side sub-tables, fused numbers, scores on different tasks pooled) |

One cause found in this review was fixed before the counts above: a second header row of model
sizes ("175B" under "GPT-3") was read as data, producing 8 false HIGH flags.
