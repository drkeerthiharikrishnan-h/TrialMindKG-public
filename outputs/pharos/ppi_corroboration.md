# Pruned versus kept protein-protein rows: experimental records in IntAct

No model is involved. The pruner never reads IntAct. Rows: every Pharos edge with predicate `physically_interacts_with` between two different proteins: **11,672,660 rows**, 5,836,330 distinct unordered protein pairs. IntAct: 687,831 distinct human protein pairs with at least one non-negative evidence (1,178,206 evidence lines used of 1,266,853).

**Headline: 1.54% of pruned rows and 2.40% of kept rows have a physical record in IntAct that is independent of the row's own source** (kept rows 1.6 times as often).
Of all rows with such a record, 1.21% are pruned; 1.87% of all rows are pruned.

With the source held fixed, the kept share is higher than the pruned share in 4 of the 4 sources that have both pruned and kept rows (table 'By source of the row').

Counting ANY IntAct record the order is the other way round (4.68% of pruned rows, 3.33% of kept rows). That count is not a fair test: 8,052 of the 218,620 pruned rows (3.68%) and 139,776 of the 11,454,040 kept rows (1.22%) come from BioPlex, whose own experiments are deposited in IntAct, so such a row finds its own source there. The headline column leaves those records out for BioPlex rows.

| pruner verdict | rows | any record | physical record | independent physical | independent, binary | corroborated (95% interval) |
|---|---|---|---|---|---|---|
| prune | 218,620 | 10,242 (4.68%) | 10,242 (4.68%) | 3,193 (1.46%) | 1,879 (0.86%) | 3,366 = 1.54% (1.49% to 1.59%) |
| keep | 11,454,040 | 381,584 (3.33%) | 380,372 (3.32%) | 255,167 (2.23%) | 105,359 (0.92%) | 275,368 = 2.40% (2.40% to 2.41%) |

Figure: `docs/figures/ppi_corroboration_intact.png` (left: the headline share for all rows and within each source that has both verdicts; right: the same share by tenth of pruner confidence).

Definitions (nested, negative records excluded): any = any IntAct evidence for the pair; physical = interaction type other than colocalization or genetic interaction; independent = physical, from a publication other than the three BioPlex papers; binary = independent and not a spoke-expanded co-complex purification. corroborated = independent for a row whose own source includes BioPlex (an IntAct record from a BioPlex paper is the same experiment), physical for every other row.

## By source of the row

| pruner verdict | source | rows | corroborated | share |
|---|---|---|---|---|
| prune | STRINGDB | 210,411 | 2,891 | 1.37% |
| prune | BioPlex | 4,788 | 213 | 4.45% |
| prune | BioPlex,STRINGDB | 3,264 | 253 | 7.75% |
| prune | Reactome,STRINGDB | 157 | 9 | 5.73% |
| keep | STRINGDB | 11,280,433 | 238,295 | 2.11% |
| keep | BioPlex,STRINGDB | 71,816 | 21,231 | 29.56% |
| keep | BioPlex | 65,474 | 6,193 | 9.46% |
| keep | Reactome,STRINGDB | 31,935 | 7,515 | 23.53% |
| keep | BioPlex,Reactome,STRINGDB | 2,480 | 2,062 | 83.15% |
| keep | Reactome | 1,896 | 72 | 3.80% |
| keep | BioPlex,Reactome | 6 | 0 | 0.00% |

## By pruner confidence (ten equal-count bins, least confident first)

| bin | confidence range | rows | pruned | corroborated | share |
|---|---|---|---|---|---|
| 1 | 0.195 to 0.370 | 1,167,266 | 218,620 | 16,643 | 1.43% |
| 2 | 0.370 to 0.415 | 1,167,266 | 0 | 17,556 | 1.50% |
| 3 | 0.415 to 0.450 | 1,167,266 | 0 | 19,559 | 1.68% |
| 4 | 0.450 to 0.482 | 1,167,266 | 0 | 21,190 | 1.82% |
| 5 | 0.482 to 0.511 | 1,167,266 | 0 | 23,129 | 1.98% |
| 6 | 0.511 to 0.539 | 1,167,266 | 0 | 25,654 | 2.20% |
| 7 | 0.539 to 0.567 | 1,167,266 | 0 | 27,814 | 2.38% |
| 8 | 0.567 to 0.598 | 1,167,266 | 0 | 31,293 | 2.68% |
| 9 | 0.598 to 0.635 | 1,167,266 | 0 | 38,417 | 3.29% |
| 10 | 0.635 to 0.748 | 1,167,266 | 0 | 57,479 | 4.92% |

Rows that link a protein to itself are not in the tables: 19,165 rows (19,165 prune).

## Limits

- IntAct holds a minority of true human protein interactions, so a row without a record is not shown to be wrong.
- STRING's experimental channel is partly built from IntAct. The pruner does not read IntAct, but a high STRING score and an IntAct record are not independent facts.
- Pairs are matched on UniProt accession with isoform suffixes removed; an interaction curated on a secondary accession is missed.

## Inputs

- `data/external/intact/human.zip` (1,215,044,675 bytes, modified 2026-11-12 02:32)
- `data/kg/pharos/edge_verdicts.tsv.gz` (766,661,485 bytes, modified 2026-11-09 17:12)
