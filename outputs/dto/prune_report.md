# Pruning v1 - dto

33,838 edges scored; 1,127 injected type-preserving errors used for training (half) and evaluation (other half).

- threshold (best F1 on held-out injected set): 0.2184
- precision of 'prune' on the held-out set: 0.979; recall of injected errors: 0.920; F1: 0.949
- AUPRC for finding injected errors: 0.942 (learned signals = plausibility + corroboration: 0.942; random = base rate 0.032)
- share of real dto edges with verdict 'prune': 0.000

Learned weights (logistic regression, real vs injected): plausibility +7.09, corroboration +0.00

Settings: trust weight 0.2, SVD dimensions 32, plausibility by all, curated + corroborated protected: no

AUPRC of each signal alone on the held-out injected errors (trust is expected near the base rate: injected errors keep the original edge's source and score, so this test cannot see it):

| signal | AUPRC |
|---|---|
| plausibility | 0.061 |
| corroboration | 0.048 |
| source trust | 0.048 |
| learned (plausibility + corroboration) | 0.942 |
| combined, trust weight 0.0 | 0.942 |
| combined, trust weight 0.1 | 0.942 |
| combined, trust weight 0.2 | 0.942 |

## By predicate

| predicate | injected errors caught | real edges pruned |
|---|---|---|
| biolink:member_of | 0.917 | 0.000 |

Caveats: injected errors are degree-matched and keep the original edge's source and score, so only plausibility and corroboration can expose them; real dto edges include unknown errors (they are treated as 'real' when learning). v2 adds out-of-fold GNN scores, newer-release corroboration and literature checks. The organizers' gold set will give the real accuracy.
