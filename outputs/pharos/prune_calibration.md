# Pruning confidence vs Open Targets corroboration (Pharos gene-disease edges)

Open Targets 26.09 gene-disease pairs: 769,960. Pharos gene-disease edges checked: 296,974 non-text-mining + 13,118,763 text-mining (TIN-X, JensenLab Text Mining: the sources whose edges carry knowledge level text_co_occurrence). 'in OT' = the pair is asserted by Open Targets at any score; 'OT >= 0.2' = at overall association score >= 0.2. The Pharos pruner never saw Open Targets, so this is held-out evidence; it is a calibration curve, not a gold set.

Least-confident decile: in OT 7.4%, OT >= 0.2 5.2%; most-confident decile: 53.8% and 48.7%.

## By verdict (non-text-mining)

| verdict | edges | in OT | OT >= 0.2 |
|---|---|---|---|
| keep | 296,694 | 17.9% | 14.6% |
| prune | 280 | 0.7% | 0.4% |

## By confidence decile (1 = least confident, non-text-mining)

| decile | confidence range | edges | in OT | OT >= 0.2 |
|---|---|---|---|---|
| 1 | 0.165 - 0.514 | 29,698 | 7.4% | 5.2% |
| 2 | 0.514 - 0.552 | 29,697 | 17.8% | 15.8% |
| 3 | 0.552 - 0.584 | 29,697 | 8.2% | 5.8% |
| 4 | 0.584 - 0.607 | 29,698 | 8.2% | 5.6% |
| 5 | 0.607 - 0.626 | 29,697 | 8.8% | 6.0% |
| 6 | 0.626 - 0.646 | 29,697 | 11.2% | 7.9% |
| 7 | 0.646 - 0.665 | 29,698 | 14.1% | 10.3% |
| 8 | 0.665 - 0.686 | 29,697 | 18.6% | 14.4% |
| 9 | 0.686 - 0.719 | 29,697 | 31.4% | 25.9% |
| 10 | 0.719 - 0.814 | 29,698 | 53.8% | 48.7% |

## By source (non-text-mining)

| source | edges | in OT | OT >= 0.2 | pruned |
|---|---|---|---|---|
| Expression Atlas | 158,707 | 3.5% | 1.5% | 0.1% |
| DisGeNET | 49,172 | 27.8% | 22.8% | 0.1% |
| CTD | 27,157 | 30.1% | 23.9% | 0.0% |
| JensenLab Experiment TIGA | 23,591 | 23.6% | 17.7% | 0.1% |
| eRAM | 13,255 | 34.0% | 29.4% | 0.0% |
| Monarch | 8,701 | 65.6% | 63.0% | 0.0% |
| UniProt Disease | 5,504 | 74.9% | 74.0% | 0.0% |
| DrugCentral Indication | 3,687 | 43.5% | 40.9% | 0.0% |
| JensenLab Knowledge MedlinePlus | 3,678 | 72.2% | 67.9% | 0.0% |
| JensenLab Knowledge UniProtKB-KW | 3,334 | 50.7% | 44.7% | 0.0% |
| JensenLab Knowledge AmyCo | 188 | 19.1% | 13.3% | 0.0% |

## Sources within each confidence decile (non-text-mining)

Each cell: edges of the source in the decile / share of them with OT >= 0.2. A decile's rate above depends on which sources fall in it.

| source | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Expression Atlas | 19,819 / 1.0% | 18,058 / 1.0% | 21,417 / 1.2% | 21,442 / 1.6% | 20,726 / 1.6% | 19,049 / 1.7% | 16,670 / 1.7% | 13,620 / 1.7% | 7,552 / 2.0% | 354 / 20.9% |
| DisGeNET | 4,019 / 11.8% | 4,296 / 34.3% | 3,593 / 10.7% | 4,042 / 12.4% | 4,616 / 12.2% | 4,947 / 13.8% | 5,555 / 14.9% | 5,419 / 17.8% | 6,246 / 27.2% | 6,439 / 56.7% |
| CTD | 610 / 13.6% | 1,854 / 48.9% | 763 / 16.1% | 799 / 16.0% | 853 / 15.1% | 1,070 / 14.6% | 1,546 / 14.3% | 2,294 / 15.6% | 5,362 / 18.9% | 12,006 / 28.1% |
| JensenLab Experiment TIGA | 3,836 / 6.9% | 2,177 / 9.3% | 2,226 / 11.5% | 2,088 / 13.9% | 1,997 / 16.0% | 2,312 / 18.6% | 2,792 / 21.2% | 3,673 / 25.0% | 2,111 / 29.4% | 379 / 72.3% |
| eRAM | 502 / 15.3% | 400 / 14.2% | 603 / 17.1% | 705 / 16.2% | 798 / 18.4% | 1,237 / 21.8% | 1,574 / 22.6% | 2,304 / 26.7% | 2,962 / 41.5% | 2,170 / 43.1% |
| Monarch | 668 / 58.5% | 688 / 64.1% | 740 / 60.5% | 211 / 46.4% | 250 / 39.2% | 401 / 47.9% | 511 / 55.4% | 699 / 55.5% | 1,566 / 63.0% | 2,967 / 72.7% |
| UniProt Disease | 61 / 60.7% | 2,016 / 67.5% | 54 / 66.7% | 80 / 58.8% | 68 / 72.1% | 101 / 68.3% | 160 / 68.1% | 239 / 71.5% | 647 / 74.0% | 2,078 / 82.4% |
| DrugCentral Indication | 130 / 13.1% | 104 / 20.2% | 170 / 28.8% | 167 / 36.5% | 160 / 37.5% | 227 / 37.4% | 322 / 39.8% | 546 / 41.0% | 1,279 / 43.1% | 582 / 53.8% |
| JensenLab Knowledge MedlinePlus | 28 / 42.9% | 65 / 55.4% | 71 / 47.9% | 85 / 51.8% | 103 / 53.4% | 163 / 46.0% | 222 / 58.1% | 399 / 53.1% | 855 / 64.9% | 1,687 / 79.8% |
| JensenLab Knowledge UniProtKB-KW | 19 / 42.1% | 33 / 36.4% | 54 / 38.9% | 70 / 30.0% | 117 / 34.2% | 176 / 30.1% | 323 / 41.8% | 469 / 38.8% | 1,056 / 37.8% | 1,017 / 60.8% |
| JensenLab Knowledge AmyCo | 6 / 0.0% | 6 / 16.7% | 6 / 0.0% | 9 / 0.0% | 9 / 0.0% | 14 / 7.1% | 23 / 13.0% | 35 / 5.7% | 61 / 6.6% | 19 / 73.7% |

## Text mining (TIN-X, JensenLab Text Mining) by verdict

| verdict | edges | in OT | OT >= 0.2 |
|---|---|---|---|
| keep | 13,108,237 | 1.2% | 0.7% |
| prune | 10,526 | 0.0% | 0.0% |

Caveat: Open Targets includes literature-derived evidence, so agreement is partly shared provenance; the OT >= 0.2 column reduces that. Expression Atlas rows are differential-expression hits, not curated associations, whatever their verdict.
