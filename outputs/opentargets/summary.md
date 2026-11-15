# opentargets graph (source version 26.09 (direct associations, score >= 0.1))

143,540 nodes, 1,034,552 edges.

## Nodes by class

| class | nodes |
|---|---|
| biolink:Gene | 78,733 |
| biolink:Disease | 45,862 |
| biolink:SmallMolecule | 11,970 |
| biolink:PhenotypicFeature | 6,975 |

## Edges by predicate and primary knowledge source

| predicate | primary knowledge source | edges | source rows |
|---|---|---|---|
| biolink:directly_physically_interacts_with | infores:opentargets | 14,605 | 14,718 |
| biolink:gene_associated_with_condition | infores:opentargets | 769,960 | 770,033 |
| biolink:has_phenotype | infores:opentargets | 163,544 | 163,973 |
| biolink:treats_or_applied_or_studied_to_treat | infores:opentargets | 86,443 | 86,451 |

## Source rows not in the graph (identifier could not be standardised)

| edge spec | reason | rows |
|---|---|---|
| ot_drug_mechanism | no usable object identifier | 885 |

## Source records that list several values (exploded: one edge per value)

| edge spec | column | records | values in them |
|---|---|---|---|
| ot_drug_mechanism | chemblIds | 1,294 | 2,718 |
| ot_drug_mechanism | targets | 1,187 | 7,777 |

## Problems

- biolink:Disease: prefixes not listed by BioLink 4.4.6: ['GO', 'GSSO', 'OBA', 'OBI', 'OGMS', 'OTAR', 'Orphanet', 'PATO', 'UBERON']
