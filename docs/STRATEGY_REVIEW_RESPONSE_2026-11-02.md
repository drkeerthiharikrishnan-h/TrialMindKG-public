# Strategy review: response (written 2026-11-02 by an outside reviewer)

Answers docs/archive/STRATEGY_REVIEW_HANDOFF.md. Everything numeric below was measured on the Mac today from the files on
disk (data/pharos/diseases.tsv.gz, data/kg/pharos/edge_verdicts.tsv.gz, data/kg/opentargets/edges.tsv.gz,
outputs/g0 and g2/heterodata.pt) or copied from the hand-off; nothing is estimated. Code changes made today are
listed in section 6; all 91 tests of the touched modules pass in .venv.

---

## 1. Verdict: is the current approach sound?

The machinery is sound and better than most entries will have (shared test pairs with a digest, hash-based splits,
leak removal across edge types, paired fold-by-fold intervals, a shuffled-layer control, an explainable meta-path
model). The **test it was pointed at is not**, and none of the seven flaws fixed since Oct 31 was the main one.
Three structural problems, in order of importance:

**1. The test set is not gene-disease biology.** The 250,314 "non-text-mining" Pharos pairs are:

| source | pairs | what it is |
|---|---|---|
| Expression Atlas | 159,748 | differential-expression hits: **107 diseases, median 652 genes per disease, largest 8,537** |
| DisGeNET | 82,453 | mixed; includes BEFREE text-mined pairs; Pharos gives no knowledge level (`not_provided`) |
| CTD | 34,797 | curated |
| TIGA | 26,317 | GWAS statistics |
| eRAM | 14,263 | `not_provided` |
| Monarch 9,424 · UniProt Disease 5,549 · MedlinePlus 4,107 · DrugCentral 3,740 · UniProtKB-KW 3,364 · AmyCo 260 | | curated |

63% of the test pairs ask "is this gene differentially expressed in one of 107 disease datasets". No knowledge
context can answer that; disease popularity can (degree baseline 0.91); and once popularity is matched away nothing
is left (0.56 everywhere in block 3). That is exactly the pattern you observed. It is a property of the test, not
of the graphs or the GNN. 83.8% of those pairs sit on diseases with more than 100 genes.

The curated subset (`knowledge_level = knowledge_assertion`: CTD, Monarch, UniProt Disease, MedlinePlus,
UniProtKB-KW, AmyCo, DrugCentral indication) is **39,513 unique pairs, 8,226 genes, 7,364 diseases, median 1 gene
per disease, 29.8% on hub diseases**. That is a gene-disease benchmark a biologist recognises, and it is where
ontology and pathway context could plausibly help (rare diseases, few known genes).

**2. The 1% sample was never a smaller version of the benchmark; it was a different graph.** And the premise that
the full graph needs a GPU is wrong. 13.1M of G0's 13.4M task edges are TIN-X text co-occurrence, which you
already exclude from the test. Remove them (they stay in the KG; they just do not enter the training graph) and
cap the 11.7M PPI edges at 2M, and the **full remaining Pharos graph (432,380 nodes, ~3.6M edges) trains at 2.5 s
per epoch on this Mac's CPU with 11.6 GB peak RAM** (measured today, block-2 wiring, hidden 64, reverse edges). 300
epochs x 5 folds is about one hour per graph. No AWS needed.

**3. The enrichment layer is unfiltered literature noise.** The Open Targets layer holds 7,225,847 gene-disease
edges with **median score 0.035; 90% score below 0.11**. Those low scores are Europe PMC co-occurrence: the same
kind of evidence you excluded from the test, re-admitted at 7M edges. It also doubles the task relation's
positives, so every G2-vs-G0 difference so far confounds "more labels" with "more context" (your block-2 reading was
right). Score >= 0.1 leaves 770,033 edges; >= 0.2 leaves 484,345. The build engine now supports that filter
(`min_value:`), but the OT layer and G2/G3 have not been rebuilt yet.

Four smaller points:

- **The GNN was trained for 50 gradient steps.** `train_epoch` is full-batch: one optimiser step per "epoch". 50
  epochs = 50 steps. No GNN result so far was from a trained model.
- **G1 = G0 is arithmetic, not a finding.** Pruning v1 prunes 25,424 of 13.4M gene-disease edges, 24,919 of them
  TIN-X. Among test-eligible sources it prunes 0.2% (CTD 6 of 27,157; Expression Atlas 266 of 158,707). Removing
  0.2% of training edges cannot move an AUROC. Likewise "pruning lowers G3 below G2" is the wrong test of pruning:
  link prediction rewards keeping every edge. Pruning is judged by precision on wrong edges (gold set, spot check)
  and by calibration against independent evidence, not by link-prediction AUROC.
- **Hand-off point 7 is resolved**: data/kg/pharos/edges.tsv.gz and edge_verdicts.tsv.gz both have 26,462,916 rows
  (Pharos with PPI and TIN-X). The committed prune_metrics.json (1,650,119) is the stale cloud build. The verdict
  file covers everything.
- **Process**: five flags per run, no pre-registered protocol, smoke numbers discussed as results. The fix is a
  frozen protocol (section 3) and a stop rule, not more diagnosis.

**One piece of good news you did not have.** Pruning-v1 confidence on Pharos gene-disease edges is strongly ordered
against Open Targets, which the Pharos pruner never saw (outputs/pharos/prune_calibration.md, produced today):

| confidence decile (non-TIN-X edges, ~30,400 each) | pair also in OT | OT score >= 0.2 |
|---|---|---|
| 1 (least confident) | 33.6% | 4.9% |
| 5 | 50.6% | 6.3% |
| 9 | 72.9% | 26.9% |
| 10 (most confident) | 82.3% | 49.6% |

Pruned vs kept: 16.2% vs 55.2% in OT (non-TIN-X); 2.3% vs 12.4% for TIN-X. That is real-data evidence that the
confidence column means something, obtained without a GNN and without the gold set. Caveat to state: Open Targets
contains literature evidence too, so part of the agreement is shared provenance; the >= 0.2 column reduces that.

## 2. What the submission should lead with, and what to cut

Scoring: Usability 40, Accuracy & Pruning 30, Explainability 20, Standards 10. The GNN comparison is at most a part
of 30 and has consumed most of the last week. Lead order:

1. **Usability (40): the agent a biologist can use.** The Pharos card (target or disease): evidence by kind,
   keep/prune verdict with reasons on every edge, predicted links with the named paths behind them, trial context.
   This has never run on the real graph (step 11). It is the biggest-weight item and the biggest risk.
2. **Accuracy & Pruning (30): a verdict and a confidence for every Pharos row, with Pharos's own identifiers**
   (verdict_file.py, built, not yet run), the pruned KGX graph next to the full one ("send both"), the calibration
   curve above, the injected-error AUPRC, and the 200-edge human spot check. Say plainly that the threshold is
   conservative (0.2% of curated gene-disease edges pruned) and that the confidence column lets the judges apply
   their own.
3. **Explainability (20):** provenance fields on every edge, the explain-edge view, and the meta-path model, whose
   every prediction is a named path with provenance ("an interaction partner of this gene is associated with this
   disease", 0.60 AUROC on G0 under degree-matched negatives today). Make the meta-path model the headline
   predictor if it equals or beats the GNN; keep the R-GCN as the "same ML on both graphs" comparison the brief asks
   for.
4. **The benchmark itself as a contribution**: a frozen, fair protocol (curated test set, popularity removed,
   identical pairs and negatives in every graph, paired intervals, shuffled-layer control) that anyone can rerun on
   a laptop. An honest number on a fair test beats a big number on a broken one.
5. **Standards (10):** the schema card (classes, predicates, IRIs, counts), Protein `PR:000000001`, BioLink-only
   core. Half a day.

**Cut (do not do before Nov 17):** AWS GPU runs (not needed, measured); pruning v2 with an out-of-fold GNN score;
block 4's ten per-layer ablations (one shuffled control of all five layers only if the enriched graph wins, see
step 2); OptimusKG baseline; task A (ligand-protein) link prediction; sparsity and cold_target splits; HGT; PyKEEN /
ComplEx / RotatE (new dependency, new pitfalls); Neptune; Nemotron on borderline edges; the text pipeline; the 1,020
Nemotron findings; the 60 non-edge expert Q&A rows; the 5% / 3-seed smoke variants (superseded by full-graph runs);
any further change to the test design after Nov 3.

## 3. The frozen benchmark (decide once, today)

- **Task**: gene -> disease, Pharos `gene_associated_with_condition`.
- **Test items**: Pharos pairs with `knowledge_level = knowledge_assertion` (39,513 pairs), the same in every graph
  (G0 reference, digest recorded). Statistical (Expression Atlas, TIGA, OT) and `not_provided` (DisGeNET, eRAM) pairs
  stay in the graph as a context relation (`--positives assertions`), never as positives or test items.
- **Split**: 5-fold by pair hash (existing). Held-out pairs removed from every edge type joining a gene and a
  disease (existing leak removal; 7,306 context edges removed per fold today).
- **Negatives**: one per test pair, from Pharos diseases in the same power-of-two degree bin as the true disease
  (`--neg-match degree`), identical in every graph. Today's check: degree baseline 0.508, so popularity is gone.
- **Metric**: AUROC on those pairs, plus the degree strata and the meta-path model. No MRR over all diseases
  (dominated by popularity, sensitive to the candidate set).
- **Training graph**: full Pharos minus TIN-X edges (`--drop-levels text_co_occurrence`), every edge type capped at
  2M by edge-id hash (`--max-edges 2000000`; bites PPI, in run A also the context relation), gene->protein bridge
  never sampled. Reverse edges, learned embeddings, 30 bases, training negatives from curated diseases, hidden 64,
  **300 epochs**, 1 seed (3 if time allows on Nov 6).
- **The one comparison**: G2 minus G0 (then G3 minus G0), paired over the 5 folds, 95% t interval (compare.py). The
  meta-path model's G2 minus G0 is the secondary comparison. Nothing else decides.
- **Not to be changed after today**: the test items, the negatives, the metric, the split, the epochs, the wiring.
  Only the graph files (rebuild of step 2) and the number of seeds may change.

## 4. The plan (dates, stop rules)

**Step 0 - today, Sun Nov 2 (1 h, Mac).** Review and commit the code changes of section 6; commit
outputs/pharos/prune_calibration.md; post the organiser question (NEXT_STEPS step 10: gold-set identifiers and
format). Stop rule: none, bookkeeping.

**Step 1 - Mon Nov 3: run A, the frozen benchmark on the graphs as they are (Mac, ~7-8 h unattended).**
Commands in section 5. G0 ~1.5 h, G2 and G3 ~3 h each (more edges). Read compare.md on Nov 4 morning.
Stop rule: this result is reported whatever it says. If the Mac runs out of memory on G2, rerun G2/G3 with
`--max-edges 1000000` and say so; do not change anything else.

**Step 2 - Tue Nov 4 to Wed Nov 5: the one justified rebuild, then run B.**
Rebuild only what changes the question: (a) MONDO EFO/Orphanet tables (step 7f), (b) the Open Targets layer with
`min_value: {associationScore: 0.1}` so its edges are evidence rather than literature volume and land on Pharos
disease nodes, (c) make_graphs G2 and G3 only (G0 and G1 are unchanged). Then run B = the identical run-A command
on G2 and G3 with tag `frozen_b`, overnight Nov 4 or 5. ChEMBL's EFO fix is optional (its indications are context
only). Stop rule: **if the rebuilt G2/G3 files do not exist by Nov 5 18:00, skip run B; run A stands.** If run B
exists, run B is the result of record and run A goes in the appendix.

**Step 2b - Thu Nov 6: close the link-prediction work (hard freeze 18:00).** If G2 - G0 has an interval above 0,
run exactly one control: the same command on G2 with `--shuffle-layers opentargets_layer,chembl37,hpo_hierarchy,
go_hierarchy,mondo_hierarchy --tag frozen_shuffled` (one run, ~3 h) and add it to compare.md. If time remains, add
`--seeds 3` for G0 and the winning graph. Then write outputs/linkpred/report.md's conclusion from compare.md
(CLAUDE.md rule 7: wording computed from the interval). After 18:00 nothing in pipelines/linkpred changes.

**Step 3 - Nov 5-6 in parallel (Mac ~1 h compute; owner ~3 h by Nov 9): the pruning deliverables (step 7g + 8 + 17).**
prune_compare -> prune_kg (v1.1) -> verdict_file (coverage report) -> prune_calibration again (verdicts changed) ->
make_spotcheck -> the owner fills 100 pruned + 100 kept edges and the 20 edge_judgement rows. Stop rule: v1.1 is
the final pruner. Its reported precision comes from the spot check; its calibration from prune_calibration.md; its
recall on injected errors from prune_metrics.json. No v2.

**Step 4 - Fri Nov 7 to Wed Nov 12: usability and explainability (60% of the score).**
Nov 7: app on the real graph (step 11), fix what is slow or wrong. Nov 8: predictions file (predict.py with the
frozen wiring, or the meta-path model if it won) so the card shows predicted links with their paths. Nov 9-10: the
5 demo questions, answers reviewed by you. Nov 11: schema card, Protein PR id, README around the Pharos core,
Maintain procedure written down. Nov 12: the video (one demo question, one explain-edge with its timeline, one
predicted link with its paths, one trial-layer multi-hop question). Stop rule: feature freeze Nov 12 18:00.

**Step 5 - Thu Nov 13 to Sun Nov 16: write-up, secrets check, repository public, submit Nov 16.**
Nov 13: submission text from the four criteria, the honest results paragraph (section 7). Nov 14: `pytest -q`
green on the Mac, `git log --all -- .env data/internal` empty, repo public. Nov 15: re-read against the brief.
Nov 16: submit. Nov 17 is the buffer, not the plan.

## 5. Exact commands for steps 0-2

Every Mac block starts with:
```bash
cd ~/projects/TrialMindKG && source .venv/bin/activate
```

**Step 0 (today).** The changes are already in your working tree (section 6). Check, test, commit:
```bash
git status --short
python3 -m pytest -q tests/test_linkpred.py tests/test_linkpred_eval_set.py tests/test_linkpred_test_levels.py tests/test_kgbuilder.py tests/test_prune_calibration.py
git add pipelines/linkpred/run.py pipelines/kgbuilder/engine.py pipelines/prune_calibration.py tests/test_linkpred_test_levels.py tests/test_linkpred_eval_set.py tests/test_kgbuilder.py tests/test_prune_calibration.py outputs/pharos/prune_calibration.md outputs/pharos/prune_calibration.json docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md
git commit -m "linkpred: curated test set (--test-levels), --drop-levels, positives split before the cap; engine min_value filter; pruning calibration vs Open Targets; strategy review response"
git push origin main
```
(Your tree also shows four older uncommitted Mac files: data/reference/chembl_synonyms.tsv.gz,
outputs/contradiction_report.csv, outputs/pharos/unmapped.tsv, outputs/run_manifest.json, dated Oct 26-30. They
are not from today; commit or discard them as you see fit, separately.)

**Step 1 (Nov 3, run A).** One command, leave it running with the lid open (`caffeinate -i` keeps the Mac awake):
```bash
caffeinate -i python3 -m pipelines.linkpred.run --graphs G0,G2,G3 --tasks B --splits kfold --seeds 1 --epochs 300 --hidden 64 \
  --drop-levels text_co_occurrence --test-levels knowledge_assertion --positives assertions --max-edges 2000000 \
  --reverse-edges --learn-embeddings --num-bases 30 --neg-pool training --neg-match degree \
  --baselines --metapaths --strata --tag frozen_a 2>&1 | tee logs/linkpred_frozen_a.log
python3 -m pipelines.linkpred.compare --base G0@frozen_a
```
What to expect in the log, per graph: "edges of knowledge levels ['text_co_occurrence'] removed: ... 13,118,763",
"244,725 task edges ... moved to a context relation" (G0), a "Gene|gene_associated_with_condition|Disease 52,249"
line, "test pairs 7850" per fold, and a degree_dst near 0.50. If degree_dst is far above 0.5, stop and tell Claude:
the negatives are not matched. Paste compare.md and the strata lines.
Memory fallback (only if the process is killed on G2): rerun with `--graphs G2,G3 --max-edges 1000000` and the same
tag; the report then says 1M.

**Step 2 (Nov 4-5, rebuild + run B).** Edit sources/opentargets_layer.yaml: in the
`ot_target_disease_association` spec, directly under `table: associationByOverallDirect`, add one line
```yaml
    min_value: {associationScore: 0.1}
```
and change `version: "26.09"` to `version: "26.09 (direct associations, score >= 0.1)"` so the provenance says what
was filtered. Then:
```bash
python3 pipelines/mondo_ref.py && wc -l data/reference/mondo_efo_exact.tsv data/reference/mondo_orphanet_exact.tsv
python3 pipelines/build_kg.py sources/opentargets_layer.yaml && python3 pipelines/prune_kg.py sources/opentargets_layer.yaml
grep -A3 "gene_associated_with_condition" outputs/opentargets/summary.md | head
python3 pipelines/make_graphs.py G2,G3 && grep -h "re-typed" outputs/g2/summary.md outputs/g3/summary.md
```
Expected: the OT gene-disease edge count near 770,000 instead of 7.2M; far fewer EFO twins in the G2 summary.
Then run B, identical flags, new tag:
```bash
caffeinate -i python3 -m pipelines.linkpred.run --graphs G2,G3 --tasks B --splits kfold --seeds 1 --epochs 300 --hidden 64 \
  --drop-levels text_co_occurrence --test-levels knowledge_assertion --positives assertions --max-edges 2000000 \
  --reverse-edges --learn-embeddings --num-bases 30 --neg-pool training --neg-match degree \
  --baselines --metapaths --strata --tag frozen_b 2>&1 | tee logs/linkpred_frozen_b.log
cp outputs/linkpred/G0_B_kfold@frozen_a.json outputs/linkpred/G0_B_kfold@frozen_b.json
python3 -m pipelines.linkpred.compare --base G0@frozen_b
```
(G0 did not change, so its run-A result is copied under the run-B tag for the paired comparison; the two files
must show the same test_set digest, which compare.py checks.)

**Step 3 (pruning deliverables)** is NEXT_STEPS 7g and 8 unchanged, plus one line after prune_kg:
```bash
python3 pipelines/prune_calibration.py
```

## 6. Code changes made today (in your working tree, uncommitted; 91 tests pass)

- `pipelines/linkpred/run.py`: `--test-levels` (restrict test items to given knowledge levels; digest includes it),
  `--drop-levels` (remove edges of given levels from the training graph after the reference pairs are taken and
  before the cap), the `--positives` split now happens before the cap (so the cap bounds the context relation, not
  the 52k positives), settings.test_set records `included_knowledge_levels`. New function
  `drop_knowledge_levels`. Note: the context relation is still named `..._text_mined` (old constant); it now holds
  statistical and not_provided edges too. Cosmetic; left as is.
- `pipelines/kgbuilder/engine.py`: `min_value: {column: minimum}` in an edge spec keeps rows whose value is a number
  >= minimum (for the Open Targets score filter).
- `pipelines/prune_calibration.py` (new) + `outputs/pharos/prune_calibration.md/.json` (run today).
- Tests: `tests/test_linkpred_test_levels.py` (3), `tests/test_prune_calibration.py` (2), one test in
  `tests/test_kgbuilder.py`, one stub signature in `tests/test_linkpred_eval_set.py`.
- Nothing was committed, no data was rebuilt, no result JSON was written (today's timing runs called the runner in
  memory). Both of the timing runs' numbers are in this file's section 1 and the chat, not in outputs/.

## 7. What to tell the judges if the comparison stays null

Write it from compare.md, in this shape (fill the numbers from the file; do not soften or inflate):

> We tested whether our enriched graph predicts held-out curated Pharos gene-disease associations better than
> Pharos alone, with the same R-GCN on both graphs, identical test pairs (39,513 curated pairs, 5 folds) and
> identical degree-matched negatives, so that disease popularity cannot score (degree baseline 0.51). Under this
> test the enriched graph's AUROC was [x] against [y] for Pharos alone (paired difference [d], 95% interval
> [lo, hi]); the explainable meta-path model gave [x'] against [y']. The interval includes zero: with these five
> layers, joined this way, we found no gain on this task, and we report that rather than a gain on an easier test.
> Three things this does show: (1) the benchmark is fair and reusable (shared pairs, leak removal, popularity
> removed, paired intervals, a shuffled-layer control), and it runs on a laptop; (2) the enriched graph does
> change what is explainable: [n]% of test pairs have a named supporting path versus [m]% in Pharos alone; (3) the
> 2-week budget went where the score is: every Pharos edge carries a verdict, a confidence and reasons, calibrated
> against an independent source (lowest confidence decile: 4.9% corroborated by Open Targets at score >= 0.2;
> highest: 49.6%), and a biologist can ask the graph questions and see the lineage of every answer.

If the interval is above zero, replace the middle sentence with the measured gain and add the shuffled-layer
control result; if the rare-disease stratum alone is positive, say that and only that.

One sentence to avoid: anything that attributes the null to "not enough compute". The full graph ran on a laptop.
