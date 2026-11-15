# Paired comparison against G0@frozen_a_g0_1m (task B, split kfold, auroc_metapath)

Each variant minus the base on the same folds and seeds (same test pairs when the digests match).

| Variant | paired folds | mean difference | 95% CI | folds higher | same test set |
|---|---|---|---|---|---|
| G2@diag_cs9_metapath | 5 | +0.1136 | +0.1071 to +0.1201 | 5 | yes |
| G2@diag_release_metapath | 5 | +0.1131 | +0.1083 to +0.1178 | 5 | yes |
| G3@diag_cs9_metapath | 5 | +0.1132 | +0.1063 to +0.1200 | 5 | yes |
| G3@diag_release_metapath | 5 | +0.1103 | +0.1050 to +0.1155 | 5 | yes |

- G2@diag_cs9_metapath scores higher than G0@frozen_a_g0_1m: +0.1136 auroc_metapath (95% CI +0.1071 to +0.1201; 5 of 5 folds higher).
- G2@diag_release_metapath scores higher than G0@frozen_a_g0_1m: +0.1131 auroc_metapath (95% CI +0.1083 to +0.1178; 5 of 5 folds higher).
- G3@diag_cs9_metapath scores higher than G0@frozen_a_g0_1m: +0.1132 auroc_metapath (95% CI +0.1063 to +0.1200; 5 of 5 folds higher).
- G3@diag_release_metapath scores higher than G0@frozen_a_g0_1m: +0.1103 auroc_metapath (95% CI +0.1050 to +0.1155; 5 of 5 folds higher).
