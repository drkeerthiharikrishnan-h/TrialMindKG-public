# G0 (pharos)

432,380 nodes, 26,462,915 edges; 0 edges removed by pruning; 0 edge rows not used (duplicate id across layers or endpoint without a node row); 0 node rows re-typed to one class per id prefix.

| layer | edges |
|---|---|
| pharos | 26,462,915 |

Layers missing (not built yet): none
Layers kept whole (no verdict file): none

## PyG HeteroData summary

### Node types
- BiologicalProcess: 11,996 nodes
- CellularComponent: 1,721 nodes
- Disease: 12,113 nodes
- DiseaseOrPhenotypicFeature: 2,056 nodes
- Gene: 20,037 nodes
- MolecularActivity: 4,186 nodes
- Pathway: 3,109 nodes
- PhenotypicFeature: 846 nodes
- Protein: 20,412 nodes
- SmallMolecule: 355,904 nodes

### Edge types
- ('Gene', 'gene_associated_with_condition', 'Disease'): 13,415,737 edges
- ('Gene', 'genetically_associated_with', 'Disease'): 12,292 edges
- ('Gene', 'genetically_associated_with', 'DiseaseOrPhenotypicFeature'): 266,529 edges
- ('Gene', 'genetically_associated_with', 'PhenotypicFeature'): 2,170 edges
- ('Gene', 'has_gene_product', 'Protein'): 20,190 edges
- ('Gene', 'has_phenotype', 'PhenotypicFeature'): 87,311 edges
- ('Protein', 'actively_involved_in', 'BiologicalProcess'): 126,465 edges
- ('Protein', 'actively_involved_in', 'Pathway'): 232,931 edges
- ('Protein', 'capable_of', 'MolecularActivity'): 54,547 edges
- ('Protein', 'located_in', 'CellularComponent'): 77,707 edges
- ('Protein', 'physically_interacts_with', 'Protein'): 11,691,825 edges
- ('SmallMolecule', 'directly_physically_interacts_with', 'Protein'): 475,211 edges

**Total: 432,380 nodes, 26,462,915 edges, 10 node types, 12 edge types.**


Note (CS-2, 8 Nov): summary regenerated with make_graphs.assemble + summary_markdown; the PyG section summarises the existing outputs/g0/heterodata.pt (not rewritten). The link-prediction benchmark drops the 13,118,763 text co-occurrence edges of this graph at run time (--drop-levels text_co_occurrence), so the benchmark's G0 is Pharos without text mining: 13,344,152 edges.
