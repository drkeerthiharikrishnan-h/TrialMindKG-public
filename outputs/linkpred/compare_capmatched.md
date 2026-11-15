# Cap-matched comparison (run A + G0 at the 1M cap), 2026-11-04

Frozen protocol, task B, 5 folds, test digest `24f5864cc07ad723`. All four files use identical settings except the edge cap. New file: `G0_B_kfold@frozen_a_g0_1m.json` (G0 at 1M, run 15:48-17:00 IST). Paired fold-by-fold differences, 95% t-interval. Computed with `pipelines.linkpred.compare.paired_delta` (`compare.md` from run A left untouched).

## Means

| graph (cap) | GNN AUROC | meta-path AUROC | test pairs with a path | common-neighbour AUROC |
|---|---|---|---|---|
| G0@1M (frozen_a_g0_1m) | 0.5866 | 0.6772 | 87.48% | 0.5000 |
| G0@2M (frozen_a) | 0.5864 | 0.6859 | 87.49% | 0.5000 |
| G2@1M (frozen_a) | 0.5821 | 0.7677 | 99.31% | 0.6498 |
| G3@1M (frozen_a) | 0.5820 | 0.7670 | 99.29% | 0.6494 |

## Paired differences (variant - base)

| metric | comparison | mean | 95% CI | folds higher | same test set |
|---|---|---|---|---|---|
| GNN AUROC | G2@1M - G0@1M | -0.0045 | -0.0064 to -0.0026 | 0/5 | True |
| GNN AUROC | G3@1M - G0@1M | -0.0046 | -0.0091 to -0.0001 | 0/5 | True |
| GNN AUROC | G0@1M - G0@2M | +0.0002 | -0.0006 to +0.0009 | 3/5 | True |
| meta-path AUROC | G2@1M - G0@1M | +0.0906 | +0.0845 to +0.0966 | 5/5 | True |
| meta-path AUROC | G3@1M - G0@1M | +0.0898 | +0.0838 to +0.0958 | 5/5 | True |
| meta-path AUROC | G0@1M - G0@2M | -0.0088 | -0.0099 to -0.0076 | 0/5 | True |
| path coverage | G2@1M - G0@1M | +0.1184 | +0.1164 to +0.1204 | 5/5 | True |
| path coverage | G3@1M - G0@1M | +0.1182 | +0.1160 to +0.1203 | 5/5 | True |
| path coverage | G0@1M - G0@2M | -0.0001 | -0.0002 to +0.0000 | 0/5 | True |
| common-neighbour AUROC | G2@1M - G0@1M | +0.1498 | +0.1450 to +0.1546 | 5/5 | True |
| common-neighbour AUROC | G3@1M - G0@1M | +0.1494 | +0.1451 to +0.1536 | 5/5 | True |
| common-neighbour AUROC | G0@1M - G0@2M | +0.0000 | +0.0000 to +0.0000 | 0/5 | True |

## Reading

- **Cap confound resolved.** GNN: enrichment still does not help when caps match (G2 - G0 = -0.0045, CI excludes 0, 0/5 folds); the cap itself changes G0's GNN by +0.0002.
- Meta-path: the cap-matched gain is **+0.0906** (5/5 folds), larger than run A's +0.0818, because the 1M cap lowers G0's meta-path score by -0.0088. The cap does not explain the gain.
- Still open (exploratory, ledger E004-E006): path coverage rises by ~11.8 points and the common-neighbour baseline by ~0.15 on G2, so coverage and source reuse remain candidate explanations. Note: run A's G2/G3 graphs were overwritten by the 2026-11-04 09:20 rebuild; these numbers come from their committed result files.
