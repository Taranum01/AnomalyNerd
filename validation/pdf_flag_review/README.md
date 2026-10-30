# Review of every flag on real papers

This folder records a hand check of every flag AnomalyNerd raised on public papers, read from
their PDFs. The aim is to measure how trustworthy PDF results are, not only how many tables can
be read.

Two sets of papers:
- **Development set** (`papers.json`, `labels.json`): 22 papers (20 from arXiv, 2 from PLOS),
  104 flags. Six papers produce no flags.
- **Held-out set** (`heldout_papers.json`, `heldout_labels.json`): 10 further arXiv papers,
  74 flags, reviewed later. It was not used to tune the tool, but the two fixes below were
  found partly by studying its false alarms.
- **Fresh sets** (`fresh_papers.json`, `fresh2_papers.json`): 10 and 10 more arXiv papers, used
  only to test the fixes below. Only the flags the fixes changed were labelled
  (`fresh_removed_labels.json`, `fresh_round2_changes.json`).

`collect_flags.py` downloads the papers and re-collects the flags (`--heldout` for the second
set, `--fresh` and `--fresh2` for the others). Both labelled sets reproduce exactly against the
code at the commit that added this folder; with the fixes below, they give the counts in "Fixes".

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
| held-out (10 papers) | 74 | 15 (20%) | 48 (65%) | 11 (15%) |

About four in five flags point at a value that was read correctly and really stands out, and the
false-alarm rate is the same on papers the review never touched. Between one in eight and one in
four flags is worth a reviewer's attention.

Priority does not yet separate the useful flags: on both sets, a MEDIUM flag is more often worth a
look than a HIGH one (development 45% vs 16%, held-out 58% vs 8%).

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
- Held-out set: 10 of 15 come from one cause, a "best method changes" flag on a table where each
  column is a different task, so a different winner per column is expected, not a reversal.

## A tuning attempt that did not hold up

We tried fixing the development-set causes and re-tuning priority from the development labels.
On the development set this cut false alarms from 21% to 6%. On the held-out set it changed
nothing (22% before, 24% after), and the re-tuned priority made HIGH flags less useful (6% worth a
look before, 0% after; figures with the labels at the time, before one held-out relabel). Those changes were fitted to the development papers, so they were not
adopted. The held-out set is what made that visible, and it is why it is kept separate.

## Fixes

The false alarms above come from a few causes, each with a narrow fix; none was tuned on a score
threshold. Fixes 1 to 4 only remove flags. Fixes 5 to 7 change how a table is read, so they can
also bring in new flags.

1. **A "winner" with nothing to beat.** A winner now counts only when at least two entries
   compete at that level (a score column read as an axis, garbled labels, example responses).
2. **Model sizes read as results.** Outlier flags are not reported on a column of model sizes
   (7B, 937M). Duplicate and flatness checks still run on it.
3. **A '-' placeholder used as a label.** It is treated as missing, never as an entity.
4. **A sideways table.** Flatness is not checked across rows that each name a data split and a
   metric (for example 'Dev PPL' and 'Test PPL').
5. **A 'Total' row.** It is shown in the extracted table but left out of the checks, since it is
   computed from the other rows.
6. **Two rows' numbers fused into one cell** ('0.25 0.23'). If the cell above or below is empty,
   the numbers are split back into their rows; if every number in a row is fused, the row is split
   in two. A table that still has fused cells after this is misaligned, so it is reported as not
   checked.
7. **Small tables printed side by side** and read as one. When every header is 'Group.Sub' and
   the groups repeat the same sub-columns, each group is checked as its own table.

One new check was added at the same time:

8. **Unit typos.** A column where every value carries the same unit (M) except one (G), whose
   number lies inside the range of the others, is flagged. On the YOLOv7 paper (Table 2, page 7)
   it finds YOLOR-W6 listed with 79.8G parameters among values from 1.9M to 154.7M, an error in
   the paper. Columns whose values really span units (125M to 175B) are not flagged.

Counts with all fixes, against the hand labels. Flags that appear only after a fix were checked
against the PDF and labelled too (marked `added` in `labels.json`).

| set | flags | false alarm | real, explained | real, worth a look |
|---|---|---|---|---|
| development, before | 104 | 22 | 55 | 27 |
| development, after | 88 | 1 | 53 | 34 |
| held-out, before | 74 | 15 | 48 | 11 |
| held-out, after | 53 | 0 | 42 | 11 |

The held-out "before" row reflects one relabel: a Swin flag (ACNet test score 38.5) first marked a
false alarm is a value read correctly that stands out with no reason given, so it is worth a look.

Of the 27 development flags worth a look, 21 are unchanged. Four, on the Llama 2 batch table, now
appear in a different form: with the Total row left out, the batch number is read as the axis, and
the same findings (two batches with long prompts, longer examples and responses later) are
reported along it. Two are lost: a level shift in turns per dialogue on that table, and a ChatGPT
score of 0.15 on page 71, which only stood out because two neighbouring cells had been misread;
with those cells repaired it falls just below the threshold. The new flags worth a look come from
tables that are now read correctly (ChatGPT sentiment scores on page 74, Falcon's ToxiGen score).

The one false alarm left is RoBERTa (page 13): a low-scoring task (CoLA) pooled with the others.
A rule that compares each value only within its own column removed it, but also removed a real
finding of the same shape (the ChatGPT score above), so it was not kept.

**Fresh papers.** The first two fixes were tested on `fresh_papers.json`: they removed 18 of 56
flags (14 false alarms, 4 explained) and added none. The other five fixes were then tested on both
fresh sets. On the first set they removed one explained flag and added one explained flag. On
`fresh2_papers.json`, never used before, they removed 14 of 58 flags (3 false alarms from a
shredded table, 4 Total-row flags, one digit-test note, and 6 explained flags that reappear in a
new form) and added 6 (those re-expressions). No flag worth a look was lost on either fresh set.

The unit-typo check adds one flag on the development set (LoRA, page 23: full fine-tuning
trains all 175B parameters, a real value labelled explained) and one on `fresh2_papers.json` (the
YOLOv7 error above). It adds nothing else on any set. The flags that
did not change on the fresh sets have not been labelled.
