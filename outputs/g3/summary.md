# G3 (pharos + dto + opentargets_layer + chembl37 + hpo_hierarchy + go_hierarchy + mondo_hierarchy)

1,324,186 nodes, 29,290,179 edges; 390,500 edges removed by pruning; 0 edge rows not used (duplicate id across layers or endpoint without a node row); 29,925 node rows re-typed to one class per id prefix.

| layer | edges |
|---|---|
| chembl37 | 1,940,824 |
| dto | 33,838 |
| go_hierarchy | 63,726 |
| hpo_hierarchy | 24,436 |
| mondo_hierarchy | 46,732 |
| opentargets_layer | 974,460 |
| pharos | 26,206,163 |

Layers missing (not built yet): none
Layers kept whole (no verdict file): none

## PyG HeteroData summary

### Node types
- BiologicalProcess: 25,506 nodes
- CellularComponent: 4,207 nodes
- Disease: 32,920 nodes
- DiseaseOrPhenotypicFeature: 9,628 nodes
- Gene: 78,882 nodes
- MolecularActivity: 10,447 nodes
- Pathway: 3,109 nodes
- PhenotypicFeature: 38,500 nodes
- Protein: 20,467 nodes
- ProteinFamily: 10,098 nodes
- SmallMolecule: 1,090,422 nodes

### Edge types
- ('BiologicalProcess', 'part_of', 'BiologicalProcess'): 4,461 edges
- ('BiologicalProcess', 'subclass_of', 'BiologicalProcess'): 40,486 edges
- ('CellularComponent', 'part_of', 'CellularComponent'): 1,800 edges
- ('CellularComponent', 'subclass_of', 'CellularComponent'): 4,696 edges
- ('Disease', 'has_phenotype', 'Disease'): 13,979 edges
- ('Disease', 'has_phenotype', 'DiseaseOrPhenotypicFeature'): 602 edges
- ('Disease', 'has_phenotype', 'PhenotypicFeature'): 141,651 edges
- ('Disease', 'subclass_of', 'Disease'): 46,732 edges
- ('DiseaseOrPhenotypicFeature', 'has_phenotype', 'Disease'): 375 edges
- ('DiseaseOrPhenotypicFeature', 'has_phenotype', 'DiseaseOrPhenotypicFeature'): 27 edges
- ('DiseaseOrPhenotypicFeature', 'has_phenotype', 'PhenotypicFeature'): 3,973 edges
- ('Gene', 'gene_associated_with_condition', 'BiologicalProcess'): 1,396 edges
- ('Gene', 'gene_associated_with_condition', 'Disease'): 13,662,437 edges
- ('Gene', 'gene_associated_with_condition', 'DiseaseOrPhenotypicFeature'): 330,625 edges
- ('Gene', 'gene_associated_with_condition', 'PhenotypicFeature'): 126,191 edges
- ('Gene', 'genetically_associated_with', 'Disease'): 35,667 edges
- ('Gene', 'genetically_associated_with', 'DiseaseOrPhenotypicFeature'): 242,752 edges
- ('Gene', 'genetically_associated_with', 'PhenotypicFeature'): 2,168 edges
- ('Gene', 'has_gene_product', 'Protein'): 20,190 edges
- ('Gene', 'has_phenotype', 'PhenotypicFeature'): 87,306 edges
- ('MolecularActivity', 'part_of', 'MolecularActivity'): 9 edges
- ('MolecularActivity', 'subclass_of', 'MolecularActivity'): 12,274 edges
- ('PhenotypicFeature', 'has_phenotype', 'Disease'): 19 edges
- ('PhenotypicFeature', 'has_phenotype', 'DiseaseOrPhenotypicFeature'): 1 edges
- ('PhenotypicFeature', 'has_phenotype', 'PhenotypicFeature'): 246 edges
- ('PhenotypicFeature', 'subclass_of', 'PhenotypicFeature'): 24,436 edges
- ('Protein', 'actively_involved_in', 'BiologicalProcess'): 124,733 edges
- ('Protein', 'actively_involved_in', 'Pathway'): 231,919 edges
- ('Protein', 'capable_of', 'MolecularActivity'): 54,114 edges
- ('Protein', 'located_in', 'CellularComponent'): 76,726 edges
- ('Protein', 'member_of', 'ProteinFamily'): 33,838 edges
- ('Protein', 'physically_interacts_with', 'Protein'): 11,454,040 edges
- ('SmallMolecule', 'directly_physically_interacts_with', 'Gene'): 14,467 edges
- ('SmallMolecule', 'directly_physically_interacts_with', 'Protein'): 2,356,831 edges
- ('SmallMolecule', 'treats_or_applied_or_studied_to_treat', 'BiologicalProcess'): 153 edges
- ('SmallMolecule', 'treats_or_applied_or_studied_to_treat', 'Disease'): 119,666 edges
- ('SmallMolecule', 'treats_or_applied_or_studied_to_treat', 'DiseaseOrPhenotypicFeature'): 9,448 edges
- ('SmallMolecule', 'treats_or_applied_or_studied_to_treat', 'PhenotypicFeature'): 9,745 edges

**Total: 1,324,186 nodes, 29,290,179 edges, 11 node types, 38 edge types.**

