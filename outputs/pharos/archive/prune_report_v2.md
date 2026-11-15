# CANDIDATE pruner v2 (trust 0, SVD 64, plausibility per predicate group) - not shipped

# Pruning v1.1 - pharos (v2 candidate)

26,462,915 edges scored; 48,705 injected type-preserving errors used for training (half) and evaluation (other half).

- threshold (best F1 on held-out injected set): 0.2165
- precision of 'prune' on the held-out set: 0.016; recall of injected errors: 0.043; F1: 0.023
- AUPRC for finding injected errors: 0.007 (learned signals = plausibility + corroboration: 0.008; random = base rate 0.002)
- share of real pharos (v2 candidate) edges with verdict 'prune': 0.005

Learned weights (logistic regression, real vs injected): plausibility +2.79, corroboration +0.65

Settings: trust weight 0, SVD dimensions 64, plausibility by predicate_group, curated + corroborated protected: yes (35,285 edges; 34 of them would otherwise be pruned)

AUPRC of each signal alone on the held-out injected errors (trust is expected near the base rate: injected errors keep the original edge's source and score, so this test cannot see it):

| signal | AUPRC |
|---|---|
| plausibility | 0.004 |
| corroboration | 0.003 |
| source trust | 0.002 |
| learned (plausibility + corroboration) | 0.008 |
| combined, trust weight 0.0 | 0.007 |
| combined, trust weight 0.1 | 0.005 |
| combined, trust weight 0.2 | 0.004 |

## By predicate

| predicate | injected errors caught | real edges pruned |
|---|---|---|
| biolink:actively_involved_in | 0.522 | 0.051 |
| biolink:capable_of | 0.475 | 0.030 |
| biolink:directly_physically_interacts_with | 0.671 | 0.013 |
| biolink:gene_associated_with_condition | 0.003 | 0.001 |
| biolink:genetically_associated_with | 0.027 | 0.003 |
| biolink:has_gene_product | 0.000 | 0.000 |
| biolink:has_phenotype | 0.149 | 0.030 |
| biolink:located_in | 0.328 | 0.042 |
| biolink:physically_interacts_with | 0.044 | 0.007 |

Caveats: injected errors are degree-matched and keep the original edge's source and score, so only plausibility and corroboration can expose them; real pharos (v2 candidate) edges include unknown errors (they are treated as 'real' when learning). v2 adds out-of-fold GNN scores, newer-release corroboration and literature checks. The organizers' gold set will give the real accuracy.
