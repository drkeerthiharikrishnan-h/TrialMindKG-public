# The agent loop, step by step

The organisers' brief ([docs/organizers/BRIEF.md](organizers/BRIEF.md)) asks whether an agent, or a swarm of agents, can
run one loop: **Ingest, Filter, Clean, Limit, Maintain**. This page says, for each step, which module in this
repository does it, the command that runs it, and the test that exercises it. The app shows the same table under
**For judges**; both are read from the block below, so they cannot drift apart (`tests/test_agent_loop.py` checks that
every file and function named here exists).

## What "agent" means here

<!-- agent:start -->
TrialMindKG is a semi-autonomous agent. It runs the loop Ingest, Filter, Clean, Limit, Maintain: it drafts the source card for a new table and builds from it once a scientist approves; a language model turns each question into a validated graph query and summarises the evidence behind each answer; a pruner scores every edge and an auditing step checks edges against literature and interaction databases; a scheduled check watches the sources; every decision is in a dated log.
<!-- agent:end -->

The same paragraph is in the README and on the judge's card; `python pipelines/headline.py --readme` writes all three, and
a clause is switched to its fuller wording only when the code behind it is merged (`AGENT_CLAUSES` in
`pipelines/headline.py`).

"Semi-autonomous" is meant literally. There are two kinds of agent in this project, and a scientist between them:

- **While the graph was built:** coding agents wrote the source cards and the programs, ran the loop, and wrote every
  hypothesis, rule and result into a dated ledger (`loop/ledger.tsv`) before and after each run. The scientist decided
  the open questions the ledger records as decisions (which classes, which mappings to exclude, which controls to run).
- **When a question is asked:** a language model turns the question into one graph query, a validator checks the query
  against the graph, and the answer is the edges that matched. No person is in that loop.

## What is autonomous and what is not

- **A language model is used in one step: Filter.** A plain-language question is turned into a graph query by a
  NVIDIA NIM model (or, with no `NVIDIA_API_KEY`, an offline stand-in the app labels as such). A validator rejects a
  query that names a class, relationship type or property that is not in the graph, or that compares a value with a
  property whose values the graph enumerates (node properties with few values; the relationship properties source,
  primary_knowledge_source, knowledge_level, verdict and layer) when the graph holds no such value; the model is asked
  again with the error. Other values (free-text names, numbers) are not checked: a name the graph lacks matches nothing.
  A question that names nothing the graph recognises is not sent to the model; a condition an earlier attempt had and the
  query that ran lacks is shown with the answer.
  The trial layer also uses a model to judge whether a drafted statement is contradicted by the retrieved facts.
- **Ingest, Clean, Limit and Maintain are fixed programs**, not language models. They are driven by small declared
  inputs (a source card, a pruning setting file) and write files, which is why a build repeats byte for byte. The agent's
  part in these steps was writing the cards and the programs and running them; the scientist reviewed the cards.
- **One part runs by itself: the weekly source check.** Every Monday, and when started by hand, a scheduled job
  asks each source for its
  current release identifier (Pharos, Open Targets, ChEMBL, MONDO, the Human Phenotype Ontology, the Gene Ontology) and
  compares it with the manifests and source cards in this repository. It makes one small request per source (a release
  page, a release feed or the source's own version query), downloads no data, rebuilds nothing and changes no file. It
  writes a dated report (what changed upstream, which layers would be rebuilt) and opens an issue when a source has a
  newer release. It uses no secret; a source that needs a login (the AACT trial registry export) is skipped and the
  report says so.
- **A scientist approves everything that changes the graph.** Taking a new release, the re-pull and rebuild (run on the
  computer that holds the data), reading the diff before going on, the pruning settings that ship, every pass rule
  fixed before a result is seen, and the merge into the main line of the repository. These decisions are rows of the
  experiment log; the app lists them, marked "Scientist decision", in its dated log of what the agent did.

## What the agent did, with dates

<!-- runlog:start -->
Each row is an entry of the dated ledger `loop/ledger.tsv` (written at the time, including the rejected hypotheses); the numbers are recomputed from the result files by `pipelines/headline.py`.

| date (2026) | what the agent did | rule or decision on record | ledger row |
|---|---|---|---|
| 4 Nov | Ran the frozen benchmark. With the enrichment the plain graph network scored lower (-0.004 AUROC, 95% CI -0.007 to -0.002). | Hypothesis recorded as rejected; the protocol stayed as frozen. | `E001` |
| 6 Nov | Ran the path-aware model that was registered before the run: +0.132 AUROC, 95% CI +0.126 to +0.137 over Pharos alone on that day's build. | Passed its rule, set before the run; the control without the disease hierarchy was queued before any claim. | `E008` |
| 6 Nov | Ran the random-pruning control: the pruned graph against one with the same number of edges removed at random (+0.007 AUROC, 95% CI +0.002 to +0.012). | Registered and approved by the scientist before the graph or any run existed. | `E009` |
| 7 Nov | Finished the control: without the MONDO disease hierarchy the enriched graph scored higher than Pharos alone (+0.033 AUROC, 95% CI +0.029 to +0.037). | Reported next to the result ever since. | `E008 control` |
| 7 Nov | Reported that layers gave the same identifier different classes. | The scientist chose one class per identifier prefix in every product. | `DECISION-OT-classes` |
| 7 Nov | Listed disease mappings whose cross-reference looked wrong, for review. | The scientist excluded five of them; those rows keep their original identifier. | `DECISION-MeSH-exclusions` |
| 9 Nov | Rebuilt the graph with the protein-class layer, scored every edge again, rebuilt the card index and the download files. | Recorded as done; this is the build the downloads hold. | `CS-9 part 1` |
| 10 Nov | Ran the same model once on the shipped graph. Enriched: +0.124 AUROC, 95% CI +0.117 to +0.130. Pruned: +0.122 AUROC, 95% CI +0.114 to +0.130. | Run once, no tuning; this is the result that is quoted. | `D (CS-9 graphs)` |
| 11 Nov | Drew the blinded 200-edge spot check again, from the shipped verdicts. | Amendment written before any row was rated. | `CS-4-spotcheck-amend1` |
<!-- runlog:end -->

## The loop

```yaml
loop:
  - step: Ingest
    brief: Take knowledge concepts from a variety of heterogeneous sources.
    plain: >-
      A source card (a short YAML file) says how one table becomes graph nodes and edges. The builder checks the card
      against the BioLink model before it reads a row, gives every edge its source and release, and counts the rows it
      could not map instead of dropping them silently.
    module: pipelines/kgbuilder/engine.py
    functions: [load_card, check_card, build]
    command: python3 pipelines/build_kg.py sources/pharos.yaml
    tests: [tests/test_kgbuilder.py, tests/test_byo_source.py]
    exercises: [engine]
    also: A new source is one new card; docs/BRING_YOUR_OWN_SOURCE.md walks through it with a labelled SYNTHETIC example.
  - step: Filter
    brief: Retrieve only relevant subsets of graphs (relevance filtering).
    plain: >-
      A question in plain language becomes one graph query. The query is validated against the graph's own classes,
      relationship types, properties and enumerated values, run, and the answer is the edges that matched, each cited by
      id, strongest evidence first. The edges of one answer can be
      downloaded as a small graph (KGX tables plus PyG tables).
    module: pipelines/querier.py
    functions: [Querier, validate, run_local]
    command: python3 pipelines/app.py   # then type a question in the search box
    tests: [tests/test_querier.py, tests/test_subgraph_export.py]
    exercises: [querier, subgraph_export]
    also: The same validated query also runs on Amazon Neptune, which holds the whole graph (see Numbers).
    dev: "Amazon Neptune path: pipelines/neptune_backend.py (NeptuneRunner), pipelines/routes_neptune.py (ask_on_neptune)"
  - step: Clean
    brief: Prune erroneous knowledge (hallucinations or contextually incorrect links).
    plain: >-
      Every edge gets keep or prune, a confidence between 0 and 1, and the reasons. The score uses how the source
      asserted the link, the source's own score, whether other sources agree, how well the edge fits its neighbourhood,
      and hard flags such as an implausible activity value. Pruned edges stay in the graph, marked. The confidence is
      then checked against Open Targets, which the pruner never sees.
    module: pipelines/prune_kg.py
    functions: [main]
    command: python3 pipelines/prune_kg.py sources/pharos.yaml
    tests: [tests/test_prune.py, tests/test_prune_calibration.py]
    exercises: [prune, prune_calibration]
    also: pipelines/kgbuilder/prune.py holds the scoring; pipelines/contradictions.py flags registry-versus-text conflicts in the trial layer.
  - step: Limit
    brief: Manage the size of the queryable graph for performance.
    plain: >-
      The brief calls this probably unnecessary. We keep the whole Pharos graph (about 26 million edges) out of the
      app's memory: a build-time card index serves search and entity cards from a small database, the pruned graph
      leaves out pruned edges, and each answer exports only its own sub-graph.
    module: pipelines/pharos_index.py
    functions: [build_index, CardIndex]
    command: python3 pipelines/pharos_index.py
    tests: [tests/test_pharos_index.py]
    exercises: [pharos_index]
    also: pipelines/export_graph_kgx.py writes the pruned graph (pruned edges dropped unless asked for).
  - step: Maintain
    brief: Perform auto-updating as new literature/data becomes available.
    plain: >-
      A weekly check runs by itself: it compares each source's current release with what this repository pulled,
      writes a dated report naming the layers that would be rebuilt, and opens an issue when a source has moved on.
      It downloads and rebuilds nothing. A scientist then approves the rest: re-pull the source, rebuild the layer,
      and read the diff against the last committed build, which lists what changed. For the trial layer the change
      monitor also names the benchmark questions whose programs changed, so they are re-run.
    module: pipelines/maintain.py
    functions: [source_check, source_check_markdown, summary_diff, diff_markdown]
    command: python3 pipelines/maintain.py diff
    tests: [tests/test_source_check.py, tests/test_agent_log.py, tests/test_schema_card.py, tests/test_monitor.py]
    exercises: [source_check, agent_log, maintain, monitor]
    also: >-
      python3 pipelines/maintain.py source-check is the weekly check (.github/workflows/source_check.yml);
      python3 pipelines/monitor.py check and diff do the same for the trial layer; docs/MAINTAIN.md is the full
      procedure; GET /api/agent-log (pipelines/routes_agent.py) is the dated log of what was done.
```

## Maintain: what runs by itself and what a scientist approves

The weekly check is the job in `.github/workflows/source_check.yml`; it runs `python3 pipelines/maintain.py source-check`.

| | what happens | who starts it | what it may change |
|---|---|---|---|
| 1 | Source check: each source's current release against the repository's manifests; dated report; an issue when a source has a newer release | **by itself**, every Monday 05:23 UTC (or by hand from the Actions tab) | nothing in the repository; it opens an issue |
| 2 | Decide whether to take the new release | **a scientist** (reads the report, answers on the issue) | nothing |
| 3 | Re-pull the source as delivered; update the manifest and the source card's `version:` line | **a scientist** starts the command on the computer that holds the data | frozen source files, the manifest, the card |
| 4 | Rebuild the layer, score every edge (keep / prune) | **a scientist** starts it; the programs are deterministic | the layer, its build summary, the verdicts |
| 5 | Diff against the last committed build (`python3 pipelines/maintain.py diff`) | **a scientist** starts it and reads it before going on (a source that lost 30% of its edges is usually a pull problem, not biology) | `outputs/maintain/diff_<layer>.md` and, when every layer is compared, `outputs/maintain_diff.md` |
| 6 | Re-assemble the graphs, rebuild the card index, commit the record | **a scientist** approves the merge into `main` | graphs, index, committed summaries |

The check reads these records and addresses (`WATCHED` in `pipelines/maintain.py`); each row is one small request:

| source | what the repository holds | read from | how the current release is read |
|---|---|---|---|
| Pharos | the release in the pull manifest | `data/reference/pharos_manifest.tsv` | the Pharos API's own database version |
| Open Targets | the release at the start of the card's `version:` line | `sources/opentargets_layer.yaml` | the release folder listing at EMBL-EBI |
| ChEMBL | the release at the start of the card's `version:` line | `sources/chembl37.yaml` | the release folder listing at EMBL-EBI |
| MONDO, Human Phenotype Ontology | the last download date of the ontology file | `data/external/DATA_MANIFEST.tsv` | the project's release feed: newest release and its date |
| Gene Ontology | the last download date of the ontology file | `data/external/DATA_MANIFEST.tsv` | the release-date file of the current release |
| AACT trial registry export | not checked: needs a login | | the monthly routine in `pipelines/monitor.py` |
| HGNC gene names | not checked: no release identifier can be read without downloading the file | | |
| Reactome, BioLink model | not checked: the download address always gives the current file and the download record holds a date only | | |
| GTEx, OnSIDES | not checked: the download address is pinned to one release (GTEx version 8, OnSIDES v3.1.0) | | |
| OptimusKG | not checked: one deposited dataset fetched by its DOI | | |

Every source of the download manifest is either checked or listed as not checked with its reason (`NOT_CHECKED`); a
source added to the manifest later without a rule is reported as not checked too.
Whether the weekly run opens an issue is decided by `issue_decision()` in `pipelines/maintain.py`: only when a source
has a newer release and no open issue already has the same title.

For the three ontologies the repository records a download date, not a release name, so "changed" means the source
released after our download. A source that does not answer is reported as "not checked" with the reason; the check
never fails on one source. Which layers would be rebuilt is computed from the source cards: a layer is named when its
card reads from that source's data folder (for MONDO also the identifier-mapping tables made from it).

## The dated log: what the agent did, and what the scientist decided

`GET /api/agent-log` (`pipelines/routes_agent.py`) returns dated entries in plain words, newest first. Every entry is
computed from a committed record and cites it by title; no entry is written by hand and no number is typed in. The text
holds no file path, run tag, graph code or packet id (`internal_terms()` is the check; `tests/test_agent_log.py` runs it
on the whole answer), which is why a cite is a record title. The file behind each title:

| record cited | file | what the log takes from it |
|---|---|---|
| Pull record of Pharos | `data/reference/pharos_manifest.tsv` | what was pulled, when, how many rows |
| Download record of reference files | `data/external/DATA_MANIFEST.tsv` | which reference files were downloaded, when |
| Build record of a layer | `outputs/<layer>/summary.md` | entities and links built; source rows kept, merged, and left out with the reason |
| Record of an assembled graph | `outputs/g<n>/summary.md` | layers put together; links left out by pruning |
| Pruning report of the Pharos layer | `outputs/pharos/prune_report.md` | links kept and marked prune, by kind of evidence and by source; the check against Open Targets |
| Change record of a layer | `outputs/maintain_diff.md`, else `outputs/maintain/diff_<layer>.md` | what changed since the build before |
| Experiment log | `loop/ledger.tsv` | the dates; what the agent recorded each day, by kind; the scientist's decisions |

Only the Pharos change record exists today (`outputs/maintain/diff_pharos.md`). The combined record
`outputs/maintain_diff.md` is written when every layer is compared in one run; that has not been done yet, so the file is
not in the repository. The log reads the build records of layers that have a source card (`sources/*.yaml`) and of the
assembled graphs only; another `outputs/<folder>/summary.md` left on a machine gives no entry.

Each entry says who did it: `program` (a deterministic step a person or the weekly check started), `agent` (entries the
coding agent wrote into the experiment log while it ran the loop) or `scientist` (marked "Scientist decision"; a row of the
experiment log that names the scientist as its judge or is headed as her decision). A build record carries no date, so an
entry is dated by the first experiment-log row that states the build's link count, else the last row that names the
record, else the download date in the record; the entry says which. The hosted image carries the experiment log
(`loop/ledger.tsv`), the download record (`data/external/DATA_MANIFEST.tsv`) and the records of the assembled graphs
(`outputs/g0/summary.md`, `outputs/g2/summary.md`, `outputs/g3/summary.md`), so the hosted log has the same entries and the
same scientist's decisions as a full copy of the repository at the same commit (`tests/test_image_files.py` checks this on
the file set the image is built from). On a copy without the experiment log, the log shows the builds, the pruning and
the change record without the scientist's decisions and says so in `missing`.
