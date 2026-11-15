# Novelty of the link predictions (CS-5, review finding M1)

Inputs: `outputs/linkpred/predictions_metapath.tsv.gz` (model ['metapath'], graph ['G3']); edges `data/kg/assembled/g3/edges.tsv.gz` (29,270,260 edges, every layer); MONDO is_a from `data/kg/mondo_hierarchy/edges.tsv.gz`. Script `scripts/predictions_novelty.py`; per-row flags in `predictions_novelty.tsv.gz`.

Rules: a pair is **known** when any edge of the graph states the same (subject, object) in the same direction, any layer, any predicate (the card's `known_in` rule, `pharos_app.PharosGraph.same_pair`), or when a layer's pruner marked such an edge 'prune' (G3's edge file holds kept edges only; pruned rows read from `data/kg/pharos/edge_verdicts.tsv.gz`, `data/kg/opentargets/edge_verdicts.tsv.gz`). **Ancestor of known**: the predicted disease is a strict MONDO ancestor (transitive `biolink:subclass_of`) of a MONDO disease the subject already has an edge to (a broad category, not a new disease).

| | rows | unique (subject, object) pairs |
|---|---|---|
| all predictions | 277,490 | 269,889 |
| known in at least one layer | 154,384 (55.6%) | 150,246 |
| **novel** (in no layer) | **123,106** (44.4%) | 119,643 |
| novel and not an ancestor of a known disease | **112,187** (40.4%) | 109,141 |
| known and ancestor of a known disease | 91,066 | - |

- Known only through a pruned layer edge: 270 rows.
- Known rows whose graph edges are all text co-occurrence (TIN-X): 154,114 of 154,384. The predictor skips pairs already in its training graph, which drops text co-occurrence edges, so curated pairs are excluded upstream and the known pairs left are text-mined ones.
- Known rows by the edges' knowledge levels: text_co_occurrence 154,114.
- Pairs stated only in the reverse direction (object -> subject): 0 rows (counted as novel above).
- Novel rows whose subject shares a gene product (`biolink:has_gene_product`) with another gene id that already has an edge to the disease (e.g. an ENSEMBL and an NCBIGene id of one gene): 516 (still counted as novel above, by the card's exact-id rule).
- Known rows by the edge file's layer code (a row can be in several): L0 154,114.
- Edge verdicts in the edge file: keep 29,270,260.

## Per node (rows where the node is subject or object; card = top 20 unique pairs by score)

| node | rows | unique pairs | novel | novel, not ancestor | card top 20: novel | card top 20: novel, not ancestor |
|---|---|---|---|---|---|---|
| NCBIGene:1956 | 76 | 68 | 14 | 9 | 1 of 20 | 1 of 20 |
| NCBIGene:5743 | 143 | 135 | 27 | 18 | 2 of 20 | 2 of 20 |
| NCBIGene:6647 | 54 | 49 | 14 | 10 | 4 of 20 | 2 of 20 |
| MONDO:0004975 | 4,722 | 4,713 | 797 | 797 | 0 of 20 | 0 of 20 |

## Sentence for the start page (Claude Code, CC-30)

> The model proposes 112,187 gene-disease links that no source in the graph states yet (of 277,490 scored predictions: 154,384 restate a pair the graph already holds, 154,114 of them only as a text-mining co-mention, and 10,919 name a broader category of a disease already linked to the gene).
