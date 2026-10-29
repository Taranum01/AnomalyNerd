# Review of every flag on real papers

This folder records a hand check of every flag AnomalyNerd raised on public papers, read from
their PDFs. The aim is to measure how trustworthy PDF results are, not only how many tables can
be read.

Two sets of papers:
- **Development set** (`papers.json`, `labels.json`): 22 papers (20 from arXiv, 2 from PLOS),
  104 flags. Six papers produce no flags.
- **Held-out set** (`heldout_papers.json`, `heldout_labels.json`): 10 further arXiv papers,
  74 flags, reviewed later and never used to change the tool.

`collect_flags.py` downloads the papers and re-collects the flags (`--heldout` for the second
set). Both sets reproduce exactly against the code at the commit that added this folder.

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
checked by a second reviewer. Nine development labels were corrected after a second look at the
PDF text (marked with a `relabeled` field); for example, two VGG configurations really do tie at
24.8, so those flags are real, not false alarms.

## Results

| set | flags | false alarm | real, explained | real, worth a look |
|---|---|---|---|---|
| development (22 papers) | 104 | 22 (21%) | 55 (53%) | 27 (26%) |
| held-out (10 papers) | 74 | 16 (22%) | 48 (65%) | 10 (14%) |

About four in five flags point at a value that was read correctly and really stands out, and the
false-alarm rate is the same on papers the review never touched. Between one in eight and one in
four flags is worth a reviewer's attention.

Priority does not yet separate the useful flags: on both sets, a MEDIUM flag is more often worth a
look than a HIGH one (development 45% vs 16%, held-out 58% vs 6%).

## An internal inconsistency it found

In the T5 paper (arXiv:1910.10683), the main-text table on adapter layers (page 30) gives the row
for d = 2048 exactly the same seven scores as the row for d = 128 (81.51, 16.62, 79.47, 63.03,
19.83, 27.50, 22.63). The appendix table (page 57) gives different values for d = 2048 (for
example 82.62 GLUE and 68.61 SuperGLUE). The duplicate-value detector flagged all seven columns.

Other examples worth a look: two models with the identical validation score 64.13% in RoFormer
(arXiv:2104.09864, page 12); in Llama 2 (arXiv:2307.09288), one comparison model evaluated on 1869
prompts while every other was evaluated on 1917 (page 56).

## Main causes of false alarms

- Development set: columns of model sizes (7B, 175B) analyzed as results; blank row labels meaning
  "same as above" grouped as one category; tables of prose example responses read as results
  tables; merged cells garbling row labels.
- Held-out set: 10 of 16 come from one cause, a "best method changes" flag on a table where each
  column is a different task, so a different winner per column is expected, not a reversal.

## A tuning attempt that did not hold up

We tried fixing the development-set causes and re-tuning priority from the development labels.
On the development set this cut false alarms from 21% to 6%. On the held-out set it changed
nothing (22% before, 24% after), and the re-tuned priority made HIGH flags less useful (6% worth a
look before, 0% after). Those changes were fitted to the development papers, so they were not
adopted. The held-out set is what made that visible, and it is why it is kept separate.
