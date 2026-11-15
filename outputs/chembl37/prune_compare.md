# Pruning settings compared - chembl37 (injected errors seed 1)

Every variant scored on the same injected errors; prune_kg.py reports the final numbers on a different set (seed 0).

| variant | AUPRC (combined) | AUPRC (learned) | AUPRC (plausibility) | precision | recall | recall gene-condition | real edges pruned |
|---|---|---|---|---|---|---|---|
| v1 (k 32, trust 0.2, one matrix) | 0.135 | 0.186 | 0.024 | 0.205 | 0.382 | - | 0.037 |
| trust weight 0 | 0.186 | 0.186 | 0.024 | 0.260 | 0.323 | - | 0.023 |
| SVD 64 dimensions | 0.175 | 0.235 | 0.025 | 0.233 | 0.433 | - | 0.035 |
| plausibility per predicate group | 0.158 | 0.229 | 0.022 | 0.251 | 0.363 | - | 0.027 |
| all three | 0.461 | 0.461 | 0.027 | 0.449 | 0.556 | - | 0.017 |

Random = base rate 0.024. Highest combined AUPRC: all three (0.461, +0.326 vs v1). Trust alone is invisible to this test (injected errors keep the original source and score), so the trust-weight rows show only how trust interacts with the other signals; whether trust helps on real errors is decided by the owner's spot check.
