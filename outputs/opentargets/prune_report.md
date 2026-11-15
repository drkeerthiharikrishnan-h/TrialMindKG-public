# Pruning v1 - opentargets

1,034,552 edges scored; 34,485 injected type-preserving errors used for training (half) and evaluation (other half).

- threshold (best F1 on held-out injected set): 0.3302
- precision of 'prune' on the held-out set: 0.140; recall of injected errors: 0.281; F1: 0.187
- AUPRC for finding injected errors: 0.098 (learned signals = plausibility + corroboration: 0.108; random = base rate 0.032)
- share of real opentargets edges with verdict 'prune': 0.058

Learned weights (logistic regression, real vs injected): plausibility +2.82, corroboration +0.00

Settings: trust weight 0.2, SVD dimensions 32, plausibility by all, curated + corroborated protected: no

AUPRC of each signal alone on the held-out injected errors (trust is expected near the base rate: injected errors keep the original edge's source and score, so this test cannot see it):

| signal | AUPRC |
|---|---|
| plausibility | 0.078 |
| corroboration | 0.048 |
| source trust | 0.032 |
| learned (plausibility + corroboration) | 0.108 |
| combined, trust weight 0.0 | 0.107 |
| combined, trust weight 0.1 | 0.103 |
| combined, trust weight 0.2 | 0.098 |

## By predicate

| predicate | injected errors caught | real edges pruned |
|---|---|---|
| biolink:directly_physically_interacts_with | 0.487 | 0.009 |
| biolink:gene_associated_with_condition | 0.341 | 0.070 |
| biolink:has_phenotype | 0.023 | 0.016 |
| biolink:treats_or_applied_or_studied_to_treat | 0.225 | 0.035 |

Caveats: injected errors are degree-matched and keep the original edge's source and score, so only plausibility and corroboration can expose them; real opentargets edges include unknown errors (they are treated as 'real' when learning). v2 adds out-of-fold GNN scores, newer-release corroboration and literature checks. The organizers' gold set will give the real accuracy.
