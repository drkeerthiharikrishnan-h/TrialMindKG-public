# Pruning settings compared - pharos (injected errors seed 1)

Every variant scored on the same injected errors; prune_kg.py reports the final numbers on a different set (seed 0).

| variant | AUPRC (combined) | AUPRC (learned) | AUPRC (plausibility) | precision | recall | recall gene-condition | real edges pruned |
|---|---|---|---|---|---|---|---|
| v1 (k 32, trust 0.2, one matrix) | 0.005 | 0.005 | 0.004 | 0.009 | 0.075 | 0.009 | 0.014 |
| trust weight 0 | 0.005 | 0.005 | 0.004 | 0.013 | 0.070 | 0.001 | 0.009 |
| SVD 64 dimensions | 0.004 | 0.005 | 0.004 | 0.008 | 0.103 | 0.014 | 0.024 |
| plausibility per predicate group | 0.005 | 0.007 | 0.004 | 0.010 | 0.055 | 0.014 | 0.010 |
| all three | 0.007 | 0.008 | 0.004 | 0.016 | 0.044 | 0.002 | 0.005 |

Random = base rate 0.002. Highest combined AUPRC: all three (0.007, +0.002 vs v1). Trust alone is invisible to this test (injected errors keep the original source and score), so the trust-weight rows show only how trust interacts with the other signals; whether trust helps on real errors is decided by the owner's spot check.
