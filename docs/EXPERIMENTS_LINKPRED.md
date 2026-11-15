# Link prediction: every experiment, result and learning (record, 2026-11-06; MONDO control added 2026-11-07)

One place for all link-prediction work behind the brief's key question: does the enriched graph beat Pharos at predicting
held-out links with graph ML (GNNs, GCNs, graph embeddings)? Every number below is copied from a committed result file or
report (listed per experiment); `loop/ledger.tsv` holds the pre-registrations and verdicts in time order.
**AUROC here is a ranking score on a benchmark, not clinical accuracy.**

## 1. Set-up (identical for every experiment unless stated)

- **Task B, gene -> disease.** Test positives = Pharos `knowledge_assertion` gene-disease pairs, digest `24f5864cc07ad723` (checked
  equal on every comparison). 5 folds, 1 seed, degree-matched negatives, leak edges removed (enrichment edges that restate a
  held-out pair). Text-mined co-occurrence edges are dropped. Frozen flags are in `docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md`.
- **Graphs.** G0 = Pharos only (432,380 nodes). G2 = Pharos + Open Targets (score >= 0.1) + ChEMBL 37 + HPO, GO and MONDO
  hierarchies (1,316,305 nodes, 29,637,900 edges; rebuilt 2026-11-04 09:20). G3 = G2 after pruning (edges with verdict
  `prune` removed). Cap: at most 1,000,000 edges per edge type (both sides of every comparison).
- **Statistics.** Paired difference per fold (`pipelines.linkpred.compare.paired_delta`), 95% CI over the 5 folds;
  "5/5" = the variant is higher in all five folds.
- **Two graph builds.** E001/E003 (frozen R-GCN) used the graphs of run A, built before the 2026-11-04 Open Targets
  rebuild; E004-E008 used the rebuilt G2/G3. G0 is the same in both. Never compare G2 numbers across the two builds.

## 2. Results at a glance (mean AUROC over 5 folds, 1M cap)

| Model | G0 Pharos | G2 enriched | G3 enriched + pruned | G2-G0 (95% CI) | G3-G2 = effect of pruning | Source |
|---|---|---|---|---|---|---|
| R-GCN, frozen protocol (run A graphs) | 0.5866 | 0.5821 | 0.5820 | -0.0045 (-0.0064 to -0.0026), 0/5 | -0.0001 | E003 |
| Meta-path model (run A graphs) | 0.6772 | 0.7677 | 0.7670 | +0.0906 (+0.0845 to +0.0966), 5/5 | -0.0007 | E003 |
| Meta-path model (rebuilt graphs) | 0.6772 | 0.7986 | 0.7978 | +0.1214 (+0.1181 to +0.1247), 5/5 | -0.0008 | E004/E005 |
| RotatE embedding, dim 256 | 0.6281 | 0.6325 | 0.6363 | +0.0044 (-0.0010 to +0.0098), 4/5 | **+0.0038 (+0.0007 to +0.0068), 5/5** | E006 |
| RotatE embedding, dim 512 (**unequal training, see note**) | 0.6335 | 0.6323 | 0.6501 | -0.0012 (-0.0041 to +0.0016), 2/5 | +0.0178 (+0.0124 to +0.0233), 5/5: **not valid, G3 trained 2.5x longer** | E006 |
| GNN + path features (R-GCN encoder) | 0.6614 | 0.7930 | 0.7916 (GPU) | **+0.1315 (+0.1260 to +0.1370), 5/5** | -0.0014 (-0.0037 to +0.0010), 1/5 (G3 on GPU vs G2 on Mac) | E008 |
| GNN + path features, G2 **without the MONDO hierarchy** (control) | 0.6614 | 0.6944 (no MONDO) | - | **+0.0329 (+0.0293 to +0.0366), 5/5**; no-MONDO minus G2 -0.0986 (-0.1032 to -0.0940), 0/5 | - | E008 control |
| RotatE dim 256, random-pruned control G2R (same edge counts removed at random) | - | G2R 0.6290 | 0.6363 | G2R-G2 -0.0035 (-0.0085 to +0.0015), 1/5 | **G3-G2R +0.0073 (+0.0022 to +0.0124), 5/5** | E009 |
| (inside E008) GNN head only | 0.5579 | 0.5571 | - | -0.0008 (-0.0052 to +0.0036) | - | E008 |
| (inside E008) path head only | 0.6583 | 0.7913 | - | +0.1330 (+0.1276 to +0.1383), 5/5 | - | E008 |
| Common-neighbour baseline (run A / rebuilt) | 0.5000 | 0.6498 / 0.730 | 0.6494 / - | +0.1498 / +0.2300 | - | E003/E004 |

**Note on RotatE dim 512 (found 2026-11-06 09:50, from the per-fold `kge_epochs_done` in the result files).** Training stops at 300 epochs or 45 minutes per fold, whichever comes first. Epochs reached: G0 141-149, G2 118-119, G3 270-300 (G2 shared the GPU with the dim-256 run; G3 ran alone after it ended). The dim-512 comparisons therefore mix the graph effect with the amount of training, and its G3-G2 gain is withdrawn. The dim-256 run reached its 100-epoch limit on every fold of every graph, so its numbers compare equal training: **pruning effect +0.0038 (CI +0.0007 to +0.0068), 5/5**. E009 (amended) tests it against random removal with the same equal-epoch settings.

## 3. Experiments in order

| ID | Question | Pre-registered rule | Result | Verdict |
|---|---|---|---|---|
| E001 | Does enrichment improve the frozen R-GCN? | G2-G0 CI > 0 | G2-G0 -0.0043 (CI -0.0065 to -0.0021); caps differed (G0 2M, G2/G3 1M) | rejected |
| E002 | Replicate on rebuilt G2/G3 at the 2M cap | - | stopped after ~2.5 h without a fold (projected 35-45 h) | superseded by E003-E006 |
| E003 | Do run A's results survive a cap-matched G0 (1M)? | as E001 | R-GCN -0.0045 (0/5); meta-path +0.0906 (5/5); the cap changes G0 by +0.0002 (R-GCN) / -0.0088 (meta-path) | accepted: GNN null stands, meta-path gain real |
| E004 | Does the meta-path gain replicate on the rebuilt graphs? | exploratory | +0.1214 (G2), +0.1207 (G3); common-neighbour gains more (+0.230) | mixed: replicates; source overlap suspected |
| E005 | Which layer carries the meta-path gain? (drop one layer at a time, G2) | exploratory | drop MONDO: gain falls to +0.0223 (~82% of the gain gone); drop Open Targets: +0.1088; drop HPO / GO: no change; drop ChEMBL: +0.1247 (ChEMBL slightly hurts this task) | gain is mostly MONDO ontology smoothing |
| E006 | Does a graph-embedding model (RotatE) gain from enrichment? | G2-G0 CI > 0; G0 < 0.55 = inconclusive | not passed at either size (CIs include 0); G0 0.63 (valid). Secondary: G3 beats G0 and G2 at both sizes, 5/5 | primary not passed; pruning gain |
| E008 | Does a GNN that also sees paths gain from enrichment? | G2-G0 CI > 0 AND on G2 GNN+paths beats GNN-only and paths-only (CI > 0) | +0.1315 (5/5); beats GNN-only by +0.2359 and paths-only by +0.0017 (CI +0.0007 to +0.0027), 5/5 | **passed**; gain carried by the paths |
| E008 on G3 | Does the pruned graph keep the GNN + paths gain? (G0 and G3 both run on a Brev H100, tag `diag_gnnpath_1m_gpu`) | allowed by E008 (G2 passed); report figure | G3-G0 (both GPU) +0.1302 (CI +0.1266 to +0.1339), 5/5; G0 GPU = Mac G0 (-0.0001, CI -0.0010 to +0.0009); G3 (GPU) - G2 (Mac) -0.0014 (CI -0.0037 to +0.0010) | gain kept after pruning; pruning neutral for this model |
| E008 control | Same without the MONDO hierarchy (G2 with `--drop-layers mondo_hierarchy`, 46,732 edges; Mac, tag `diag_gnnpath_1m_nomondo`) | report figure (pre-registered with E008) | finished 2026-11-06 20:55 IST (started 08:46, before the 18:00 freeze). GNN + paths 0.6944: no-MONDO minus G0 **+0.0329 (CI +0.0293 to +0.0366), 5/5**; no-MONDO minus G2 -0.0986 (CI -0.1032 to -0.0940), 0/5. MONDO carries ~75% of the E008 gain; GNN head alone -0.0050 (CI includes 0). Report `outputs/linkpred/diag_e008_nomondo.md` | gain without MONDO is real but ~4x smaller; same picture as E005  |
| E009 | Does the pruner remove the right edges, or does any 1.27% removal help? (RotatE dim 256, 100 epochs every fold; G2R = same number of edges per type and layer removed at random, seed 0) | PASS if G3-G2R CI > 0 (amended before any result to equal-epoch settings) | G3-G2R +0.0073 (CI +0.0022 to +0.0124), 5/5; G2R-G2 -0.0035 (CI -0.0085 to +0.0015); G3 on the H100 reproduced E006 dim-256 G3 exactly (same AUROC and final loss in every fold) | **passed**: the pruner's choice matters; random removal does not help |

(E007 in the ledger is not a link-prediction experiment: judge labels and schema checks.)

## 4. What we learned

1. **Enrichment helps when the model can use paths between a gene and a disease.** Path-based models gain +0.09 to +0.13
   AUROC (meta-path, GNN + paths), 5/5 folds every time.
2. **A plain message-passing GNN does not gain** (R-GCN -0.0045; the GNN head alone in E008 -0.0008). It builds one vector
   per node and cannot count the paths *between* a gene and a disease, which is where the new signal is.
3. **Most of the path gain is the MONDO disease hierarchy** (E005: about 82%). A gene linked to a parent or child of a
   disease is predicted for that disease: legitimate and explainable, but partly a restatement of existing knowledge at a
   neighbouring ontology level. Without MONDO a real but small gain remains (+0.022 for the meta-path model; +0.033 for GNN + paths in the E008 control, 5/5, CI +0.029 to +0.037: MONDO carries ~75% of that model's gain).
4. **Open Targets adds a small, real part** (+0.013 meta-path); **HPO and GO add nothing** to this task; **ChEMBL slightly
   hurts it** (+0.003 when removed). This is about gene -> disease only, not about whether those edges are correct.
5. **Pruning (G3 vs G2):**
   - helps the graph-embedding model slightly: +0.0038 (dim 256, equal training), 5/5 folds, CI above 0. The dim-512 +0.0178 is withdrawn (G3 trained 2.5x longer than G2);
   - **beats random removal of the same edges** (E009): G3 vs G2R +0.0073 (CI +0.0022 to +0.0124), 5/5, while random removal slightly lowers the score (G2R vs G2 -0.0035). So the gain comes from *which* edges the verdicts remove, not from removing edges as such;
   - keeps the GNN + paths gain (E008 on G3: +0.1302 over Pharos, 5/5);
   - is neutral for the path model (-0.0007 / -0.0008) and the R-GCN (-0.0001);
   - for RotatE, *enrichment alone* gives nothing (G2 = G0); enrichment + pruning gives a small gain (dim 256: G3-G0 +0.0082). A possible explanation (not tested): pruning removes edges
     that add noise for an embedding model while leaving the paths the path models rely on.
6. **Model ranking on Pharos alone (G0):** meta-path 0.677 > GNN + paths 0.661 > RotatE 0.63 > R-GCN 0.587.
7. **Process lessons:** compare caps like for like (E001 vs E003); never run two heavy jobs at once on the Mac (E008 slowed
   to ~2 h per fold); cap training by epochs, not minutes, when graphs are compared (E006 dim 512: a shared GPU gave G3 2.5x the epochs of G2); pre-register before running and report the primary test even when it fails (E006).

## 5. How to word it in the submission

- "On the brief's benchmark, a graph model that uses paths between a gene and a disease (meta-path model; GNN with path
  features) predicts held-out Pharos links better on the enriched graph than on Pharos alone (+0.09 to +0.13 AUROC, 5/5
  folds). Most of this comes from the MONDO disease hierarchy; without it the gain is +0.02 to +0.03 (still 5/5 folds, CI above 0)."
- "A standard GNN (R-GCN) and a graph embedding (RotatE) do not gain from enrichment alone. After pruning, the embedding
  model gains slightly (+0.004 AUROC with equal training, 5/5 folds); Removing the same number of edges at random does not help (E009: pruned beats random-pruned by +0.007, 5/5 folds), so the verdicts pick the right edges."
- Always show the overlap controls next to a gain (E005 table; E008 MONDO control). Never call AUROC clinical accuracy.

## 6. Limits

One seed per configuration; 5 folds; 1M edge cap per type; a single task (gene -> disease); test positives come from Pharos
itself; secondary comparisons (G3) were planned graphs but not the pre-registered pass rule; RotatE folds were time-capped
(45 min), which gave unequal training in the dim-512 run (only dim 256 is used for the pruning claim); E008 on G3 ran on a GPU (H100) while G2 ran on the Mac (G0 matched across devices to 0.0001); E009 uses one seed and one random draw of removed edges (seed 0); RotatE training is deterministic (identical losses across two GPU types), so fold-to-fold differences are graph effects, but a different random draw could move G2R.

## 7. Files

- Reports: `outputs/linkpred/compare_capmatched.md` (E003), `outputs/linkpred/diag_rebuilt.md` (E004),
  `outputs/linkpred/diag_e005.md` (E005), `outputs/linkpred/diag_kge_rotate.md` (E006), `outputs/linkpred/diag_e008_nomondo.md` (E008 MONDO control); E008 and E009 in `loop/ledger.tsv`.
- Results: `outputs/linkpred/G{0,2,3}_B_kfold@<tag>.json` with tags `frozen_a`, `frozen_a_g0_1m`, `diag_rebuilt_1m`,
  `diag_e005_*`, `diag_kge_rotate_{d256,gpu}`, `diag_gnnpath_1m`, `diag_gnnpath_1m_gpu`, `diag_gnnpath_1m_nomondo`, `diag_kge_rotate_d256_e009`.
- Code: `pipelines/linkpred/run.py` (frozen), `kge.py` (RotatE driver), `gnnpath.py` (GNN + paths), `metapaths.py`.

## D: release-graph confirmation (pre-registered 7 Nov 12:30 IST; run 7-8 Nov; a REPORT, not a pass/fail test)

Question: does the enrichment gain hold on the graph we ship (final G2/G3 after the CC-22 ChEMBL and CC-16 Open Targets
rebuilds and the CC-27 one-class-per-prefix rule)? Same frozen flags, 1M cap, 5 folds, test digest 24f5864cc07ad723; G0 not re-run.
Each half was run once; nothing was tuned or re-run.

| Model | Graph | Gain over G0 (95% CI; folds higher) | Same model on the 4 Nov graph | Final minus 4 Nov |
|---|---|---|---|---|
| GNN + paths (`auroc_gnnpath`; H100, tag `diag_gnnpath_1m_release`) | G2 | **+0.1216** (+0.1159 to +0.1272; 5/5) | +0.1315 (E008) | -0.0100 (-0.0157 to -0.0044) |
| | G3 (pruned) | **+0.1186** (+0.1130 to +0.1243; 5/5) | +0.1302 | -0.0116 (-0.0180 to -0.0053) |
| Meta-path (`auroc_metapath`; Mac, tag `diag_release_metapath`) | G2 | **+0.1131** (+0.1083 to +0.1178; 5/5) | +0.1214 | -0.0084 (-0.0142 to -0.0025) |
| | G3 (pruned) | **+0.1103** (+0.1050 to +0.1155; 5/5) | +0.1207 | -0.0104 (-0.0176 to -0.0032) |

- Pruning on the final graphs (G3 - G2): GNN + paths -0.0030 (-0.0044 to -0.0015), meta-path -0.0028 (-0.0043 to -0.0013).
- GPU baseline check: G0 GPU vs Mac -0.0001 (-0.0010 to +0.0009).
- Pre-registered expectation (deltas inside the E008 interval +0.126 to +0.137): **not met**; both GNN + paths deltas sit just
  below it. Both models lose about 0.01 by the same amount, which points at the graph, not the model. Graph changes between the
  two: ChEMBL disease ids EFO -> MONDO (CC-22) with five MeSH exclusions (CC-28B), the Open Targets rebuild (CC-16), one class
  per id prefix (CC-27, OBA -> PhenotypicFeature). No ablation was run, so no single change is blamed.
- These graphs were rebuilt once more on 9 Nov (next section), so this row is history: the submission quotes the shipped graph
  only (table "Which number is quoted" at the end of this page).
- Files: `outputs/linkpred/G{2,3}_B_kfold@{diag_gnnpath_1m_release,diag_release_metapath}.json`, `logs/gpu_h100_release/`, ledger rows
  "D meta-path" and "D GNN+paths".

### D on the CS-9 graphs (re-run 9 Nov; DTO layer, Pharos rebuild, mock-PPI hard prune; a REPORT, not a test)

After CS-9 part 1 (G2 29,680,679 / G3 29,290,179 edges, `n_hard_pruned` 19,165) both halves of D were run once more with the
frozen flags, 1M cap, 5 folds, test digest 24f5864cc07ad723 (G0 base unchanged: `G0@diag_gnnpath_1m_gpu` for the GNN + paths half).
GNN + paths (H100, tag `diag_gnnpath_1m_cs9`, `scripts/gpu_kge_run.sh cs9`, ~70 min): G2 - G0 **+0.1235** (+0.1167 to +0.1304; 5/5),
G3 - G0 **+0.1219** (+0.1139 to +0.1299; 5/5); previous release graphs (history, not quoted) +0.1216 / +0.1186. Meta-path (Mac, tag `diag_cs9_metapath`):
G2 - G0 **+0.1136** (+0.1071 to +0.1201; 5/5), G3 - G0 **+0.1132** (+0.1063 to +0.1200; 5/5); previous +0.1131 / +0.1103. The CS-9
gains are equal to or slightly above the release-graph gains and the intervals overlap; the G3 - G2 gap narrowed on both models
(hard-pruning the 19,165 placeholder self-interactions costs nothing on this benchmark). **The submission quotes these numbers
and no others:** +0.124 (G2) / +0.122 (G3) GNN + paths, on the graph that is shipped. Files: `outputs/linkpred/G{2,3}_B_kfold@diag_gnnpath_1m_cs9.json`, `logs/gpu_kge_cs9.log`,
`outputs/linkpred/compare_cs9_gnnpath.md`, `compare_cs9_metapath.md`; ledger rows "CS-9 part 1" and "D (CS-9 graphs)".

## Which number is quoted (one table; the earlier builds in the sections above are history and are not quoted)

The README headline, the app's Numbers page and the judge's card all quote the first row, computed by `pipelines/headline.py`
from the result files. The other rows are the same model on earlier builds of the enriched graph and are history: they are not
quoted as the result anywhere. G2 is the enriched graph, G3 its pruned version, G0 Pharos alone.

<!-- headline-history:start -->
Written by `python pipelines/headline.py --readme` from the result files in `outputs/linkpred/`; do not edit by hand. Pharos alone is the same graph in every row.

| Build of the graph | Quoted as the result? | Enriched minus Pharos alone (95% CI; folds higher) | Pruned minus Pharos alone | Result files |
|---|---|---|---|---|
| the shipped graph (9 November build) | **yes** | +0.1235 (+0.1167 to +0.1304; 5/5) | +0.1219 (+0.1139 to +0.1299; 5/5) | `G2_B_kfold@diag_gnnpath_1m_cs9.json`, `G3_B_kfold@diag_gnnpath_1m_cs9.json`, `G0_B_kfold@diag_gnnpath_1m_gpu.json` |
| the 7 November build | no, history only | +0.1216 (+0.1159 to +0.1272; 5/5) | +0.1186 (+0.1130 to +0.1243; 5/5) | `G2_B_kfold@diag_gnnpath_1m_release.json`, `G3_B_kfold@diag_gnnpath_1m_release.json`, `G0_B_kfold@diag_gnnpath_1m_gpu.json` |
| the 4 November build | no, history only | +0.1316 (+0.1270 to +0.1362; 5/5) | +0.1302 (+0.1266 to +0.1339; 5/5) | `G2_B_kfold@diag_gnnpath_1m.json`, `G3_B_kfold@diag_gnnpath_1m_gpu.json`, `G0_B_kfold@diag_gnnpath_1m_gpu.json` |
<!-- headline-history:end -->

- The base in every row is Pharos alone run on the same GPU as the enriched graphs (`G0_B_kfold@diag_gnnpath_1m_gpu.json`);
  the Mac run of the same graph differs by -0.0001. E008 above reports the 4 November G2 against the Mac base (+0.1315).
- The intervals are recomputed by `pipelines/linkpred/compare.py` (`paired_delta`, t-interval over the 5 folds) and agree
  with the ledger rows and `outputs/linkpred/compare_cs9_gnnpath.md`. Run it in the project environment
  (`pip install -r requirements.txt`): without scipy the t quantile is approximated and the fourth decimal can move.
- Three controls were run once, on the 4 November build, and were not repeated on the shipped graph: the plain R-GCN of the
  frozen protocol (`frozen_a`), RotatE with its random-pruning control (E006, E009) and the MONDO control (E008 control,
  +0.033 without MONDO against +0.132 with it). The headline names that build wherever it uses them.
- Pruning on the shipped graph (G3 minus G2, GNN + paths): the headline states it from
  `G3_B_kfold@diag_gnnpath_1m_cs9.json` minus `G2_B_kfold@diag_gnnpath_1m_cs9.json`; on the 7 November build it was -0.0030
  (-0.0044 to -0.0015).

