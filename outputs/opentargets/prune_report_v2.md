# CANDIDATE pruner v2 (trust 0, SVD 64, plausibility per predicate group) - not shipped

# Pruning v1 - opentargets (v2 candidate)

1,034,552 edges scored; 34,485 injected type-preserving errors used for training (half) and evaluation (other half).

- threshold (best F1 on held-out injected set): 0.2075
- precision of 'prune' on the held-out set: 0.191; recall of injected errors: 0.294; F1: 0.231
- AUPRC for finding injected errors: 0.156 (learned signals = plausibility + corroboration: 0.157; random = base rate 0.032)
- share of real opentargets (v2 candidate) edges with verdict 'prune': 0.042

Learned weights (logistic regression, real vs injected): plausibility +4.11, corroboration +0.00

Settings: trust weight 0, SVD dimensions 64, plausibility by predicate_group, curated + corroborated protected: no

AUPRC of each signal alone on the held-out injected errors (trust is expected near the base rate: injected errors keep the original edge's source and score, so this test cannot see it):

| signal | AUPRC |
|---|---|
| plausibility | 0.092 |
| corroboration | 0.048 |
| source trust | 0.032 |
| learned (plausibility + corroboration) | 0.157 |
| combined, trust weight 0.0 | 0.156 |
| combined, trust weight 0.1 | 0.134 |
| combined, trust weight 0.2 | 0.123 |

## By predicate

| predicate | injected errors caught | real edges pruned |
|---|---|---|
| biolink:directly_physically_interacts_with | 0.863 | 0.011 |
| biolink:gene_associated_with_condition | 0.240 | 0.039 |
| biolink:has_phenotype | 0.375 | 0.043 |
| biolink:treats_or_applied_or_studied_to_treat | 0.535 | 0.072 |

Caveats: injected errors are degree-matched and keep the original edge's source and score, so only plausibility and corroboration can expose them; real opentargets (v2 candidate) edges include unknown errors (they are treated as 'real' when learning). v2 adds out-of-fold GNN scores, newer-release corroboration and literature checks. The organizers' gold set will give the real accuracy.
