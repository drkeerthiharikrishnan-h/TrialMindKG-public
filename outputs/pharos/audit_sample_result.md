# Independent audit sample: result (machine raters only)

Separate from the pre-registered 200 rows and never mixed into their rule (rule file, addition A7). Label = majority of three verdict-blind raters; `cannot tell` when all three differ. No pass rule.

| pruner verdict | rows | unsupported (95% interval) | supported | cannot tell |
|---|---|---|---|---|
| prune | 500 | 459 = 91.8% (89.1% to 93.9%) | 30 | 11 |
| keep | 500 | 275 = 55.0% (50.6% to 59.3%) | 216 | 9 |

## Agreement between raters

| raters | rows | agree | Cohen's kappa |
|---|---|---|---|
| A and B | 1000 | 912 (91.2%) | 0.76 |
| A and C | 1000 | 956 (95.6%) | 0.90 |
| B and C | 1000 | 878 (87.8%) | 0.69 |
| all three | 1000 | 874 (87.4%) | n/a |

Rows on which all three differ (labelled cannot tell): 2.

Raters: A = `nvidia/nemotron-3.5-super-vl-120b-a12b`; B = `nvidia/nemotron-3.5-lightning-30b-a3b`; C = `mistralai/mistral-large-4-1t-a52b`.

## Per source (majority labels)

| pruner verdict | source | rows | unsupported | share (95% interval) | supported | cannot tell |
|---|---|---|---|---|---|---|
| prune | STRINGDB (infores:string) | 416 | 410 | 98.6% (96.9% to 99.3%) | 6 | 0 |
| prune | mock (infores:string) | 32 | 31 | 96.9% (84.3% to 99.4%) | 1 | 0 |
| prune | TIN-X (infores:pharos) | 21 | 10 | 47.6% (28.3% to 67.6%) | 11 | 0 |
| prune | BioPlex (infores:bioplex) | 8 | 4 | 50.0% (21.5% to 78.5%) | 0 | 4 |
| prune | Gene Ontology annotation (infores:goa) | 8 | 3 | 37.5% (13.7% to 69.4%) | 4 | 1 |
| prune | Pharos ligand activity (infores:chembl) | 7 | 0 | 0.0% (0.0% to 35.4%) | 7 | 0 |
| prune | BioPlex,STRINGDB (infores:string) | 6 | 0 | 0.0% (0.0% to 39.0%) | 0 | 6 |
| prune | IMPC (infores:pharos) | 1 | 0 | 0.0% (0.0% to 79.3%) | 1 | 0 |
| prune | Reactome,STRINGDB (infores:string) | 1 | 1 | 100.0% (20.7% to 100.0%) | 0 | 0 |
| keep | TIN-X (infores:pharos) | 251 | 61 | 24.3% (19.4% to 30.0%) | 187 | 3 |
| keep | STRINGDB (infores:string) | 215 | 203 | 94.4% (90.5% to 96.8%) | 10 | 2 |
| keep | Pharos ligand activity (infores:chembl) | 9 | 0 | 0.0% (0.0% to 29.9%) | 9 | 0 |
| keep | TIGA (GWAS Catalog) (infores:tiga) | 7 | 5 | 71.4% (35.9% to 91.8%) | 2 | 0 |
| keep | Gene Ontology annotation (infores:goa) | 4 | 3 | 75.0% (30.1% to 95.4%) | 1 | 0 |
| keep | WikiPathways (infores:wikipathways) | 4 | 0 | 0.0% (0.0% to 49.0%) | 0 | 4 |
| keep | BioPlex,STRINGDB (infores:string) | 2 | 0 | 0.0% (0.0% to 65.8%) | 2 | 0 |
| keep | Expression Atlas (infores:pharos) | 2 | 0 | 0.0% (0.0% to 65.8%) | 2 | 0 |
| keep | JAX/MGI Human Ortholog Phenotype (infores:mgi) | 2 | 1 | 50.0% (9.5% to 90.5%) | 1 | 0 |
| keep | JensenLab Experiment TIGA (infores:tiga) | 1 | 1 | 100.0% (20.7% to 100.0%) | 0 | 0 |
| keep | Pharos target-gene mapping (infores:pharos) | 1 | 0 | 0.0% (0.0% to 79.3%) | 1 | 0 |
| keep | Reactome (infores:reactome) | 1 | 1 | 100.0% (20.7% to 100.0%) | 0 | 0 |
| keep | Reactome,STRINGDB (infores:string) | 1 | 0 | 0.0% (0.0% to 79.3%) | 1 | 0 |
