# Diagnostic: meta-path model on the rebuilt (submission) G2/G3, 2026-11-04 (EXPLORATORY)

Frozen settings except `--epochs 0` (no GNN training; meta-path and baseline AUROCs do not depend on it; the GNN `auroc` field of these files is meaningless). Cap 1M. Graphs = current `outputs/g2|g3/heterodata.pt`, rebuilt 2026-11-04 09:20-09:22 after the Open Targets layer was limited to score >= 0.1. Test digest `24f5864cc07ad723` (same test pairs as run A). Files: `G2|G3_B_kfold@diag_rebuilt_1m.json`, log `logs/linkpred_diag_rebuilt_1m.log` (21:49-22:14).

## Means

| graph | meta-path AUROC | test pairs with a path | common-neighbour | Adamic-Adar | path3 |
|---|---|---|---|---|---|
| G0@1M (frozen_a_g0_1m) | 0.6772 | 87.48% | 0.5000 | 0.5000 | 0.5288 |
| G2 run A @1M (old OT layer) | 0.7677 | 99.31% | 0.6498 | 0.6562 | 0.5297 |
| G2 rebuilt @1M | 0.7986 | 99.33% | 0.7300 | 0.7441 | 0.5390 |
| G3 rebuilt @1M | 0.7978 | 99.29% | 0.7306 | 0.7433 | 0.5405 |

## Paired differences vs G0@1M (5 folds, 95% t-interval)

| comparison | meta-path | common-neighbour |
|---|---|---|
| G2 rebuilt - G0@1M | +0.1214 (+0.1181 to +0.1247, 5/5) | +0.2300 (+0.2284 to +0.2315, 5/5) |
| G3 rebuilt - G0@1M | +0.1207 (+0.1169 to +0.1244, 5/5) | +0.2306 (+0.2294 to +0.2319, 5/5) |
| G2 rebuilt - G2 run A | +0.0309 (+0.0220 to +0.0397, 5/5) | +0.0801 (+0.0766 to +0.0836, 5/5) |

## Reading (exploratory; the frozen primary comparison is unchanged)

- The meta-path gain replicates and grows on the submission graphs (G2 +0.1214, G3 +0.1207 vs G0@1M, 5/5 folds).
- But the plain common-neighbour score grows about twice as much (G2 +0.2300). A heuristic that only counts shared neighbours rising this far means the enriched graph contains evidence that overlaps heavily with the held-out curated pairs. **Source reuse is now the leading explanation of the gain**; it is not established that the enrichment adds new knowledge.
- The best single meta-path shape on the rebuilt G2 goes through the `gene_associated_with_condition_context` relation (the weaker-evidence gene-disease associations kept as context) and then a disease `subclass_of` hop (see log). Leak removal deletes the exact held-out pair, but a parent/child disease of the same gene is not removed.
- Next control (E005): drop context/OT edges whose datasource also supplies the Pharos test positives, and/or remove 1-hop disease-ontology neighbours of held-out pairs; rerun with `--epochs 0`. Report the meta-path claim at the Nov 6 freeze only with these numbers next to it.
