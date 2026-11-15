# Paired comparison against G0@diag_gnnpath_1m_gpu (task B, split kfold, auroc_gnnpath)

Each variant minus the base on the same folds and seeds (same test pairs when the digests match).

| Variant | paired folds | mean difference | 95% CI | folds higher | same test set |
|---|---|---|---|---|---|
| G2@diag_gnnpath_1m_cs9 | 5 | +0.1235 | +0.1167 to +0.1304 | 5 | yes |
| G2@diag_gnnpath_1m_release | 5 | +0.1216 | +0.1159 to +0.1272 | 5 | yes |
| G3@diag_gnnpath_1m_cs9 | 5 | +0.1219 | +0.1139 to +0.1299 | 5 | yes |
| G3@diag_gnnpath_1m_release | 5 | +0.1186 | +0.1130 to +0.1243 | 5 | yes |

- G2@diag_gnnpath_1m_cs9 scores higher than G0@diag_gnnpath_1m_gpu: +0.1235 auroc_gnnpath (95% CI +0.1167 to +0.1304; 5 of 5 folds higher).
- G2@diag_gnnpath_1m_release scores higher than G0@diag_gnnpath_1m_gpu: +0.1216 auroc_gnnpath (95% CI +0.1159 to +0.1272; 5 of 5 folds higher).
- G3@diag_gnnpath_1m_cs9 scores higher than G0@diag_gnnpath_1m_gpu: +0.1219 auroc_gnnpath (95% CI +0.1139 to +0.1299; 5 of 5 folds higher).
- G3@diag_gnnpath_1m_release scores higher than G0@diag_gnnpath_1m_gpu: +0.1186 auroc_gnnpath (95% CI +0.1130 to +0.1243; 5 of 5 folds higher).
