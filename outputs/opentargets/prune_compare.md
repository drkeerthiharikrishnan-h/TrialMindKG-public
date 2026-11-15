# Pruning settings compared - opentargets (injected errors seed 1)

Every variant scored on the same injected errors; prune_kg.py reports the final numbers on a different set (seed 0).

| variant | AUPRC (combined) | AUPRC (learned) | AUPRC (plausibility) | precision | recall | recall gene-condition | real edges pruned |
|---|---|---|---|---|---|---|---|
| v1 (k 32, trust 0.2, one matrix) | 0.095 | 0.104 | 0.075 | 0.137 | 0.276 | 0.337 | 0.058 |
| trust weight 0 | 0.104 | 0.104 | 0.075 | 0.143 | 0.289 | 0.340 | 0.057 |
| SVD 64 dimensions | 0.104 | 0.121 | 0.084 | 0.141 | 0.306 | 0.350 | 0.062 |
| plausibility per predicate group | 0.126 | 0.153 | 0.090 | 0.174 | 0.324 | 0.296 | 0.051 |
| all three | 0.154 | 0.154 | 0.091 | 0.187 | 0.290 | 0.236 | 0.042 |

Random = base rate 0.032. Highest combined AUPRC: all three (0.154, +0.059 vs v1). Trust alone is invisible to this test (injected errors keep the original source and score), so the trust-weight rows show only how trust interacts with the other signals; whether trust helps on real errors is decided by the owner's spot check.
