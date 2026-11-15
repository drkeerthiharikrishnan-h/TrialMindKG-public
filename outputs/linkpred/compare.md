# Paired comparisons

## Paired comparison against G0@frozen_a_g0_1m (task B, split kfold, auroc)

_auroc: the GNN: the primary frozen metric._

Each variant minus the base on the same folds and seeds (same test pairs when the digests match).

| Variant | paired folds | mean difference | 95% CI | folds higher | same test set |
|---|---|---|---|---|---|
| G2@frozen_a | 5 | -0.0045 | -0.0064 to -0.0026 | 0 | yes |
| G3@frozen_a | 5 | -0.0046 | -0.0091 to -0.0001 | 0 | yes |

- G2@frozen_a scores lower than G0@frozen_a_g0_1m: -0.0045 auroc (95% CI -0.0064 to -0.0026; 0 of 5 folds higher).
- G3@frozen_a scores lower than G0@frozen_a_g0_1m: -0.0046 auroc (95% CI -0.0091 to -0.0001; 0 of 5 folds higher).

## Paired comparison against G0@frozen_a_g0_1m (task B, split kfold, auroc_metapath)

_auroc_metapath: exploratory until the confound controls are reported (controls.py)._

Each variant minus the base on the same folds and seeds (same test pairs when the digests match).

| Variant | paired folds | mean difference | 95% CI | folds higher | same test set |
|---|---|---|---|---|---|
| G2@frozen_a | 5 | +0.0906 | +0.0845 to +0.0966 | 5 | yes |
| G3@frozen_a | 5 | +0.0898 | +0.0838 to +0.0958 | 5 | yes |

- G2@frozen_a scores higher than G0@frozen_a_g0_1m: +0.0906 auroc_metapath (95% CI +0.0845 to +0.0966; 5 of 5 folds higher).
- G3@frozen_a scores higher than G0@frozen_a_g0_1m: +0.0898 auroc_metapath (95% CI +0.0838 to +0.0958; 5 of 5 folds higher).

## Paired comparison against G0@frozen_a_g0_1m (task B, split kfold, metapath_coverage)

_metapath_coverage: share of test pairs with at least one meta-path; differs between graphs, so read it next to the meta-path AUROC._

Each variant minus the base on the same folds and seeds (same test pairs when the digests match).

| Variant | paired folds | mean difference | 95% CI | folds higher | same test set |
|---|---|---|---|---|---|
| G2@frozen_a | 5 | +0.1184 | +0.1164 to +0.1204 | 5 | yes |
| G3@frozen_a | 5 | +0.1182 | +0.1160 to +0.1203 | 5 | yes |

- G2@frozen_a scores higher than G0@frozen_a_g0_1m: +0.1184 metapath_coverage (95% CI +0.1164 to +0.1204; 5 of 5 folds higher).
- G3@frozen_a scores higher than G0@frozen_a_g0_1m: +0.1182 metapath_coverage (95% CI +0.1160 to +0.1203; 5 of 5 folds higher).
