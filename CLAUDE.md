# CLAUDE.md - project briefing for Claude Code

> **START HERE (2026-11-04):** `docs/MASTER_PLAN.md` is the only live plan; read its STATUS block first. Claude Code:
> take packets CC-1..CC-8 from its section 9 (one branch `cc/<id>` each, fixtures and tests only, then a PR). Claude
> Science: load the skill `trialmindkg-continuity`. Where any older section below (START HERE 2026-11-02,
> EXECUTION_PLAN, WINNING_PLAN, BUILD_ROLES) disagrees, `docs/MASTER_PLAN.md` wins.

## What this project is
TrialMindKG: agents that build and query a clinical-trial knowledge graph which joins
PUBLIC evidence (ClinicalTrials.gov, ChEMBL, Open Targets, GTEx, openFDA FAERS, curated literature)
with a sponsor's INTERNAL trial data, and answer drug-development questions with provenance
on every statement. Built solo for the NVIDIA-AWS BioAgentKG Challenge (6 Oct - 17 Nov 2026; FINAL SUBMISSION 17 NOV; judging
17 Nov - 8 Dec; results 15 Dec; internal target: submit Nov 16, Nov 17 is the buffer).
The owner is a molecular biologist/pathologist, not a software engineer: explain changes in plain
language, keep modules small, and say what each function takes in and returns.

## Layout
- pipelines/seed_kg.py      - builds the KG from 34 failed trial programs (orchestrator; see docs/PIPELINE.md)
- pipelines/snapshot.py     - ALL API calls go through it; responses frozen in data/snapshots/ (repeatable builds)
- pipelines/kg_schema.py    - allowed node types, relationships, id patterns, provenance fields; validate() gate
- pipelines/export.py       - writes CSV, GraphML, KGX TSV (BioLink terms), Neptune bulk-load files, Neo4j loader, run_manifest.json
- pipelines/kg_query.py     - answer harness: fixed queries returning evidence edge ids; verify_answer(); golden set
- pipelines/aact.py           - trial registry source (default): AACT flat files/DB -> same record shape as the CT.gov API;
                              check-schema / compare / freeze run on the owner's Mac; results frozen in data/snapshots/aact
- pipelines/ctgov.py, opentargets.py, reactome.py, literature.py, mondo_ref.py, drug_names.py, chembl_synonyms.py
                            - one small connector per source
- pipelines/provenance.py     - BioLink knowledge source / level / agent type per edge (data/curated/provenance_map.csv)
- pipelines/nim.py, resolver.py, stop_reasons.py, retrieval.py, specialists.py, orchestrator.py, querier.py, evaluate.py
                            - NVIDIA NIM agents (offline stand-ins until NVIDIA_API_KEY is set)
- pipelines/curated_tables.py - parsers for the curated post-failure / repurposing / data-access tables
- pipelines/raw_docs.py     - builds raw/: per-program source documents for ground-truth review (index.csv, README.md)
- pipelines/app.py, app_page.html - web app (FastAPI): Intelligence Report, evidence cards, graph explorer, PDF/JSON
- pipelines/numbers_page.py  - Numbers page data: frozen benchmark files + prune_calibration.json only, each number with its file
- pipelines/schema_card.py   - docs/SCHEMA_CARD.md + outputs/schema_card.json: classes, predicates, IRIs, BioLink mappings, counts
- pipelines/maintain.py      - `diff`: a rebuilt layer's summary vs its last commit (docs/MAINTAIN.md)
- pipelines/subgraph_export.py - an answer's nodes and edges as KGX tables + PyG-ready tables (zip)
- pipelines/pharos_index.py  - build-time card index (data/kg/pharos_index/card_index.sqlite): app status/search/card without the edge table
- pipelines/pharos_app.py    - Pharos screen inside app.py ("Pharos graph" tab): search, explain edge, predicted links,
                              demo questions (pharos_db.py / pharos_page.html were retired 2026-10-30)
- pipelines/linkpred/       - R-GCN link prediction; eval_set.py = the fixed, leakage-filtered test set for all graphs
- raw_text/                 - text of the raw/ PDFs (committed; index.csv lists NEEDS_OCR scans)
- docs/GAP_ANALYSIS.md, docs/OWNER_CHECKLIST.md - application vs build status; owner's to-do list
- pipelines/pdf_text.py     - raw/ PDFs -> raw_text/*.txt.gz (+ index.csv; scanned PDFs flagged NEEDS_OCR); run on the Mac
- pipelines/doc_discovery.py - finds documents/data per program (Europe PMC, accessions, Drugs@FDA, FAERS, registries)
- pipelines/make_catalogue.py - raw/CATALOGUE.xlsx: checklist of what exists / is missing per program
- data/curated/web_candidates.csv - press releases, presentations, regulator docs found by web search (verify)
- pipelines/make_ground_truth_template.py - writes data/curated/ground_truth_template.xlsx (owner fills it in)
- pipelines/adc_demo.py     - ADC (TUB-040, NaPi2b) logic: synthetic internal data, analysis, graph, demo answer
- pipelines/run_adc_demo.py - runs the ADC demo end to end (snapshot in data/snapshots_adc_demo/, graph in outputs/adc/)
- data/curated/             - curated inputs; curated_overrides.csv (trials), condition_overrides.csv (diseases),
                              golden_questions.csv (answers that must keep passing)
- data/snapshots/           - frozen raw API responses (committed; the data version of the graph)
- data/reference/           - small tables extracted from MONDO (committed; regenerate with mondo_ref.py)
- raw/                      - review copies of registry records, abstracts, open-access full texts, curated sources
                              (PDFs git-ignored; run raw_docs.py on the owner's Mac to fetch blocked sites)
- data/snapshots_raw/       - frozen Europe PMC abstract/full-text responses used by raw_docs.py
- data/synthetic/           - SYNTHETIC internal tables (CDISC-style). Not real sponsor data.
- data/external/            - downloaded public reference data (BioLink, MONDO, ChEMBL, GTEx); git-ignored except README + DATA_MANIFEST.tsv
- scripts/download_external_data.sh - fills data/external/
- data/internal/            - real confidential data; git-ignored; never read it into outputs that get committed
- outputs/                  - generated files; outputs/neptune/ = Neptune bulk-load CSVs; resolution_report.csv = review queue
- docs/                     - pitch, getting-started guide, PIPELINE.md (determinism, Neptune, harness), figures
- tests/                    - pytest

## Commands
- Set up:     python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
- Seed KG:    python pipelines/seed_kg.py        (TMKG_SNAPSHOT_MODE=offline|refresh, TMKG_TRIAL_SOURCE=aact|ctgov)
- AACT (Mac): python pipelines/aact.py check-schema && python pipelines/aact.py freeze   (AACT_FLATFILES in .env)
- Review:     python pipelines/review_findings.py make|apply   (owner accepts Nemotron findings in data/curated/review_findings.xlsx)
- Monthly:    python pipelines/monitor.py check -> aact.py freeze -> seed_kg.py -> python pipelines/monitor.py diff
- Query:      python pipelines/kg_query.py program_trials pid=P19
- ADC demo:   python pipelines/run_adc_demo.py   (needs gtexportal.org and api.fda.gov reachable)
- MONDO refs: python pipelines/mondo_ref.py      (after scripts/download_external_data.sh)
- Ref data:   bash scripts/download_external_data.sh   (SKIP_CHEMBL=1 to skip the multi-GB ChEMBL file)
- Raw docs:   python pipelines/raw_docs.py [--no-large]   (also rebuilds raw/CATALOGUE.xlsx)
- PDF text:   python pipelines/pdf_text.py   (Mac, after raw_docs.py; commit raw_text/)
- GT sheet:   python pipelines/make_ground_truth_template.py
- Evaluate:   python pipelines/evaluate.py   (NIM if NVIDIA_API_KEY set; writes outputs/eval/summary_<backend>.md)
- App:        python pipelines/app.py   (http://localhost:8000)
- Tests:      pytest -q   (test_reproducible.py rebuilds offline and must match committed outputs byte for byte)

## Rules (non-negotiable)
1. NEVER invent identifiers (NCT, ChEMBL, UniProt, MONDO, MedDRA) or numbers. Every value in the graph
   must come from an API response, a curated file in data/curated/, or a computation on data.
2. Every edge carries provenance: source, source_record, evidence_type or layer, retrieved date.
3. When matching is uncertain, flag it for human review (curated_overrides.csv / NEEDS_REVIEW) - do not guess.
4. Synthetic data must stay labelled SYNTHETIC in files, graph nodes and any text that reports it.
5. Never commit .env, API keys, or anything in data/internal/.
6. Use the `biolink:` prefix only for real BioLink Model classes/predicates; otherwise use `tmkg:`.
7. Generated text must be computed from the numbers (conditional wording), not prewritten conclusions.
8. Add or update a test in tests/ for every new function; run `pytest -q` before saying a task is done.
9. Every HTTP call goes through snapshot.Snapshot; never call requests directly. New node types or
   relationships are added to kg_schema.py first. After changing pipeline code, rebuild and commit
   outputs/ + data/snapshots/ together so test_reproducible.py stays green.
10. Agents answer only via kg_query.py functions, cite edge ids, and pass verify_answer().

## UPDATE 2026-11-04 (read before START HERE; supersedes it where they differ)
- **Deadline: final submission Nov 17** (confirmed by the owner 2026-11-04; the old "15 Dec" was the results date).
  Feature freeze Nov 12 18:00; link-prediction freeze Nov 6 18:00; submit Nov 16.
- **Run A is finished and committed (5736b0e9).** Frozen benchmark, same test digest 24f5864cc07ad723; G0 ran at the 2M
  edge cap, G2 and G3 at 1M (every report must say so): GNN AUROC G0 0.5864, G2 0.5821, G3 0.5820 (G2-G0 -0.0043,
  95% CI -0.0065 to -0.0021: enrichment does not help the R-GCN); meta-path model G0 0.6859, G2 0.7677, G3 0.7670
  (G2-G0 +0.0818, CI +0.0764 to +0.0871, 5/5 folds). The meta-path gain is NOT yet a claim: path coverage differs
  (87.5% vs 99.3% of test pairs), common-neighbour is 0.50 on G0 vs 0.65 on G2 (Open Targets may reuse sources the
  test set contains), and the caps differ. Controls to run, labelled exploratory (the primary protocol stays
  frozen): coverage-matched AUROC, shuffled-layer control, source-drop, time split using the OT release history.
- **Organizer Q&A (Nov 4, docs from the organizers):** usability and democratization decide who wins; show that the
  tool generalizes beyond Pharos; hosted demo ideal (video only in a pinch); UniProt IDs are fine for the gold set, the
  NCATS comparison uses HGNC / MONDO / Node Normalizer preferred IDs; Pharos itself is built on the Drug Target
  Ontology with HPO and GO on top; KGX TSV + PyG export accepted; send the pruned graph AND every Pharos edge with
  keep/prune + confidence; build.nvidia.com counts for NVIDIA; AWS credits cover all console services; set billing
  alarms first; Neptune has no idle shutdown (CloudWatch alarm + Lambda, or EventBridge schedule). The gold-set
  details (release, edge types, labels, metric, injected errors, held-out link type) were deferred to NCATS in
  margin comments and are STILL UNKNOWN: output a continuous confidence plus verdicts at several thresholds.
- **Known gaps found 2026-11-04 (checked in the code):** (1) pipelines/claims.py support_status() returns "supported"
  for every claim that is not "contradicted", including judge results "unjudged" / "not_addressed"; (2) pipelines/nim.py
  check_schema() validates objects, arrays and strings only, so a wrong boolean or number passes; (3)
  pipelines/evaluate.py judges all systems against the facts TrialMindKG itself retrieved; (4) numbers_page.PROTOCOL_TEXT
  says every edge type is capped at 2M, which is wrong for G2/G3 in run A; (5) linkpred/predict.py exports GNN
  predictions only, no meta-path model; (6) the Pharos Drug Target Ontology table (data/pharos/dto.tsv.gz, 54,242 rows)
  is downloaded but used by no source card; (7) the card index (data/kg/pharos_index) is not built and the Pharos
  card has never run on the real graph; (8) AWS (aws/) has never been run and no hosted demo exists.
- **Who works on this now:** Claude Code credits ran out in early Nov. Work is handed over as task packets that any
  agent can run (Claude Code, Codex, Claude Science); git main stays the only channel. A Codex copy of the repo is in
  ~/projects/codex (branch codex/takeover, uncommitted) with a development controller (harness/dev_controller.py,
  bin/dev-controller, plan -> implement -> validate -> judge -> bounded rework, 68 tests); it is to be merged here and
  its Codex-only isolation rules dropped. Its unattended CLI path was never verified.
- **Build plan (approved by the owner 2026-11-04; now superseded by docs/MASTER_PLAN.md, original in docs/archive/PLAN_2026-11-04.md):** The loop is PLAN -> DESIGN -> RUN -> JUDGE ->
  ITERATE -> IMPROVE -> COMMIT with a scorecard on the four judging criteria (loop/README.md; `python3 loop/loop.py
  score|next|status`; ledger loop/ledger.tsv; task packets loop/tasks/). Nothing in it is done until NEXT_STEPS or the ledger say so.
- **Review / handoff snapshot:** docs/archive/HANDOFF_REVIEW_2026-11-04.md and docs/reviews/2026-11-04/, now in git history only (commit ab098037, removed from the public tree) (self-contained state, verified numbers, open items,
  sandbox pitfalls, questions for a reviewer). Read it first in a fresh session.

## START HERE (updated 2026-11-04; the one live plan is docs/MASTER_PLAN.md)
- Live docs: README.md, this file, docs/MASTER_PLAN.md (plan, working rules, work packets, keep/fix/archive list),
  docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md (the FROZEN link-prediction protocol, section 3), docs/SCHEMA_CARD.md,
  docs/PIPELINE.md, docs/BRING_YOUR_OWN_SOURCE.md, docs/organizers/QUESTIONS_FROM_TEAMS.md. Operational: loop/ (scorecard,
  ledger, task packets), docs/HANDOFF.md (machine-written), docs/MAINTAIN.md, docs/sessions/. Everything superseded
  (old plans, status log, project memory, setup guides) is in docs/archive/; read it only for history. Review behind
  the plan: docs/reviews/2026-11-04/ (in git history only (commit ab098037, removed from the public tree)).
- Who works on what: Claude Science on the owner's Mac is the only one that runs jobs; Claude Code works on branches
  `cc/<packet>` (MASTER_PLAN section 9) and opens a PR; Science merges after the packet's acceptance check on real data.
  Claude Code may add files under pipelines/linkpred/ and change predict.py / compare.py, but never the frozen logic in
  run.py, splits.py, metapaths.py or evaluate.py.
- Prize: open-source track, USD 5,000 in AWS credits. The repository must be public at submission. Judging: Usability 40%,
  Accuracy & Pruning 30%, Explainability 20%, Standards 10%. UI/persona plan: docs/USABILITY_PLAN.md.
- Branches: `main` is the only long-lived branch. Session start: `git fetch origin main`; if the session branch is
  behind, `git checkout -B <session branch> origin/main`. Session end (tests green): push the branch; Science merges to
  main. Merged session branches are deleted (`git push origin --delete <branch>`).
- Use `bin/agent-cli` first (AGENTS.md): `bin/agent-cli start` shows branch vs main, open steps and the last log entry;
  prefer its subcommands (`next`, `defs`, `fn`, `grep`, `test --for`, `commit`, `sync`, `mac`) over re-reading files.
- Continuity: `.claude/continuity.yaml` is the index (memory files, skills, agents, hooks, connectors, start and end
  sequences). Session start = `bin/agent-cli start` + `bin/agent-cli handoff`. Session end = tests green, NEXT_STEPS and
  loop/ledger.tsv updated and committed, then `bin/agent-cli end -m "..." --session <url> --run` (`--digest` adds a
  docs/sessions/ digest, opt-in because it quotes the owner's messages into a public repo).
- FIRST, every session and every task: read docs/MASTER_PLAN.md and docs/NEXT_STEPS.md (live items only).
- The official brief makes PHAROS the core (gold set scores pruning of Pharos edges); the trial graph is the reusable
  machinery and differentiator demo.

## Current state (2026-11-02) - the ordered list is docs/NEXT_STEPS.md, the old session log docs/archive/STATUS_CHECKLIST.md
- Pharos core (the brief's centre): Pharos 3.19.2 / TCRD 6.13.5 pulled as delivered (pipelines/pharos.py), built by the
  generic source-card builder (sources/*.yaml -> pipelines/kgbuilder/) into data/kg/pharos (26,462,915 edges on the
  Mac with PPI and TIN-X; outputs/pharos/summary.md and prune_report.md were regenerated from that layer on 8 Nov, CS-2). Layers: Open
  Targets 26.09, ChEMBL 37, GO / HPO / MONDO hierarchies; graphs G0-G4 by make_graphs.py.
- Pruning v1: verdict, confidence, reasons on every edge (prune_kg.py); gold-set verdict file with native Pharos ids
  (verdict_file.py); calibration vs Open Targets (outputs/pharos/prune_calibration.md: lowest confidence tenth 4.9%
  corroborated at OT >= 0.2, highest 49.7%). v1.1 + spot check pending on the Mac (NEXT_STEPS 7g, 8, 17).
- Link prediction: protocol FROZEN (docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md section 3); run A DONE (see UPDATE
  2026-11-04 above), run B (after the OT rebuild) Nov 4-5, freeze Nov 6 18:00. Only Claude Science runs it; Claude
  Code may add files there and change predict.py / compare.py, never the frozen run.py, splits.py, metapaths.py, evaluate.py.
- App (pipelines/app.py): personas, Pharos card (served from the card index pharos_index.py when built), "why"
  drawer with drawn timeline, tested demo questions, subgraph download (KGX + PyG), Numbers page on the frozen
  result files only (numbers_page.py), schema card (schema_card.py -> docs/SCHEMA_CARD.md).
- Maintain: docs/MAINTAIN.md (re-pull, rebuild, `pipelines/maintain.py diff`, re-index).
- Trial layer (the multi-hop differentiator): 34 failed programs, 3220 nodes, 5220 edges, frozen snapshots,
  provenance + mapping_confidence on every edge, NIM agents (offline stand-ins without NVIDIA_API_KEY); work order
  P1-P12 done in October (details: docs/GAP_ANALYSIS.md, docs/archive/PROJECT_MEMORY.md). NIM evaluation 2026-10-06:
  outputs/eval/summary_nim.md.
- Known gap: demo_query() text says "in this demo: SYNTHETIC"; make it depend on the data source when real data are used
- How we work (Mac steps, commands, pitfalls, review workflow): .claude/skills/trialmindkg/SKILL.md
