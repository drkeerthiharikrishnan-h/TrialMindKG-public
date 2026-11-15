# Pruning v1 - chembl37

2,014,593 edges scored; 50,000 injected type-preserving errors used for training (half) and evaluation (other half).

- threshold (best F1 on held-out injected set): 0.2916
- precision of 'prune' on the held-out set: 0.204; recall of injected errors: 0.380; F1: 0.265
- AUPRC for finding injected errors: 0.133 (learned signals = plausibility + corroboration: 0.181; random = base rate 0.024)
- share of real chembl37 edges with verdict 'prune': 0.037

Learned weights (logistic regression, real vs injected): plausibility +4.33, corroboration +0.00

Settings: trust weight 0.2, SVD dimensions 32, plausibility by all, curated + corroborated protected: no

AUPRC of each signal alone on the held-out injected errors (trust is expected near the base rate: injected errors keep the original edge's source and score, so this test cannot see it):

| signal | AUPRC |
|---|---|
| plausibility | 0.024 |
| corroboration | 0.036 |
| source trust | 0.024 |
| learned (plausibility + corroboration) | 0.181 |
| combined, trust weight 0.0 | 0.181 |
| combined, trust weight 0.1 | 0.145 |
| combined, trust weight 0.2 | 0.133 |

## By predicate

| predicate | injected errors caught | real edges pruned |
|---|---|---|
| biolink:directly_physically_interacts_with | 0.386 | 0.036 |
| biolink:treats_or_applied_or_studied_to_treat | 0.324 | 0.051 |

Caveats: injected errors are degree-matched and keep the original edge's source and score, so only plausibility and corroboration can expose them; real chembl37 edges include unknown errors (they are treated as 'real' when learning). v2 adds out-of-fold GNN scores, newer-release corroboration and literature checks. The organizers' gold set will give the real accuracy.
