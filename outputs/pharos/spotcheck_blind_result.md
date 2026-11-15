# Blinded spot check: result (Amendment 1)

Rule (pre-registered): PASS if >= 60 of the pruned edges are judged unsupported AND <= 15 of the kept edges are judged unsupported. Applied to the final label of each row (rule file, addition A6).

- Pruned edges judged unsupported: **71 of 100** (threshold >= 60)
- Kept edges judged unsupported: **56 of 100** (threshold <= 15)
- Result: **FAIL**

| pruner verdict | unsupported | supported | cannot tell |
|---|---|---|---|
| prune | 71 | 26 | 3 |
| keep | 56 | 38 | 6 |

## How each final label was reached

- the owner's blind label (her 40 rows): 39 rows
- all three raters agree: 136 rows
- flagged row, adjudicated by the owner: 23 rows
- flagged row, not adjudicated: majority label: 2 rows
- flagged row, not adjudicated, no majority: cannot tell: 0 rows

## Agreement (does not change PASS or FAIL)

| raters | rows | agree | Cohen's kappa |
|---|---|---|---|
| A and B | 200 | 174 (87.0%) | 0.58 |
| A and C | 200 | 193 (96.5%) | 0.91 |
| B and C | 200 | 169 (84.5%) | 0.54 |
| all three | 200 | 168 (84.0%) | n/a |

Raters: A = `nvidia/nemotron-3.5-super-vl-120b-a12b`; B = `nvidia/nemotron-3.5-lightning-30b-a3b`; C = `mistralai/mistral-large-4-1t-a52b`.

Owner's blind labels against the machine label (majority of the three raters) on the rows she rated: 7 of 39 agree (17.9%), Cohen's kappa -0.04.
The machine labels were NOT checked against a blind human subset to the pre-registered level (32 of 40 agreeing rows).

## Per source (final labels)

| pruner verdict | source | rows | unsupported | share (95% interval) | supported | cannot tell |
|---|---|---|---|---|---|---|
| prune | STRINGDB (infores:string) | 79 | 63 | 79.7% (69.6% to 87.1%) | 15 | 1 |
| prune | mock (infores:string) | 6 | 4 | 66.7% (30.0% to 90.3%) | 2 | 0 |
| prune | BioPlex (infores:bioplex) | 4 | 1 | 25.0% (4.6% to 69.9%) | 3 | 0 |
| prune | Gene Ontology annotation (infores:goa) | 3 | 1 | 33.3% (6.1% to 79.2%) | 2 | 0 |
| prune | TIN-X (infores:pharos) | 3 | 1 | 33.3% (6.1% to 79.2%) | 1 | 1 |
| prune | BioPlex,STRINGDB (infores:string) | 2 | 1 | 50.0% (9.5% to 90.5%) | 1 | 0 |
| prune | DisGeNET (infores:disgenet) | 1 | 0 | 0.0% (0.0% to 79.3%) | 1 | 0 |
| prune | IMPC (infores:pharos) | 1 | 0 | 0.0% (0.0% to 79.3%) | 0 | 1 |
| prune | Pharos ligand activity (infores:chembl) | 1 | 0 | 0.0% (0.0% to 79.3%) | 1 | 0 |
| keep | TIN-X (infores:pharos) | 50 | 21 | 42.0% (29.4% to 55.8%) | 24 | 5 |
| keep | STRINGDB (infores:string) | 43 | 33 | 76.7% (62.3% to 86.8%) | 10 | 0 |
| keep | Gene Ontology annotation (infores:goa) | 2 | 0 | 0.0% (0.0% to 65.8%) | 2 | 0 |
| keep | IMPC (infores:pharos) | 1 | 0 | 0.0% (0.0% to 79.3%) | 1 | 0 |
| keep | Pharos ligand activity (infores:chembl) | 1 | 0 | 0.0% (0.0% to 79.3%) | 1 | 0 |
| keep | Pharos target-gene mapping (infores:pharos) | 1 | 1 | 100.0% (20.7% to 100.0%) | 0 | 0 |
| keep | TIGA (GWAS Catalog) (infores:tiga) | 1 | 1 | 100.0% (20.7% to 100.0%) | 0 | 0 |
| keep | WikiPathways (infores:wikipathways) | 1 | 0 | 0.0% (0.0% to 79.3%) | 0 | 1 |
