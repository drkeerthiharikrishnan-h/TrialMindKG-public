# Usability plan: the app for a physician-scientist, a clinician, a bench scientist and a judge (2026-10-31)

Written at the owner's request after looking at the running app (screenshots in docs/ui_before/, taken with
scripts/ui_screenshots.js on the committed trial graph; the Pharos tab had no data in the cloud). The owner's
verdict, "too bland", is right. This document says what the brief scores, how the current code base measures up
against each criterion, who the users are, and what gets built, in what order, before the code freeze on Nov 13.

Prize: open-source track, **USD 5,000 in AWS credits** (the only prize; the closed-source track gives recognition).
The repository must be public at submission (NEXT_STEPS step 23).

## 1. What the brief scores, and where the code base stands

| Criterion | Weight | What exists | Honest grade today | What moves the grade |
|---|---|---|---|---|
| Usability: a non-KG expert can use it, the utility is obvious, multi-hop answers | 40% | One-command app (`python3 pipelines/app.py`), question box over the trial graph with cited claims, evidence cards, graph explorer, Pharos tab (search, ask, explain edge, predicted links, 5 demo questions), PDF/JSON/subgraph export, dark mode, no build step | 5/10. Works, but looks like a developer tool: empty landing page; a status line of internals as the first text ("schema-valid 1 · provenance complete 1"); the scored core (Pharos) is the fourth tab and a blank search box; no guidance on what to ask; ids and jargon in the reader's face ("openCypher", "edge ids", "MONDO:0004975"); answers are text walls with tag chips; the headline of the challenge (new links predicted, beating plain Pharos) is three clicks deep | A "Start here" page by persona; one entity card per target / disease / compound that shows the multi-hop story on one screen; plain words with ids behind names; the predicted links and the G0-vs-G3 numbers on the front; a static replay of the demo for judges who cannot load 16M edges |
| Accuracy and pruning | 30% | Pruning v1 verdict + confidence + reasons for every Pharos edge (numbers: outputs/pharos/prune_report.md); shared-pairs link-prediction harness; smoke runs only | 6/10 until the full G0-G3 runs and pruning v2 land (NEXT_STEPS 14-18) | Not a UI matter; the UI must SHOW the verdicts (kept / pruned / why) on every row |
| Explainability: lineage of any edge | 20% | Provenance on every edge, explain-edge endpoint with reasons, corroborating layers and a dated timeline, two-hop paths behind every predicted link | 8/10. Strong in the data, weak on the screen: the explanation is a JSON-shaped list | A readable "why this edge" panel with the timeline drawn, reachable from every row |
| Standards: ontology IRIs, specific classes, few classes | 10% | BioLink-only Pharos core, SO/PR ids on genes and proteins, MONDO/GO/HP/CHEMBL/UniProt ids, KGX export | 7/10 | Schema card page in the app (classes, predicates, IRIs, counts) so the judge sees it without opening files |

Summary: the data and the provenance are better than the screen that shows them. The 40% criterion is the one
where a week of focused work changes the score most, and the cloud can do nearly all of it while the Mac and the
GPU run the numbers (NEXT_STEPS 12-14).

## 2. Who uses it (personas) and what each wants on the first screen

Each persona is a button on the landing page. Choosing one sets the starting tab, the example questions, and the
wording level. Nothing else in the code changes per persona: the same graph, the same endpoints.

| Persona | Starts from | Wants first | Multi-hop story the card must show | Wording |
|---|---|---|---|---|
| Physician-scientist (the owner's own role) | a disease or a failed trial | which targets are credibly linked to the disease and by what kind of evidence (human genetics, curated, clinical, text mining only); what the graph predicts next; which trials failed and why | disease -> gene -> protein -> compound -> trial | clinical + mechanistic; ids on hover |
| Clinician / physician | a disease or a drug | plain-language answers, "known or predicted", source names they recognise (DisGeNET, ClinicalTrials.gov, FDA, ChEMBL) | drug -> target -> disease -> trial outcome | plain; no ids, no predicate names |
| Bench scientist (molecular biologist) | a gene or protein | target development level, ligands with potency, pathways, interactors, expression; which disease links are worth testing | gene -> protein -> compound / pathway / disease | mechanistic; ids visible |
| Drug-development / translational lead | a compound or program | mechanism, targets, failed trials and reasons, predicted repurposing, redesign options (the trial layer's Intelligence Report) | compound -> target -> disease -> trial -> post-failure events | decision-oriented |
| Computational biologist / judge | the numbers | G0 vs G3 AUROC with confidence intervals, pruning statistics, verdict file, schema card, KGX + PyG subgraph download | the graph as data | technical |

The owner may rename or merge personas; the list above is the proposal. "Other relevant persona" candidates
considered and left out for the deadline: regulator, patient advocate, payer (no data in the graph for them).

## 3. What gets built, in order (cloud work unless marked)

All of it lives in the two files that exist today, `pipelines/app_page.html` and `pipelines/app.py`
(plus `pharos_app.py` for new read-only functions), tested with the fixture graphs in `tests/test_app.py` and
`tests/test_pharos_app.py`, and screenshotted with `scripts/ui_screenshots.js` into docs/ui_after/ so the owner
sees the change without running anything.

### P1, must have: BUILT 2026-10-31 (one cloud session; screenshots in docs/ui_after/; Mac check = NEXT_STEPS step 11)
Reused from the owner's FACET repository (github.com/drkeerthiharikrishnan-h/FACET): the persona-as-YAML-file pattern
(personas/*.yaml; FACET's cell_molecular_biologist, pathologist, computational_biologist and medicinal_chemist map to
bench scientist, clinician, computational biologist and drug-development lead; the physician-scientist is new), the
role cards, example-question pills, eyebrow label and accent bars of FACET's front end (ported from Tailwind to plain
CSS so the page still needs no CDN or build step), and the /api/personas endpoint. Not reused: FACET's connectors and
LLM synthesis (TrialMindKG answers only from the graph with cited edges).
1. **Landing page = Start here.** Title, one sentence of what the graph is (counts computed from the status
   endpoint, in words), five persona buttons, and under them "Try one of these" questions chosen by the persona.
   The Pharos graph becomes the first tab; the trial graph the second ("Failed trials: the differentiator").
   The internals line moves to the "Build & validation" tab.
2. **Entity card** (`/api/pharos/card/{node_id}`): one screen per target, disease or compound, with sections in
   a fixed order: what it is (name, type, ids, target development level where known); known links grouped by
   evidence kind, each group with a count and how many were pruned; predicted new links (top 10, each with its
   supporting paths); trials from the trial layer when the id matches; and an "explain" button on every row.
   This is the multi-hop criterion made visible on one page. Uses `node_view`, `predicted_links`, `explain_edge`.
3. **Plain words.** Names first, ids on hover or behind a small "ids" toggle; predicates rendered as sentences
   ("is associated with", "binds", "was tested in"); a confidence word with colour (kept, pruned, predicted) and a
   glossary tooltip for knowledge levels; "the exact question the graph answered" as a collapsible block instead
   of "openCypher query". A `wording.py` table maps predicate -> sentence (tested).
4. **A picture on every answer.** The neighbourhood SVG (code exists in the Explore tab) drawn by default on the
   entity card and on each Pharos answer, with the evidence kinds as colours and the predicted links dashed.
   A small summary band above each answer: rows, sources, evidence kinds, pruned edges (numbers, not prose).

Mobile and polish pass (2026-10-31, owner request): bottom navigation and single-column role cards on phones, evidence
tables that stack into labelled rows under 760 px, a role popover in the header, dropdown search results, jump chips
and a copyable deep link on every card, skeleton loading and empty states, soft elevation and a type ramp, 44 px touch
targets, focus rings and reduced-motion support. Phone screenshots: docs/ui_after/m1-m4.

Third pass (2026-10-31, owner: "smooth, interactive, frictionless, clean, intuitive; easy to navigate complex workflows;
all conditions of the brief"): hash routes (back button, shareable links), search-as-you-type with keyboard control,
explain drawer, workflow strip, recent trail, and the Numbers tab (P2 items 5 and 8 done: results with delta vs G0,
pruning metrics, schema with BioLink IRIs, downloads). Screenshots 8_numbers.png, 4_why_this_link.png (drawer), m5.

### P2, should have (one cloud session, Nov 7-9)
5. **Numbers page for the judge** (`/api/results`): the G0-G3 table from `outputs/linkpred/report.md` (built by
   `collect_results`), the pruning report figures, the schema card (classes, predicates, IRIs, counts: Standards
   10%), and download links: verdict file, pruned KGX graph, predictions file.
6. **Question -> subgraph download** for Pharos answers (KGX + PyG `.pt` in one zip), the thing Ben Busby asked
   for in Slack; the trial side already has `report_subgraph.zip`.
7. **Guided tour.** The 5 demo questions as a strip of buttons; each answer shows its check passing (green) and
   the evidence ids clickable. The video follows this strip.
8. **Explain panel.** The explain-edge result drawn: provenance table, verdict with reasons, corroborating layers,
   and the timeline as a horizontal line with dated points.

### P3, if ahead (Nov 10-12)
9. **Static replay for judges.** MAC: `python3 pipelines/make_demo_site.py` runs the 5 demo questions and five
   entity cards on the real graph and writes self-contained HTML into `docs/demo/` (committed, small). Judges who
   do not load the 16M-edge graph still see the real screens; the README links to it. (Cheaper and safer than a
   hosted instance; a hosted EC2 demo stays optional, NEXT_STEPS step 22.)
10. **Hosted demo** on a small EC2 with the pruned graph without PPI, only if the credits and time allow after the
    GPU runs (USD 10-20 for the judging weeks); never before the numbers are in.

### Not doing (deadline)
- A new front-end framework or build step (the single HTML file starts with one command and that is a usability
  point in itself); mobile layout; user accounts; chat memory; free-text LLM answers without a cited row.

## 4. How it is tested and shown
- Every new endpoint: a fixture test in tests/test_app.py or tests/test_pharos_app.py (tiny Pharos layer).
- Every rendering rule (predicate -> sentence, confidence word, id hiding): a unit test on the Python side; the
  page uses the same table through `/api/wording` so the words cannot drift.
- Before/after screenshots: docs/ui_before/ (today) and docs/ui_after/ (after P1 and after P2) made by
  `scripts/ui_screenshots.js`; the owner judges from the pictures, not from running the app.
- MAC (NEXT_STEPS step 11, after P1 lands): run the app on the real graph, open each persona, run the five demo
  questions, read one entity card and one explain panel; paste back anything slow, wrong or ugly.

## 5. Video storyboard (3-5 minutes, OWNER records, NEXT_STEPS step 20)
1. Start here: choose "Physician-scientist"; one sentence on what the graph is.
2. Entity card for a disease (e.g. Alzheimer disease): known targets by evidence kind, pruned edges shown, then
   the predicted new targets with their paths. Click "explain" on one edge: provenance, verdict, timeline.
3. The same question on plain Pharos vs the enriched graph: the Numbers page, G0 vs G3 with intervals.
4. Switch persona to "Drug-development lead": a trial-layer question (TULIP-1 or a SOD1/tofersen multi-hop) with
   cited claims and the verifier's rejected claims.
5. Download the subgraph (KGX + PyG) and the verdict file; show the schema card; end on the public repository.

## 6. Owner decisions (reply by number)
1. Personas: keep the five above, or rename/merge (proposed: keep; the owner is persona 1).
2. Order: P1 before the GPU numbers are in (proposed: yes, the cloud is idle while the Mac computes).
3. Static replay (P3.9) instead of a hosted demo (proposed: yes; hosted only if credits and time are left).
