# chembl37 graph (source version 37)

1,091,746 nodes, 2,014,593 edges.

## Nodes by class

| class | nodes |
|---|---|
| biolink:SmallMolecule | 1,084,086 |
| biolink:Protein | 5,194 |
| biolink:Disease | 2,191 |
| biolink:PhenotypicFeature | 275 |

## Edges by predicate and primary knowledge source

| predicate | primary knowledge source | edges | source rows |
|---|---|---|---|
| biolink:directly_physically_interacts_with | infores:chembl | 1,955,981 | 1,956,061 |
| biolink:treats_or_applied_or_studied_to_treat | infores:chembl | 58,612 | 59,315 |

## Source rows not in the graph (identifier could not be standardised)

| edge spec | reason | rows |
|---|---|---|
| chembl_indication | no usable object identifier | 156 |
| chembl_indication | object prefix has no class | 584 |

## How object identifiers were mapped (route per source row; first route that gave an id wins)

| edge spec | route | source rows | distinct objects |
|---|---|---|---|
| chembl_indication | as_given | 21,954 | 1,258 |
| chembl_indication | efo_to_mondo | 35,278 | 1,120 |
| chembl_indication | mesh_to_mondo | 2,083 | 187 |
| chembl_indication | fell through: key in skip_keys | 78 | 5 |

## Problems

None.
