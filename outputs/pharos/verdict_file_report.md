# Verdict file for the gold set: pharos

27,640,425 source rows; 26,462,802 of 26,462,802 graph edges reached from them. One row per source row, keyed by the source's own identifiers (data/kg/pharos/row_verdicts.tsv.gz).

| table | rows | in graph | with verdict | not in graph (reason: rows) |
|---|---|---|---|---|
| gene_ids | 20,475 | 20,190 | 20,190 | no subject identifier: 285 |
| diseases | 365,103 | 317,302 | 317,302 | no subject identifier: 425; no usable object identifier: 47,376 |
| ligands | 653,143 | 635,406 | 635,406 | no subject identifier: 17,737 |
| go | 260,031 | 258,719 | 258,719 | excluded by the card filter: 1,312 |
| pathways | 266,649 | 232,931 | 232,931 | excluded by the card filter: 33,718 |
| phenotypes | 148,899 | 130,126 | 130,126 | excluded by the card filter: 18,770; no subject identifier: 3 |
| tiga | 430,700 | 426,147 | 426,147 | no subject identifier: 285; no usable object identifier: 2,122; object prefix has no class: 2,146 |
| tinx | 13,802,353 | 13,513,383 | 13,513,383 | no subject identifier: 13,194; no usable object identifier: 275,776 |
| ppi | 11,693,072 | 11,691,825 | 11,691,825 | no usable object identifier: 1,247 |

## Checks

- every graph edge is reached by at least one source row, every in-graph row has a verdict
