# pharos graph (source version pharos319 (Pharos 3.19.2 / TCRD 6.13.5))

432,070 nodes, 26,462,802 edges.

## Nodes by class

| class | nodes |
|---|---|
| biolink:SmallMolecule | 355,904 |
| biolink:Protein | 20,412 |
| biolink:Gene | 20,037 |
| biolink:Disease | 12,146 |
| biolink:BiologicalProcess | 11,996 |
| biolink:MolecularActivity | 4,186 |
| biolink:Pathway | 3,109 |
| biolink:CellularComponent | 1,721 |
| biolink:DiseaseOrPhenotypicFeature | 1,713 |
| biolink:PhenotypicFeature | 846 |

## Edges by predicate and primary knowledge source

| predicate | primary knowledge source | edges | source rows |
|---|---|---|---|
| biolink:actively_involved_in | infores:goa | 126,465 | 126,465 |
| biolink:actively_involved_in | infores:kegg | 32,325 | 32,325 |
| biolink:actively_involved_in | infores:reactome | 110,872 | 110,872 |
| biolink:actively_involved_in | infores:wikipathways | 89,734 | 89,734 |
| biolink:capable_of | infores:goa | 54,547 | 54,547 |
| biolink:directly_physically_interacts_with | infores:chembl | 473,777 | 633,954 |
| biolink:directly_physically_interacts_with | infores:drugcentral | 1,197 | 1,215 |
| biolink:directly_physically_interacts_with | infores:gtopdb | 237 | 237 |
| biolink:gene_associated_with_condition | infores:amyco | 188 | 193 |
| biolink:gene_associated_with_condition | infores:ctd | 27,157 | 28,703 |
| biolink:gene_associated_with_condition | infores:diseases | 6,980 | 7,204 |
| biolink:gene_associated_with_condition | infores:disgenet | 49,172 | 51,806 |
| biolink:gene_associated_with_condition | infores:drugcentral | 3,687 | 6,525 |
| biolink:gene_associated_with_condition | infores:eram | 13,255 | 14,499 |
| biolink:gene_associated_with_condition | infores:medlineplus | 3,678 | 4,101 |
| biolink:gene_associated_with_condition | infores:monarchinitiative | 8,701 | 9,341 |
| biolink:gene_associated_with_condition | infores:pharos | 13,270,490 | 13,673,104 |
| biolink:gene_associated_with_condition | infores:tiga | 23,591 | 26,257 |
| biolink:gene_associated_with_condition | infores:uniprot | 8,838 | 8,952 |
| biolink:genetically_associated_with | infores:tiga | 280,878 | 426,147 |
| biolink:has_gene_product | infores:pharos | 20,190 | 20,190 |
| biolink:has_phenotype | infores:mgi | 57,884 | 58,250 |
| biolink:has_phenotype | infores:pharos | 29,427 | 71,876 |
| biolink:located_in | infores:goa | 77,707 | 77,707 |
| biolink:physically_interacts_with | infores:bioplex | 70,262 | 70,262 |
| biolink:physically_interacts_with | infores:reactome | 1,896 | 1,896 |
| biolink:physically_interacts_with | infores:string | 11,619,667 | 11,619,667 |

## Source rows not in the graph (identifier could not be standardised)

| edge spec | reason | rows |
|---|---|---|
| gene_has_product | no subject identifier | 285 |
| gene_disease | no subject identifier | 425 |
| gene_disease | no usable object identifier | 47,376 |
| ligand_activity | no subject identifier | 17,737 |
| gene_mouse_phenotype | no subject identifier | 3 |
| gene_gwas_trait | no subject identifier | 285 |
| gene_gwas_trait | no usable object identifier | 2,122 |
| gene_gwas_trait | object prefix has no class | 2,146 |
| gene_disease_textmining_tinx | no subject identifier | 13,194 |
| gene_disease_textmining_tinx | no usable object identifier | 275,776 |
| protein_protein_interaction | no usable object identifier | 1,247 |

## Source records that list several values (exploded: one edge per value)

| edge spec | column | records | values in them |
|---|---|---|---|
| gene_mouse_phenotype | Term ID | 1 | 2 |

## How object identifiers were mapped (route per source row; first route that gave an id wins)

| edge spec | route | source rows | distinct objects |
|---|---|---|---|
| gene_gwas_trait | as_given | 372,156 | 1,867 |
| gene_gwas_trait | efo_to_mondo | 53,991 | 343 |

## Problems

- biolink:DiseaseOrPhenotypicFeature: prefixes not listed by BioLink 4.4.6: ['EFO']
- biolink:PhenotypicFeature: prefixes not listed by BioLink 4.4.6: ['MPATH']
