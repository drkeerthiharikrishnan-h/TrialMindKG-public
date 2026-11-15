# What TrialMindKG can and cannot tell you

Read this before you rely on an answer. It is written for a scientist or clinician, not for a programmer. Every number
below is copied from a file in this repository; the files are listed in the last section, and a test checks that the
copies still match.

## 1. What it is for

TrialMindKG is a research tool for exploring what public databases say about human genes, proteins, diseases, phenotypes
and compounds, and for seeing where a statement comes from. It is good for questions such as "which sources link this
gene to this disease, and how strong is each one?", "what is predicted but not yet reported?", and "which failed trials
touched this target?".

It is **not** a medical device and gives no medical advice. Do not use it to diagnose, to choose a treatment, or to
decide on a patient. A link in the graph means "a source reported this", not "this is true".

## 2. Where the data come from, and how old they are

The graph is a snapshot. Nothing in it is newer than the dates below, and it does not update by itself.

| source | version in this graph | what it contributes |
|---|---|---|
| Pharos / TCRD (NCATS) | Pharos 3.19.2 / TCRD 6.13.5, pulled 25 October 2026 | the core: targets, diseases, ligands, protein interactions, gene functions, phenotypes, text-mining rows, protein classes |
| Open Targets Platform | 26.09 | gene-disease associations with an overall score of 0.1 or more, drug and phenotype links |
| ChEMBL | 37 | compound-target activities and drug indications |
| Gene Ontology, Human Phenotype Ontology, MONDO | files downloaded 25 October 2026 | the hierarchies (which term is a kind of which) |
| ClinicalTrials.gov (through AACT), openFDA, GTEx, curated literature | registry and GTEx data of 5 October 2026 | the layer of 34 failed drug programs |

A discovery published after these dates is missing. A record a source corrected after these dates is still in its old
form here. Keeping the graph current is a procedure a person runs ([MAINTAIN.md](MAINTAIN.md)); no program watches the
sources.

## 3. What the graph does not cover

- **Human only.** Genes and proteins are human. Mouse phenotypes appear only as phenotype terms attached to the human
  gene by a source.
- **No patient data and no genotypes.** There are no individual variants, no patients, no samples, no expression
  measurements in the Pharos graph. It cannot say which mutation matters, in which tissue, or in whom.
- **No transcripts, cell types or anatomy in the Pharos graph.** It has 12 kinds of node (gene, protein, disease, phenotype, compound,
  pathway, three kinds of Gene Ontology term, protein class, a mixed disease-or-trait class, and chemical entity for the
  ChEBI terms of the Drug Target Ontology table). Anything outside these
  is absent, not negative. (The small trial layer adds its own kinds: trials, arms, endpoints, outcomes.)
- **Absence is not evidence of absence.** If the graph shows no link between a gene and a disease, it means none of these
  sources, in these versions, reported one in a form that could be mapped. Rows whose identifiers could not be
  standardised are counted in a list kept with each layer, not silently dropped, but they are not in the graph.
- **Most gene-disease links are text mining.** Of the gene-disease links that come from Pharos, 13,118,763 are text
  co-mention rows (the gene and the disease appear together in abstracts) and 296,974 are not. A co-mention is a weak
  statement. Every row says which kind it is.
- **The trial layer is small and chosen by hand.** It holds 34 failed drug programs, picked as case studies. It is not a
  registry of all trials and cannot be used to estimate how often trials fail.
- **The "internal data" example is synthetic.** The antibody-drug-conjugate demonstration uses invented internal data,
  labelled SYNTHETIC wherever it appears. It is not real sponsor data.

## 4. Keep and prune verdicts are estimates

Every one of the 26,462,802 Pharos links has a verdict (keep or prune), a confidence between 0 and 1, and reasons. 256,639
links (0.97%) are marked prune; they stay in the graph, marked, and are left out of the pruned download.

- **There is no answer key.** Nobody holds a list of which Pharos links are wrong, so the verdicts cannot be scored as
  right or wrong. What we can show is indirect: of the 280 pruned gene-disease links that are not text-mined, 2
  (0.7% of the pruned ones) also appear in Open Targets (which the pruning step never reads), against 17.9% of the kept ones. That says
  pruned links are less often corroborated. It does not prove they are false. And it covers few of the pruned links: those
  280 are 0.11% of the 256,639; most pruned links are protein-protein or text-mined.
- **"Prune" means "weakly supported here", not "false".** A true but little-studied link can be pruned.
- **"Keep" means "not flagged", not "true".** A kept text co-mention is still only a co-mention.
- **The blinded check failed.** A blinded check of 200 links under a rule fixed before rating FAILED: 71 of 100 pruned and
  56 of 100 kept links were judged unsupported (rule: at least 60 and at most 15). Three model raters labelled the rows; on
  the 39 rows the scientist rated blind she agreed with them on 7 of 39, so the raters' labels are not confirmed by a human.
  A separate machine audit of 1,000 links gave 91.8% of pruned and 55.0% of kept links unsupported. Without any model:
  1.54% of pruned and 2.40% of kept protein-protein rows have an independent IntAct record.
- **The difference between pruned and kept links in those two checks is mostly the mix of sources.** Within
  protein-protein rows from STRING, pruned and kept rows are judged unsupported at similar rates; most of the overall
  difference comes from the kept sample holding many text-mined disease links. The raters find little evidence for any
  STRING row, so this check says little about protein-protein pruning; the IntAct comparison is the model-free check for
  those.
- **The independent check shown on a link is made by the same automated raters.** Each judgement in the app says so,
  with how often they agreed with the scientist. For the 200 links of the blinded check, the stored check shows the three
  raters' judgement; the final label of each row (the scientist's where she rated it or settled a disagreement) is in the
  spot-check result file (section 9).

## 5. Predicted links are hypotheses

The meta-path model (the one whose scores appear on the cards) proposes 112,187 gene-disease links that no source in the
graph states yet. That count was made on an earlier build of the pruned graph (29,270,260 edges; the shipped one has
29,290,179) and has not been redone on the shipped build.

- **A prediction is a suggestion to test, not a finding.** Each one is shown as "predicted, not yet reported", with the
  paths in the graph that produced it. None has been tested in a laboratory by this project.
- **What the score means.** On gene-disease links held back from the model, the same model ranks true links better on
  the enriched graph than on Pharos alone: +0.124 AUROC. AUROC measures how well a model orders links on a benchmark. It
  is not the probability that a prediction is correct, and it is not biological accuracy.
- **Where the gain comes from.** About three quarters of it comes from the disease hierarchy: a gene already linked to
  a broader or narrower form of a disease is predicted for the related form. Such predictions are sensible but often
  unsurprising. A plain graph neural network, without path counting, gained nothing from the added layers. Both results
  are reported in the README.
- **Only gene-disease links were tested.** Predictions of other kinds (for example compound-target) were not evaluated
  and should be read with more caution.
- **Well-studied genes and diseases are favoured.** Models of this kind find it easier to predict links for entities
  that already have many links. The benchmark corrects for disease popularity when it scores; the predictions you browse
  are not corrected.

## 6. Answers to questions

- A language model turns your question into a query on the graph. The query is checked against the graph before it runs,
  and the answer is the links that matched, each with its source. The model does not add facts of its own to the list of
  links.
- The model can still misread a question. The page shows any condition the query dropped: check that the answer is to
  the question you meant.
- A name the graph does not hold returns nothing. Try the official gene symbol or the disease name as a database
  writes it.
- The hosted site answers at most 20 questions per visitor per hour.
- Every answer comes first from this server's own copy of the graph; that is the default and nothing depends on
  anything else. On the hosted site the same question can also be run on Amazon Neptune ("Run this on Amazon Neptune"
  under an answer), where the whole graph is loaded: 1,326,108 nodes and 29,295,399 links, equal to the download, and the
  four fixed check questions return the same evidence there as in the download. Those links are the 29,290,179 links of
  the pruned graph plus the 5,220 links of the failed-trial layer. Anyone can ask the site to wake Amazon Neptune; the
  site sends at most six wake requests a day in all. Amazon Neptune is put to sleep when
  nobody has asked it anything for an hour, and every night, to save cost. Waking it takes about 11 minutes; the answer
  from this server's graph stays on screen meanwhile, and the question runs on Amazon Neptune by itself once it is
  awake. If Amazon Neptune is asleep, or does not answer within 20 seconds, the answer comes from this server's graph
  and the page says so.
- The two copies do not answer every question the same way. This server's copy is a search index built from the same
  sources; for some questions it holds more evidence than the download that Amazon Neptune holds, and it puts the
  best-supported rows first before it shortens a long list, so the two often return different rows. When both
  answer, the page prints how many evidence links are the same, as they are: for the ready PTGS2 question 23 of 215,
  for the SOD1 question 8 of 8. It says "identical" only when the two sets are equal. One ready question (the
  compounds that bind the PTGS2 protein) and one stored example go through the link from a gene to its protein, which
  the Amazon Neptune copy does not carry under that name, so Amazon Neptune returns nothing for them and the answer
  from this server's graph is the one to read.
- The short paragraph under a typed answer is written by a language model from the answer's rows, and each sentence is
  checked against the rows it cites. The check can still miss a misreading of a number or of the kind of link. The rows
  below the paragraph are the record.
- An answer with no rows can come from the shape of the query the model wrote, or from the time limit, and not only from
  the graph holding nothing. Rephrase, or open the card of the gene or disease, before reading it as "no link".
- The trial graph answers questions about named drugs and programs. A question about a whole drug class (for example
  "CETP inhibitors") finds no starting point, and the answer says it could not verify a claim.
- A trial report reads the curated statements about a program. It can say that the records show no efficacy or safety
  results while the trial's own registry page on this site holds them, and its redesign and responder sections can be
  thin. Open the trial to see the registry outcomes.
- When some outside sources do not answer, the independent check on a link judges from the sources that did and lists
  the ones that were missing; it then gives "cannot tell" rather than "unsupported", but a "supported" rests on less
  evidence than usual.

## 7. Known gaps in this version

Each of these was found in the review before submission and is larger than could be fixed safely in time.

- **A disease card lists at most 40 links of each kind**, ordered by the pruning confidence, which is not the strength of
  a genetic signal; a well-known risk gene can sit below the first 40. The question box orders its answers differently.
- **Genes for inherited (Mendelian) diseases from curated sources are filed under "curated"**, not under "human genetics".
- **The question box reads disease names by its own ranking**, so a specific name can be read as a broader term. The
  answer says which term it used.
- **Publications are counted on many links, but their PubMed ids are not carried**, and several sources have no link to
  their own record page.
- **Typed questions to the trial graph can fail.** The stored trial answers and the program cards are the reliable route.
- **A target card's list of failed trials includes trials that reach the target only through a shared disease.**
- **Prediction bands rank pairs.** The card does not show how often each band was right on held-out links, and paths
  through very common diseases are not flagged.
- **The headline gain comes from the path-aware model**, chosen after the frozen protocol showed no gain for the plain
  graph network; some controls were run on an earlier build.
- **Antibodies from ChEMBL are typed as small molecules.** In Amazon Neptune the relationship types are the sources'
  relation names (one of them, `0000050`, is the "part of" relation with its prefix lost); to query by BioLink names, use
  the property `biolink_predicate` of each relationship. Renaming the relationship types needs a new export.
- **The downloadable sample holds kept links only.**
- **A gene and its protein have separate cards.** Disease links sit on the gene card; protein interactions and compounds
  sit on the protein card, one step away through the link that says the gene encodes the protein.
- **A typed answer can show several unnamed compounds as one row** when a source lists them under one target record; the
  row then carries all their ChEMBL identifiers.
- **The held-out test links of the benchmark are not a download.** They are rebuilt by the code in the repository from the
  Pharos data, and each result file carries a digest of them, so a rerun can show it used the same links.
- **The per-run intervals in the site's results data read 0.0**, because each run used one random seed. The intervals
  quoted for the headline compare the two graphs on the same five splits of the held-out links.
- **No usability test with outside users was run.** The site was checked only by the scientist herself, in Chrome, Safari
  and on a phone.
- **One pruning reason shows the text "protected undefined"** in the detail panel and hover card of the PTGS2 -> thrombotic
  disease link. This is a display defect; it is not fixed.
- **The click-through check of the app exits as passed without writing results when its browser bridge cannot start.** It
  should fail in that case.
- **The package-install test keeps the source directory on the Python path in the programs it starts**, so it can pass
  against the source tree instead of the installed package.
- **The script that makes the public copy unpacks the `.claude` directory before it excludes it.** This is harmless on a
  normal Mac; it fails where the system refuses to write `.claude`.
- **The site's first screen puts two prune counts side by side without saying why they differ:** 256,639 Pharos links
  marked prune, and 390,500 fewer links in the pruned graph. The first counts Pharos links only; the second counts the
  links removed from every layer.
- **A verdict's reasons are shown as short coded text** (plausibility, number of sources, reliability, source score), and
  the confidence at which a link is marked prune is not printed next to the verdict.
- **A link from one curated source with nothing else behind it can be kept.** Example: PTGS2 to "NAD biosynthesis via
  nicotinamide riboside salvage", a single curated Gene Ontology annotation, is kept.
- **The evidence record repeats the statement** in its title and its body, and shows the predicate as a schema term
  (for example `tmkg:has_outcome_measure`) rather than in words.
- **Trial-layer classes and predicates are the project's own terms** under the prefix `tmkg:`, because BioLink has no
  exact term for them; the schema card ([docs/SCHEMA_CARD.md](SCHEMA_CARD.md)) gives the closest BioLink term for each.
- **Two notes under the tested questions name a reference file** (for the MONDO terms and for the ChEMBL synonyms)
  instead of the ontology or database.
- **The raw data of a stored answer gives the local snapshot file of each registry record** (field `raw_response`), not
  the public ClinicalTrials.gov address that the evidence view shows.
- **The first screen's link to how the headline number was tested uses method words** a reader outside machine learning
  may not know.
- **The plain graph network's change is quoted in two forms.** README: -0.004 AUROC (95% CI -0.007 to -0.002),
  recomputed from the result files by the current comparison code. Experiments log and white paper: -0.0045 (-0.0064 to
  -0.0026), as written on 4 Nov. Both say the enrichment gave that model nothing.

## 8. Licences and how to cite

- **The code** is under the MIT licence (`LICENSE`).
- **The data keep the licences of their sources.** Every link names its source, so the licence of any row can be traced.
  Rows from CTD and DisGeNET are for non-commercial use only; rows from ChEMBL are share-alike (CC BY-SA 3.0); the Human
  Phenotype Ontology, ClinicalTrials.gov and GTEx ask for specific attribution lines. The full table, with the page read
  for each source and what could not be confirmed, is [DATA_LICENSES.md](../DATA_LICENSES.md). Nothing there is legal
  advice. A commercial user must filter out the non-commercial rows or obtain the upstream licences.
- **To cite a fact you found here, cite its source**, which is named on the link (database, release, record, and the
  publication where the source gives one). The graph is a route to the evidence, not the evidence.
- **To cite the graph or the tool**, give the name TrialMindKG, the repository address, and the date or commit you used,
  because the graph is a dated snapshot. Cite Pharos as its own page asks: Kelleher, Sheils et al., Pharos 2023, Nucleic
  Acids Research, doi:10.1093/nar/gkac1033. The attribution lines for the other sources are in the README.

## 9. Where each number comes from

Paths are relative to the repository root.

| section | file |
|---|---|
| 2. data sources and versions | `README.md` (data attribution table), the `summary.md` of each layer under `outputs/`, `docs/SCHEMA_CARD.md` |
| 3. kinds of node | `docs/SCHEMA_CARD.md` |
| 3. text-mining share of gene-disease links | `outputs/pharos/prune_calibration.md` |
| 4. verdict counts and the Open Targets comparison | `outputs/pharos/prune_report.md`, `outputs/pharos/prune_calibration.json` (the 280 and the 2) |
| 4. the 200-link blinded check | `outputs/pharos/spotcheck_rule.md` (the rule), `outputs/pharos/spotcheck_blind_result.md` (the result, the 7 of 39) |
| 4. the machine audit of 1,000 links | `outputs/pharos/audit_sample_result.md` |
| 4. the IntAct comparison | `outputs/pharos/ppi_corroboration.md` |
| 5. number of predicted links | `outputs/linkpred/predictions_novelty.md` |
| 5. AUROC gain | `pipelines/headline.py`, from the files in `outputs/linkpred/` |
| 6. what is loaded in Amazon Neptune, the check questions, the evidence links in common, the wake time | `outputs/neptune/live_parity.json` (`loaded`, `manifest`, `pinned`, `answers`, `wake_minutes`) |
| 7. links removed from every layer (390,500) | `outputs/g3/summary.md` |
| 7. the plain graph network's change in two forms | `pipelines/headline.py` (README), `docs/EXPERIMENTS_LINKPRED.md` (experiments log) |
| checked by | `tests/test_limitations.py` |
