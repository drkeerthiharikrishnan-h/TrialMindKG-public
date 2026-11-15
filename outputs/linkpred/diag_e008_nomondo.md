# E008 control: GNN + path features without the MONDO hierarchy (diag_gnnpath_1m_nomondo)

Pre-registered with E008 (ledger 2026-11-05) as a report figure. Run on the Mac (owner Terminal) 2026-11-06 08:46 to 20:55 IST, exit 0,
after the 18:00 link-prediction freeze; started before it. Same frozen flags as E008 (`diag_gnnpath_1m`), 1M cap, 5 folds,
plus `--drop-layers mondo_hierarchy` on G2 (46,732 edges). Test digest 24f5864cc07ad723 on every file (checked).
Files: `G0_B_kfold@diag_gnnpath_1m.json`, `G2_B_kfold@diag_gnnpath_1m.json`, `G2_B_kfold@diag_gnnpath_1m_nomondo.json`.
Statistics: `linkpred.compare.paired_delta` (paired fold differences, 95% t interval, wins = folds where the first is higher).

## Mean AUROC over 5 folds

| Head | G0 Pharos | G2 enriched | G2 without MONDO |
|---|---|---|---|
| GNN + paths (`auroc_gnnpath`) | 0.6614 | 0.7930 | 0.6944 |
| paths only (`auroc_pathhead`) | 0.6583 | 0.7913 | 0.6910 |
| GNN only (`auroc_gnnhead`) | 0.5579 | 0.5571 | 0.5529 |
| plain R-GCN (`auroc`) | 0.5672 | 0.5673 | 0.5658 |

## Paired differences (95% CI), wins

| Head | no-MONDO minus G0 | no-MONDO minus G2 | G2 minus G0 (E008) |
|---|---|---|---|
| GNN + paths | +0.0329 (+0.0293 to +0.0366), 5/5 | -0.0986 (-0.1032 to -0.0940), 0/5 | +0.1315 (+0.1260 to +0.1370), 5/5 |
| paths only | +0.0327 (+0.0296 to +0.0358), 5/5 | -0.1002 (-0.1055 to -0.0950), 0/5 | +0.1330 (+0.1276 to +0.1383), 5/5 |
| GNN only | -0.0050 (-0.0129 to +0.0030), 1/5 | -0.0041 (-0.0095 to +0.0012), 1/5 | -0.0008 (-0.0052 to +0.0036), 3/5 |
| plain R-GCN | -0.0014 (-0.0064 to +0.0036), 1/5 | -0.0015 (-0.0072 to +0.0042), 3/5 | +0.0001 (-0.0039 to +0.0041), 3/5 |

**Reading.** Without the MONDO hierarchy the GNN + paths model still beats Pharos alone by +0.0329 AUROC (CI above 0, 5/5 folds),
but the gain shrinks from +0.1315 to +0.0329: the MONDO disease hierarchy accounts for about 75% of the E008 gain (E005 found ~82% for
the meta-path model). The remaining +0.0329 comes from the other enrichment layers (Open Targets, ChEMBL, HPO, GO) through the path features.
The GNN head alone does not gain in any configuration (CIs include 0).

## Strata (GNN + paths, no-MONDO minus G0; all 5/5)

| Stratum | no-MONDO AUROC | no-MONDO minus G0 | G2 minus G0 (E008) |
|---|---|---|---|
| disease degree 1-5 | 0.7926 | +0.0537 (+0.0367 to +0.0706), 5/5 | +0.1398 |
| disease degree 6-20 | 0.8011 | +0.0178 (+0.0066 to +0.0290), 5/5 | +0.0768 |
| disease degree 21-100 | 0.7522 | +0.0091 (+0.0057 to +0.0126), 5/5 | +0.0752 |
| disease degree 101-500 | 0.7034 | +0.0211 (+0.0167 to +0.0255), 5/5 | +0.1209 |
| disease degree >500 | 0.8815 | +0.0219 (+0.0058 to +0.0380), 5/5 | +0.0302 |
| gene degree 1-5 | 0.6662 | +0.0234 (+0.0177 to +0.0290), 5/5 | +0.1370 |
| gene degree 6-20 | 0.7091 | +0.0440 (+0.0399 to +0.0482), 5/5 | +0.1460 |
| gene degree 21-100 | 0.7206 | +0.0245 (+0.0153 to +0.0337), 5/5 | +0.1045 |
| gene degree 101-500 | 0.7337 | +0.0449 (+0.0231 to +0.0666), 5/5 | +0.0759 |

Leak edges removed per fold: G0 7306, G2 10127, no-MONDO 10127 (`n_leak_edges_removed`, fold 0).

Limits: one seed; the MONDO layer was dropped from G2 at run time (not a rebuilt graph); exploratory analysis, not the pre-registered pass rule of E008.
