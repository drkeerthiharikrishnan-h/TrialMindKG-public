# Blinded spot check of the Pharos pruner: pre-registered pass rule

Status: **PRE-REGISTERED 2026-11-08 (22:40 IST), before anyone opened the sheet.** Packet CS-4 of
`docs/handoff/FIX_PLAN_2026-11-08.md` (review finding M5). Ledger row: `loop/ledger.tsv`, id `CS-4-spotcheck`.
Claude Science generated the sheet and the key with `scripts/make_spotcheck_blinded.py` and has not looked at the
key's per-row contents.

**Amended 2026-11-11 (Amendment 1, at the end of this file): the sample was re-drawn from the shipped verdict file
before any rating; the rule numbers are unchanged.**

## What is rated

- Sheet: `outputs/pharos/spotcheck_blind.xlsx`, tab `spot_check` (200 rows), tab `README` (seed 20261108, inputs,
  method). Shown per row: subject name + id, predicate, object name + id, source (primary knowledge source),
  knowledge level, the source's own statistic(s), the first PMIDs. Not shown: the pruner's verdict, confidence, reasons.
- Key (closed): `outputs/pharos/spotcheck_blind_key.tsv` (row, verdict, confidence, reasons, edge id, predicate group,
  stratum size). Do not open it until every row is rated.
- Population: the shipped verdict file `data/kg/pharos/edge_verdicts.tsv.gz` (26,462,915 edges: 242,833 prune,
  26,220,082 keep). Sample: 100 pruned + 100 kept, stratified by predicate group (8 groups; the card's pair group
  `gene_condition` joins gene_associated_with_condition and genetically_associated_with), proportional allocation
  with at least one row per group, rows shuffled.

## The rule

`RULE: prune_unsupported_min=60; keep_unsupported_max=15`

**PASS if at least 60 of the 100 pruned edges are judged `unsupported` AND at most 15 of the 100 kept edges are
judged `unsupported`.** Otherwise FAIL.

- Judgements: `supported` (the claim is right, or its evidence backs it), `unsupported` (wrong, or not backed by its
  evidence), `cannot tell`. `cannot tell`, a blank cell and any other value count as neither: they are not
  `unsupported`, so they count against the first condition and for the second.
- The owner may change the two numbers **before opening the sheet**: edit the `RULE:` line above and add a dated ledger
  row saying so. After the sheet is opened the numbers are fixed; the result is reported either way.
- Rater: the owner. Second rater (optional): the Tue 10 Nov stranger rates 50 rows (rows 1-50) in a copy of the sheet;
  agreement is reported next to the result and does not change PASS/FAIL.

## Scoring

    .venv/bin/python scripts/score_spotcheck.py --write outputs/pharos/spotcheck_blind_result.md
    # with a second rater: add --sheet2 <their copy>.xlsx

The scorer reads the thresholds from the `RULE:` line, joins the sheet to the key by `row`, and prints the two counts,
PASS/FAIL and the verdict x judgement table (the `cannot tell` and blank counts are listed separately).

## Known limits (decided before rating)

- Proportional allocation with at least one row per group puts one or two rows in each small group; the 100-row
  counts estimate each verdict's error rate on this stratified sample, not a re-weighted population rate (stratum
  sizes are in the key if a weighted figure is wanted afterwards).
- TIN-X text-mining edges (knowledge level `text_co_occurrence`) are included at their share of the population; they
  are hard to judge from the row alone, so expect `cannot tell` there.
- `has_gene_product` is a never-prune predicate on the card (`sources/pharos.yaml`, `prune.never_prune_predicates`),
  so a row with that predicate is necessarily a kept edge.
- The previous unblinded sheet `outputs/pharos/spotcheck_sheet.xlsx` is left in place; it shows verdicts and must not
  be opened before this rating is done.

## Amendment 1 (2026-11-11, 16:30 IST; written before any rating)

Status: **PRE-REGISTERED AMENDMENT, before any row of either sheet was rated.** Hand-off
`docs/handoff/HANDOFF_2026-11-11.md` task S01; plan `docs/SPRINT_PLAN_2026-11-11.md` Appendix A step 1. Ledger row:
`loop/ledger.tsv`, id `CS-4-spotcheck-amend1`. The owner stated on 11 Nov 2026 that she has neither read nor rated any
row of the 8 Nov sheet; the `owner_judgement` column of that sheet is empty in all 200 rows. Claude Science generated the
new sheet and key with the unchanged `scripts/make_spotcheck_blinded.py` and has not read the rows of either key; from
the 8 Nov key only the count join below was computed.

### Why the sample was drawn again

- The 8 Nov sample came from the verdict file as it stood on 8 Nov (26,462,915 edges: 242,833 prune, 26,220,082 keep).
  The Pharos layer and its verdicts were rebuilt on 9 Nov. The shipped file `data/kg/pharos/edge_verdicts.tsv.gz` has
  26,462,802 edges: 256,639 prune, 26,206,163 keep (the counts of `outputs/pharos/prune_report.md`).
- Joining the 200 edge ids of the 8 Nov key to the shipped file (counts only, no row printed or read):
  100 keep -> keep, 92 prune -> prune, 8 prune -> keep. Eight of the 100 rows drawn as pruned are kept edges in the
  shipped graph, so the 8 Nov sheet no longer samples the shipped verdicts. 0 of its 200 rows were rated.
- The 8 Nov files were moved, unrated and byte-identical, to `outputs/pharos/archive/spotcheck_blind_2026-11-08.xlsx`
  and `outputs/pharos/archive/spotcheck_blind_key_2026-11-08.tsv`. They are not scored and not reported as a result.

### What is rated now (replaces the population and seed in "What is rated")

- Sheet `outputs/pharos/spotcheck_blind.xlsx` and closed key `outputs/pharos/spotcheck_blind_key.tsv`, drawn
  11 Nov 2026 16:19 IST with `--seed 20261111`. The README tab reads 26,462,802 verdict rows.
- Population: the shipped verdict file (26,462,802 edges: 256,639 prune, 26,206,163 keep). Sample: 100 pruned + 100
  kept, same method as before (strata = verdict x predicate group, 8 groups, proportional allocation with at least one
  row per group, bottom-k reservoir sampling in one streamed pass, rows shuffled). No edge id is in both samples (0 of 200).
- File identity at the time of this amendment (sha256):
  sheet `d5d2527fb07a426121d421e25c5d8071b5220f24c752fa59e30a589de8a7aec8`,
  key `afa576ce95dd420c80ab9d2bcda26919a50eb1884aa8bbfeed76b611466e4d57`,
  verdict file `0a591ff238a9e1741d5a4687c30a14f885656b73168910428fbefae5451af1bd`.
  The owner's ratings will change the sheet's hash; the key's hash must not change.

### The rule: unchanged

The numbers stand as written above: at least 60 of the 100 pruned edges judged `unsupported` and at most 15 of the 100
kept edges judged `unsupported`. The scorer still reads them from the single `RULE:` line above; this amendment adds no
second rule line. `cannot tell`, a blank cell and any other value still count as neither.

### Procedure (replaces the "Rater" bullet above)

1. One evidence dossier per row, collected from sources the pruner never reads (plan Appendix A step 2).
2. Verdict-blind model judges propose a label per row from the dossier. No judge receives the verdict, the confidence or
   the reasons. A label that cites anything outside the dossier becomes `cannot tell`.
3. The owner rates a seeded random 40 of the 200 rows with the dossier open and without seeing the proposals, then
   adjudicates the rows on which the raters disagree.
4. The rule is applied to the final labels of the 200 rows. Agreement between the raters and with the owner is reported
   next to the result.

### Still to be appended, before the first judge run (task S04)

- The decision rules per predicate group: what counts as `supported`, `unsupported` and `cannot tell`.
- Not yet fixed anywhere and to be written in the same addition: the seed of the owner's 40 rows, and which label is
  final for a row where the owner's blind rating and the proposal differ.

### Statements above that this amendment supersedes

- "What is rated": seed 20261108 and the population 26,462,915 / 242,833 / 26,220,082 (now seed 20261111 and
  26,462,802 / 256,639 / 26,206,163).
- "Rater": the optional second human rater on rows 1-50 is replaced by the procedure above.
- "Known limits", last bullet: the unblinded sheet of 7 Nov is now `outputs/pharos/archive/spotcheck_sheet.xlsx`
  (moved with the rejected candidate pruner's files `prune_report_v2.md`, `prune_metrics_v2.json`,
  `spotcheck_v1_vs_v2.xlsx`, `spotcheck_v1_vs_v2_key.tsv`). It still shows verdicts and must not be opened before
  this rating is done.

## Amendment 1, addition A: decision rules, raters and final labels (2026-11-12, between 02:02 and 02:09 IST)

Status: **WRITTEN BEFORE THE FIRST JUDGE RUN and before any evidence dossier of the 200 rows was collected.** Hand-off
task S04; plan Appendix A steps 2 to 6. At the time of writing the sheet and the key have the sha256 values recorded in
Amendment 1 (unchanged), no row is rated, and Claude Science has not read the key. Owner approval: see A9.

### A1. What a rater sees

- The claim, exactly as the sheet shows it: subject name and id, predicate, object name and id, source, knowledge
  level, the source's own statistic, the first references.
- One evidence dossier for the row: numbered items, each from a named public source queried on a recorded date, each
  with an item id (for example `IA1`) and a link. The pruner reads none of these sources; it reads only the Pharos tables.
- Never shown to any rater, model or human: the pruner's verdict, its confidence, its reasons, the edge id.

### A2. Evidence items and context items

Every dossier item is marked `evidence` or `context`. Only an `evidence` item can be the basis of `supported`.
Context items are shown so the rater understands the row, and are these:

- STRING channel scores, on every row (STRING is the source of most interaction rows).
- Any item from the database that the row itself names as its source: an IntAct record from a BioPlex publication on a
  row whose source includes BioPlex (the same experiment), a Reactome complex or reaction on a row whose source
  includes Reactome.
- The Open Targets `literature` data type (text mining, the same kind of evidence as a co-mention row).

The abstracts of the row's own references are `evidence`: the label asks whether the claim is right or its evidence
backs it, so reading the cited paper is the direct test.

### A3. Rules common to all groups

- `supported`: at least one evidence item meets the group's condition below.
- `unsupported`: every source the group requires answered, and no evidence item meets the condition.
- `cannot tell`: a required source failed or could not match the identifiers and nothing supports the claim; or the
  evidence is of the in-between kind the group lists; or a literature search found more abstracts than the dossier
  examined (at most 25 per search) and none of the examined ones decides the row.
- Turned into `cannot tell` by the script, whatever the rater wrote: a label that cites an item id that is not in the
  dossier; a rationale that names a PubMed id or database record that is not in the dossier; a `supported` label that
  cites no evidence item. The rater's original label is kept in the file next to the corrected one.
- Rationale: 40 words or fewer. A longer one is cut at 40 words where it is shown; the label is not changed.
- Absence from a database is not proof that a claim is false. `unsupported` here means "not backed by any source in
  the dossier", the second half of the label's definition. This applies to pruned and kept rows alike.

### A4. Rules per predicate group

**Protein-protein rows** (`physically_interacts_with`; sources STRING, BioPlex, Reactome, the Pharos placeholder
source). Claim: the two proteins interact physically (direct binding or membership of one complex). Required sources:
IntAct, Complex Portal, Reactome, Europe PMC.
- `supported` if at least one of: (a) an IntAct experimental record for the pair with interaction type association,
  physical association, direct interaction or a more specific type under these (for example phosphorylation reaction),
  not a negative record; (b) both proteins are participants of one curated Complex Portal complex; (c) both are
  components of one Reactome complex, or share a Reactome reaction whose name describes binding, complex formation or
  one protein modifying the other; (d) an abstract sentence states that the two proteins bind, form a complex, or that
  one directly modifies the other.
- `cannot tell` (in-between evidence) if the only evidence is a functional link without physical contact: an IntAct
  record of type colocalization or genetic interaction; a shared Reactome reaction that does not describe binding or
  modification; an abstract that states a regulatory, pathway or co-expression relation only.
- `unsupported` if all four sources answered and there is nothing: no IntAct record, no shared complex, no shared
  reaction, and no abstract that links the two proteins (none found, or the two are only named in a list).
- A row whose subject and object are the same protein is judged the same way on self-interaction: an IntAct record in
  which the protein is both partners, or a complex that holds two or more copies of it.

**Gene-disease rows** (`gene_associated_with_condition`, `genetically_associated_with`; TIN-X text co-mention,
DisGeNET, TIGA and the other Pharos disease sources). Claim: the gene or its product is associated with the disease or
trait. Required sources: Open Targets (association scores), Open Targets' literature sentences (Europe PMC
text-mined sentences naming the gene with the disease or a more specific disease under it; see A10), Europe PMC
(co-mention search on the disease's own names), and the abstracts of the row's own references where it has any.
- `supported` if at least one of: (a) Open Targets holds an association (direct, or through a more specific disease
  term) in which at least one data type other than `literature` scores 0.1 or more (Open Targets' curated
  `genetic_literature` type is not text mining and counts); for `genetically_associated_with` that data type must be
  one of the genetic ones (`genetic_association`, `genetic_literature`, `somatic_mutation`); (b) an abstract or a text-mined sentence states a relation between
  this gene and this disease, or a more specific disease under it: causal or risk variant, mutation or expression change found in patients, biomarker, drug target, or
  a functional role in a model of the disease. For a text co-mention row this is the plan's rule: supported only if at
  least one retrieved abstract states a relation, not a list co-mention.
- `cannot tell` if Open Targets holds only non-literature evidence below 0.1 and no abstract or sentence states a
  relation; or they use the gene symbol or disease name in another meaning.
- `unsupported` if the required sources answered, no non-literature Open Targets data type reaches 0.1, and no abstract
  or sentence states a relation (none found; or the two are named together only in a list or in unrelated statements;
  or they state that no association was found).

**Gene Ontology rows** (`located_in`, `capable_of`, `actively_involved_in` with a GO term; source GOA). Claim: the
protein has this location, function or process. Required source: QuickGO.
- `supported` if QuickGO holds an annotation of this protein to the same term or a more specific term, without the
  `NOT` qualifier, with an evidence code other than IEA (electronic) and ND (no data).
- `cannot tell` if the only such annotations are IEA or ND.
- `unsupported` if QuickGO answered and holds no annotation of the protein to the term or a more specific term, or only
  `NOT` annotations.

**Pathway rows** (`actively_involved_in` with a pathway id; sources Reactome, WikiPathways, KEGG). Claim: the protein
takes part in this pathway. Required source: the pathway database the row names, read directly (Reactome content
service; WikiPathways participant list). KEGG is not queried (its interface is licensed for academic use only).
- `supported` if the database's current release lists the protein (or its gene) as a participant of that pathway.
- `unsupported` if the database answered, the pathway exists, and the protein is not a participant.
- `cannot tell` for every KEGG row, and when the pathway id is no longer in the database.

**Ligand rows** (`directly_physically_interacts_with`; Pharos ligand activity, from ChEMBL and other sources). Claim:
the compound acts on the protein. Required source: ChEMBL activity records.
- `supported` if ChEMBL holds at least one activity record of this compound against this protein as a single-protein
  target with pChEMBL 5 or more (10 micromolar or stronger), and no data-validity warning on that record.
- `cannot tell` if the only records are against a complex or protein family that contains the protein, carry no
  pChEMBL value, or carry a data-validity warning; or the compound id is not a ChEMBL id.
- `unsupported` if ChEMBL answered and holds no activity record for the pair, or every record is weaker than
  pChEMBL 5 or marked inactive.

**Mouse-phenotype rows** (`has_phenotype`; IMPC, MGI). Claim: loss of the gene gives this phenotype. Required source:
IMPC (the knockout of the mouse orthologue).
- `supported` if IMPC lists a significant phenotype call for the orthologue's knockout with the same phenotype term or
  a more specific term.
- `cannot tell` if no mouse orthologue is found in IMPC, the term is not a Mammalian Phenotype term, or the only
  significant call is to a broader term.
- `unsupported` if IMPC answered, the orthologue has data, and there is no significant call for the term or a more
  specific term.

**Gene-to-protein rows** (`has_gene_product`). No model. `supported` if the UniProt entry of the protein
cross-references the gene id; `unsupported` if the entry is current and does not; `cannot tell` if the entry is
obsolete or UniProt failed. The script's label is recorded as the label of all raters.

**Any other predicate** that a sample contains: Europe PMC co-mention search plus the row's own references;
`supported` only if an abstract states the relation the predicate claims; `unsupported` if the search answered and
nothing states it; otherwise `cannot tell`.

### A5. Raters and how their labels are combined

- Three verdict-blind raters, all called through the application's own model client (`pipelines/nim.py`), with the
  generation settings of `config/nim_models.yaml` (temperature 0, top_p 1, seed 2026), structured output: label,
  rationale, item ids relied on. Each call is stored, so the run can be replayed.
- Judge A: `nvidia/nemotron-3.5-super-vl-120b-a12b`. Judge B: `nvidia/nemotron-3.5-lightning-30b-a3b` (the
  application's own judge model). Rater C, from another model family: the first of
  `mistralai/mistral-large-4-1t-a52b`, `google/gemma-4-31b-it`, `deepseek-ai/deepseek-v4.1-flash` that answers a
  structured-output test call on this account on the day of the run; the id used is recorded with the result.
- Proposed label of a row = the label all three raters give. A row on which the three do not all agree is **flagged**.
  For a flagged row the majority label (two of three) is recorded; when all three differ there is none.

### A6. The owner's 40 rows and the final label of each of the 200 rows

- The owner's 40 rows: a simple random sample of 40 of the 200 row numbers, `numpy.random.default_rng(20261112)`,
  drawn without reference to the key. Sheet `outputs/pharos/spotcheck_owner40.xlsx`: the claim, the dossier text, an
  empty judgement column; no proposal, no rater label, no rationale.
- After the 40 are rated, the owner adjudicates the flagged rows that are not among her 40, in a second sheet that
  shows the dossier and the three raters' labels and rationales.
- Final label: (1) a row among the owner's 40: the owner's blind label; (2) any other row on which the three raters
  agree: that label; (3) a flagged row the owner adjudicated: her label; (4) a flagged row she did not adjudicate: the
  majority label, or `cannot tell` when there is none. The number of rows under (4) is reported.
- The pre-registered rule (60 / 15) is applied to the 200 final labels. Reported next to it, without changing
  PASS or FAIL: agreement between each pair of raters and between the owner and the proposal on her 40 rows (raw and
  Cohen's kappa), and a per-source table with Wilson intervals. The machine labels are described as "checked against a
  blind human subset" only if the owner and the majority label agree on at least 32 of the 40 rows; otherwise the
  result states that they were not.

### A7. The separate audit sample (never mixed into the rule)

500 pruned + 500 kept edges drawn from the shipped verdict file with the same sampler and seed 20261113, files
`outputs/pharos/audit_sample_blind.xlsx` and `audit_sample_key.tsv`. Machine only, same dossiers and rules, label =
majority of the three raters (`cannot tell` when all three differ); the share of rows with three-way agreement is
reported. Reported as an "independent audit" table, separately from the 200 rows; it has no pass rule.

### A8. Limits, stated before the run

- The raters read only the dossier but may carry prior knowledge from training; the outside-citation check catches
  named records, not unstated knowledge.
- STRING's experimental channel is partly built from IntAct, and Open Targets is a layer of the enriched graph. The
  pruner reads neither, but the sources are not statistically independent of the edges' origins.
- For Gene Ontology, pathway, ligand and gene-to-protein rows the check is whether the originating public database
  still holds the record and at what evidence level. That is a check of the record, not an independent replication.
- IntAct, Complex Portal and Reactome cover a minority of true human protein interactions, so `unsupported` on a
  protein-protein row means "no record in these sources and no stated interaction in the abstracts examined".
- The result may fail the rule, most plausibly on its second condition (kept rows judged unsupported). It is reported
  either way.

### A9. Owner approval

Pending at the time of writing. The first judge run starts only after the owner has seen A1 to A8; her decision and
any change she asks for are recorded here, dated, before that run.

**Approved by the owner on 2026-11-12 at about 07:45 IST, before any rater had run.** She was shown a summary of A1 to
A10 with the counts of A10 and was asked three questions; she had not read the file in full. Her answers:

1. "May the three AI raters start labelling the 200 rows now, under the rules written in this file?" Answer: yes,
   start them under the written rules.
2. A protein-protein row whose dossier holds only a functional link and no physical record: `cannot tell`, as written
   in A4.
3. A gene-disease row with no title or abstract naming both, no readable sentence and no non-literature Open Targets
   evidence of 0.1 or more, but a co-occurrence somewhere in full text: `unsupported`, as written in A3.

No rule text was changed by her answers. One retrieval fix followed the approval and preceded the first judge run:
network access to WikiPathways was granted at the same time, and the WikiPathways collector was corrected to read
that site's participant file (identifiers carry a prefix such as `ncbigene:`); the one pathway row of the 200 was
collected again.

### A10. Revision of the gene-disease retrieval (2026-11-12, 02:36 IST, corrected 02:48 IST; before any judge run and before owner approval)

What was seen: after the dossiers of the 200 rows were collected, source-status counts only (no dossier content was
read for this, the key was not opened, no rater had run) showed that the Europe PMC search on the disease's own names
found no title or abstract for 47 of the 55 gene-disease rows, although 33 of those 47 have a non-zero Open Targets
`literature` score.

First explanation, tested and rejected: that the text-mining source of these rows counts a mention of a specific
disease for its broader parent terms. Measured on the 47 rows: adding up to 40 more specific disease names to the
search finds abstracts for 4 of them; 19 of the 47 diseases have no more specific term at all; Europe PMC's own
synonym expansion finds none. Searching full text instead of title and abstract finds at least one article for 36 of
the 47. Open Targets serves readable sentences behind its literature score for 8 of the 40 rows that have such a
score. So most of these pairs have no title-or-abstract co-mention under the standard names of the gene and the
disease; where they co-occur it is in full text, which this procedure cannot read sentence by sentence.

What changed, in A4 "Gene-disease rows" above (retrieval only; condition (a) and every threshold are as first
written): (1) one more required source, Open Targets' literature sentences, which are Europe PMC sentences naming the
gene together with the disease or a more specific disease under it; a rater reads them and applies condition (b). The
Open Targets `literature` score stays a context item. (2) The Europe PMC search also uses up to 30 more specific
disease terms, nearest first, and condition (b) reads "this disease, or a more specific disease under it", as
condition (a) already did. (3) When a disease id is not an Open Targets id, the disease is looked up in Open Targets
by its exact name; a partial name match is not used. Nothing else in A1 to A9 changed.

What did not change: a pair that co-occurs only in full text, with no sentence any dossier source lets a rater read,
is `unsupported` under A3 ("not backed by any source in the dossier") unless the owner decides otherwise in A9. The
question is put to her with the counts above.

Also recorded here: the Open Targets data-type wording in condition (a) (`genetic_literature` named as a curated,
non-text-mining type) was corrected minutes after addition A was first written and before any dossier was collected,
to match the names the Open Targets interface returns.

### A11. Deviation after the first judge run: Rater C's reply limit (2026-11-12, 08:33 IST)

This is a change made AFTER the first judge run, so it is recorded as a deviation and not as part of the
pre-registration. The key was not opened; nothing below uses a verdict.

What happened: the first run (07:52 to 08:28 IST; raters as in A5, Rater C = `mistralai/mistral-large-4-1t-a52b`)
returned 600 replies. For 61 of Rater C's 200 rows the reply held no answer: the model reasons before it answers,
used the whole reply limit of 2,048 tokens (the application's setting) on that reasoning, and stopped
(`finish_reason` length, empty content). The script recorded each as `cannot tell` with the problem "no valid label in
the reply". These are missing answers, not judgements. They made Rater C the dissenting rater on 49 of the 75 flagged
rows. Judges A and B had no such reply (0 of 400).

What was changed: Rater C's reply limit was raised to 8,192 tokens (`config/nim_models.yaml`, key
`spotcheck_raters.options`). Only the calls that ended without an answer are asked again; the criterion is the
recorded problem "no valid label in the reply", which is blind to the verdict. The 139 answers Rater C completed in
the first run were not cut by the limit and are kept as they are. Prompt, rules, dossiers, temperature, seed and the
other two raters are unchanged. The same setting applies to the audit sample and to the cited edges.

What is kept: the complete labels file of the first run, unchanged, as
`outputs/pharos/archive/spotcheck_machine_labels_run1_2026-11-12.tsv`
(sha256 `f15ba3201a2c49d19a589e2dbc30c8ea5a43de636bb42de02901ca86a373fa57`). A row that still has no answer after
the second request stays `cannot tell` and is counted in the result.

The owner was told of this deviation in the session summary of 12 Nov. If she rejects it, the first-run file is the
labels file and the 61 rows stay `cannot tell`.

### A12. Deviation: one of the owner's 40 rows is not rated (2026-11-14, 13:40 IST; before any scoring)

What happened: the owner returned her 40-row sheet on 14 Nov with 39 rows rated (28 supported, 6 unsupported,
5 cannot tell) and row 132 empty. Every cell outside the judgement column equals the committed sheet. Asked about the
empty row, she instructed on 14 Nov that row 132 be ignored. No reason was recorded.

State of knowledge when this was written: the rule had not been scored; the adjudication sheet in the repository had
0 of 24 rows rated; no result file existed (`outputs/pharos/spotcheck_blind_result.md` absent). For this note only, Claude Science read from the raters' labels file
that the three raters do not all agree on row 132 (a flagged row); no label of that row was shown to the owner.

What follows, with no change to the scorer (a blank owner cell already counts as not rated):
- Row 132 is treated as a row outside the owner's rated set. It is flagged and is not in the adjudication sheet
  (that sheet holds only flagged rows outside her 40), so its final label comes from A6 case (4): the majority label.
  It is counted in the reported number of rows under (4).
- Agreement between the owner and the majority label is reported on 39 rows, not 40. The threshold for calling the
  machine labels "checked against a blind human subset" stays at 32 agreeing rows and is not rescaled, which makes it
  harder to meet, not easier.
- Row 132 is not rated later: after this note it would no longer be a blind rating.

The pre-registered rule (60 / 15) and everything else in Amendment 1 are unchanged.
