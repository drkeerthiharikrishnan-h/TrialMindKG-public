# E006: RotatE graph embedding, Pharos vs enriched vs pruned graph (EXPLORATORY, 2026-11-05)

Pre-registered in `loop/ledger.tsv` (E006) before any run: pass if the paired G2-G0 95% CI is above 0; G0 AUROC < 0.55 =
inconclusive. Frozen benchmark: Task B (gene -> disease), Pharos knowledge_assertion test pairs (digest 24f5864cc07ad723, checked
on every file), 5 folds, degree-matched negatives, 1,000,000 edges per edge type. RotatE from torch_geometric.nn.kge, trained on
an NVIDIA L40S (Brev) in NVIDIA's NGC PyG container. Two sizes: dim 256 (100 epochs) and dim 512 (up to 300 epochs), both with a
45-minute cap per fold. Paired differences per fold (`linkpred.compare.paired_delta`), 95% CI over 5 folds.

| RotatE | G0 Pharos | G2 enriched | G3 enriched + pruned | G2-G0 (primary) | G3-G0 | G3-G2 (effect of pruning) |
|---|---|---|---|---|---|---|
| dim 256 | 0.6281 | 0.6325 | 0.6363 | +0.0044 (CI -0.0010 to +0.0098), 4/5 | +0.0082 (CI +0.0034 to +0.0130), 5/5 | +0.0038 (CI +0.0007 to +0.0068), 5/5 |
| dim 512 | 0.6335 | 0.6323 | 0.6501 | -0.0012 (CI -0.0041 to +0.0016), 2/5 | +0.0166 (CI +0.0088 to +0.0244), 5/5 | +0.0178 (CI +0.0124 to +0.0233), 5/5 |

Reference on the same G0 test set: frozen R-GCN 0.5866 (E003); meta-path model 0.6772 (E003).

## Verdict

- **Pre-registered primary test (G2 > G0): NOT passed** at either size. Adding sources alone does not improve this embedding model.
- G0 AUROC is 0.63 at both sizes (> 0.55), so the runs are valid; RotatE beats the frozen R-GCN on Pharos alone.
- **Secondary (not the pre-registered test, report as such): the pruned graph G3 beats both G0 and G2 at both sizes**, 5/5 folds
  each, CIs above 0. Pruning adds +0.004 (dim 256) and +0.018 (dim 512) AUROC over the same enriched graph unpruned.
- Plain-language reading: for a graph-embedding model, *enrichment helps only after pruning*; the pruned graph is the better
  input. Effects are small (under 0.02 AUROC) and this is a ranking benchmark, not clinical accuracy.

## Caveats

- One seed per configuration; 5 paired folds. Time cap of 45 min per fold (dim 512 may not reach 300 epochs).
- Secondary comparisons were planned (the graphs G0/G2/G3 were pre-registered) but the pass rule was stated for G2-G0 only.
- `settings.kge` was written by kge.py itself at the end of each run (copies taken mid-run lacked it); the run markers show a stray earlier invocation of the dim-512 step exiting 1 at 08:00 UTC while the real run
  finished with exit 0 at 15:12 UTC (log has no errors; all folds present).

Files: `G{0,2,3}_B_kfold@diag_kge_rotate_{d256,gpu}.json`, logs `logs/gpu_kge_{d256,gpu512}.log`.
