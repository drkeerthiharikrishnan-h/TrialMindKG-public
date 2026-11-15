# Pruning v1.1 - pharos

26,462,802 edges scored; 48,699 injected type-preserving errors used for training (half) and evaluation (other half).

- threshold (best F1 on held-out injected set): 0.3041
- precision of 'prune' on the held-out set: 0.011; recall of injected errors: 0.058; F1: 0.018
- AUPRC for finding injected errors: 0.005 (learned signals = plausibility + corroboration: 0.005; random = base rate 0.002)
- share of real pharos edges with verdict 'prune': 0.010

Learned weights (logistic regression, real vs injected): plausibility +2.75, corroboration +0.60

Settings: trust weight 0.2, SVD dimensions 32, plausibility by all, curated + corroborated protected: yes (35,310 edges; 11 of them would otherwise be pruned)

AUPRC of each signal alone on the held-out injected errors (trust is expected near the base rate: injected errors keep the original edge's source and score, so this test cannot see it):

| signal | AUPRC |
|---|---|
| plausibility | 0.004 |
| corroboration | 0.003 |
| source trust | 0.002 |
| learned (plausibility + corroboration) | 0.005 |
| combined, trust weight 0.0 | 0.005 |
| combined, trust weight 0.1 | 0.005 |
| combined, trust weight 0.2 | 0.005 |

## By predicate

| predicate | injected errors caught | real edges pruned |
|---|---|---|
| biolink:actively_involved_in | 0.202 | 0.008 |
| biolink:capable_of | 0.144 | 0.008 |
| biolink:directly_physically_interacts_with | 0.242 | 0.008 |
| biolink:gene_associated_with_condition | 0.005 | 0.001 |
| biolink:genetically_associated_with | 0.000 | 0.001 |
| biolink:has_gene_product | 0.979 | 0.000 |
| biolink:has_phenotype | 0.000 | 0.000 |
| biolink:located_in | 0.140 | 0.013 |
| biolink:physically_interacts_with | 0.098 | 0.020 |

Caveats: injected errors are degree-matched and keep the original edge's source and score, so only plausibility and corroboration can expose them; real pharos edges include unknown errors (they are treated as 'real' when learning). v2 adds out-of-fold GNN scores, newer-release corroboration and literature checks. The organizers' gold set will give the real accuracy.
