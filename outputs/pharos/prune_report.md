# Pruning report - pharos (shipped verdicts, real layer)

Computed from `data/kg/pharos/edge_verdicts.tsv.gz` (the verdicts the graphs were built from; no re-scoring) with `scripts/prune_report_from_verdicts.py`. Knowledge levels from the layer's edges file (same row order, ids checked row by row). Held-out corroboration: Open Targets gene-disease pairs from `data/kg/opentargets/edges.tsv.gz` (the pruner never saw Open Targets). Replaces the 25 Oct report of a 1,650,119-edge build.

- edges scored: **26,462,802**; verdict 'prune': **256,639** (**0.97%**); edges marked protected in the reasons: 35,310
- confidence: mean 0.548; quantiles q0 0.125, q0.01 0.304, q0.05 0.369, q0.1 0.413, q0.25 0.492, q0.5 0.566, q0.75 0.618, q0.9 0.653, q0.99 0.692, q1 0.814

## What the injected-error test says (quoted, not recomputed)

The shipped run's own injected-set numbers were not written to a file. The same v1 settings on this layer, on a separate injected set (seed 1), from `outputs/pharos/prune_compare.md`:

| variant | AUPRC (combined) | AUPRC (learned) | AUPRC (plausibility) | precision | recall | recall gene-condition | real edges pruned |
|---|---|---|---|---|---|---|---|
| v1 (k 32, trust 0.2, one matrix) | 0.005 | 0.005 | 0.004 | 0.009 | 0.075 | 0.009 | 0.014 |

Injected errors are type-preserving object swaps that keep the original edge's source and score, so this test measures graph implausibility and missing corroboration only; it cannot see a wrong edge from a trusted source, and real edges include unknown errors. The Open Targets columns below are the real-graph check.

## Three thresholds: pruned share and Open Targets corroboration

Gene-disease edges only for the corroboration columns ('in OT' = pair asserted by Open Targets at any score; 'OT >= 0.2' = at overall score >= 0.2).

| threshold | confidence cut | edges pruned | share of all edges | share of gene-disease edges | pruned that are text co-occurrence | non-text G-D pruned: n / in OT / OT >= 0.2 | non-text G-D kept: in OT / OT >= 0.2 | TIN-X pruned: n / in OT | TIN-X kept: in OT |
|---|---|---|---|---|---|---|---|---|---|
| shipped verdicts (best-F1 cut on injected errors) | between 0.3490 and 0.3041 | 256,639 | 0.97% | 0.08% | 4.1% | 280 / 0.7% / 0.4% | 17.9% / 14.6% | 10,526 / 0.0% | 1.2% |
| confidence < 5% quantile of all edges | < 0.3695 | 1,303,533 | 4.93% | 0.58% | 5.8% | 1,071 / 4.6% / 2.9% | 18.0% / 14.6% | 76,094 / 0.1% | 1.2% |
| confidence < 10% quantile of all edges | < 0.4133 | 2,623,656 | 9.91% | 1.63% | 8.2% | 3,217 / 5.5% / 3.5% | 18.1% / 14.7% | 215,209 / 0.3% | 1.2% |

The alternative cuts apply the shipped run's rule (exempt predicates and protected edges are never pruned); they are reported, not shipped.

## By knowledge level

| knowledge level | edges | pruned | share pruned |
|---|---|---|---|
| text_co_occurrence | 13,118,763 | 10,526 | 0.08% |
| knowledge_assertion | 12,789,009 | 245,540 | 1.92% |
| statistical_association | 492,603 | 509 | 0.10% |
| not_provided | 62,427 | 64 | 0.10% |

## By predicate

| predicate | edges | pruned | share pruned |
|---|---|---|---|
| biolink:gene_associated_with_condition | 13,415,737 | 10,806 | 0.08% |
| biolink:physically_interacts_with | 11,691,825 | 237,785 | 2.03% |
| biolink:directly_physically_interacts_with | 475,211 | 3,594 | 0.76% |
| biolink:actively_involved_in | 359,396 | 2,744 | 0.76% |
| biolink:genetically_associated_with | 280,878 | 291 | 0.10% |
| biolink:has_phenotype | 87,311 | 5 | 0.01% |
| biolink:located_in | 77,707 | 981 | 1.26% |
| biolink:capable_of | 54,547 | 433 | 0.79% |
| biolink:has_gene_product | 20,190 | 0 | 0.00% |

## By source

| source | edges | pruned | share pruned |
|---|---|---|---|
| TIN-X | 13,111,783 | 10,524 | 0.08% |
| STRINGDB | 11,490,844 | 210,411 | 1.83% |
| Pharos ligand activity | 470,904 | 3,522 | 0.75% |
| TIGA (GWAS Catalog) | 280,878 | 291 | 0.10% |
| Gene Ontology annotation | 258,719 | 3,146 | 1.22% |
| Expression Atlas | 158,707 | 188 | 0.12% |
| Reactome | 112,768 | 466 | 0.41% |
| WikiPathways | 89,734 | 487 | 0.54% |
| BioPlex,STRINGDB | 75,080 | 3,264 | 4.35% |
| BioPlex | 70,262 | 4,788 | 6.81% |
| JAX/MGI Human Ortholog Phenotype | 57,884 | 1 | 0.00% |
| DisGeNET | 49,172 | 60 | 0.12% |
| KEGG | 32,325 | 59 | 0.18% |
| Reactome,STRINGDB | 32,092 | 157 | 0.49% |
| IMPC | 29,427 | 4 | 0.01% |
| CTD | 27,157 | 0 | 0.00% |
| JensenLab Experiment TIGA | 23,591 | 26 | 0.11% |
| Pharos target-gene mapping | 20,190 | 0 | 0.00% |
| mock | 19,165 | 19,165 | 100.00% |
| eRAM | 13,255 | 4 | 0.03% |
| Monarch | 8,701 | 2 | 0.02% |
| JensenLab Text Mining | 6,980 | 2 | 0.03% |
| UniProt Disease | 5,504 | 0 | 0.00% |
| DrugCentral Indication | 3,687 | 0 | 0.00% |
| JensenLab Knowledge MedlinePlus | 3,678 | 0 | 0.00% |
| JensenLab Knowledge UniProtKB-KW | 3,334 | 0 | 0.00% |
| CHEMBL | 2,873 | 66 | 2.30% |
| BioPlex,Reactome,STRINGDB | 2,480 | 0 | 0.00% |
| DRUG LABEL | 630 | 3 | 0.48% |
| SCIENTIFIC LITERATURE | 542 | 2 | 0.37% |
| IUPHAR | 237 | 1 | 0.42% |
| JensenLab Knowledge AmyCo | 188 | 0 | 0.00% |
| KEGG DRUG | 25 | 0 | 0.00% |
| BioPlex,Reactome | 6 | 0 | 0.00% |

## Confidence distribution (all edges; pruned edges in the last column)

| confidence | edges | pruned edges |
|---|---|---|
| 0.0 - 0.1 | 0 | 0 |
| 0.1 - 0.2 | 392 | 392 |
| 0.2 - 0.3 | 228,235 | 209,176 |
| 0.3 - 0.4 | 1,955,945 | 47,071 |
| 0.4 - 0.5 | 5,004,914 | 0 |
| 0.5 - 0.6 | 10,374,841 | 0 |
| 0.6 - 0.7 | 8,727,812 | 0 |
| 0.7 - 0.8 | 170,550 | 0 |
| 0.8 - 0.9 | 113 | 0 |
| 0.9 - 1.0 | 0 | 0 |

What this pruner is: confidence re-weighting (learned graph plausibility + corroboration, plus source trust) with a conservative default cut. What it demonstrably removes is uncorroborated, graph-implausible edges; whether they are wrong is for the blinded spot check (`outputs/pharos/spotcheck_rule.md`) and the organisers' gold set to decide.
