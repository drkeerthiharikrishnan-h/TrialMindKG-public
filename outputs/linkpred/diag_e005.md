# E005: which enrichment layer carries the meta-path gain? (exploratory, 2026-11-05)

**Design.** G2 (rebuilt 2026-11-04, Open Targets score >= 0.1) with one enrichment layer removed at a time, frozen
flags with `--epochs 0` (meta-path model + baselines only; the R-GCN is not trained in these runs, so its AUROC
column is not a result), `--max-edges 1000000`, Task B, 5 folds, test set digest `24f5864cc07ad723` on every
file (checked). Base for the gain = G0 at the same cap (E003, `G0_B_kfold@frozen_a_g0_1m.json`).
Paired differences per fold (`linkpred.compare.paired_delta`), 95% CI over 5 folds.

| Variant | Meta-path AUROC | vs G0@1M (95% CI, folds won) | vs G2 all layers | Coverage | CN AUROC | CN vs G0@1M |
|---|---|---|---|---|---|---|
| G0@1M | 0.6772 | - | - | 0.875 | 0.5000 | - |
| G2 all layers | 0.7986 | +0.1214 (+0.1181 to +0.1247), 5/5 | - | 0.993 | 0.7300 | +0.2300 (+0.2284 to +0.2315), 5/5 |
| drop Open Targets | 0.7860 | +0.1088 (+0.1050 to +0.1126), 5/5 | -0.0126 (-0.0179 to -0.0073), 0/5 | 0.965 | 0.6861 | +0.1861 (+0.1835 to +0.1886), 5/5 |
| drop ChEMBL | 0.8019 | +0.1247 (+0.1204 to +0.1290), 5/5 | +0.0033 (+0.0001 to +0.0064), 5/5 | 0.993 | 0.7299 | +0.2299 (+0.2284 to +0.2314), 5/5 |
| drop MONDO hierarchy | 0.6995 | +0.0223 (+0.0178 to +0.0268), 5/5 | -0.0991 (-0.1044 to -0.0939), 0/5 | 0.963 | 0.5628 | +0.0628 (+0.0598 to +0.0659), 5/5 |
| drop HPO hierarchy | 0.7984 | +0.1212 (+0.1176 to +0.1248), 5/5 | -0.0002 (-0.0007 to +0.0002), 1/5 | 0.993 | 0.7300 | +0.2300 (+0.2284 to +0.2315), 5/5 |
| drop GO hierarchy | 0.7986 | +0.1214 (+0.1181 to +0.1247), 5/5 | +0.0000 (+0.0000 to +0.0000), 0/5 | 0.993 | 0.7300 | +0.2300 (+0.2284 to +0.2315), 5/5 |

## Reading (plain language)

1. **The MONDO disease hierarchy carries most of the gain.** Removing it takes the meta-path gain from +0.121 to
   +0.022 (about 82% of the gain gone). The best single path shape on G2 is "gene linked to a disease that is a
   sub-class of the test disease" (AUROC 0.659 alone).
2. **Open Targets adds a small, real part** (+0.013 meta-path; +0.044 for common neighbours).
3. **HPO and GO hierarchies add nothing** to this task (differences 0.0000 to -0.0002).
4. **ChEMBL slightly hurts** (removing it improves meta-path AUROC by +0.003, CI just above 0): it adds paths
   that are noise for gene -> disease.
5. Without MONDO, a small gain remains (+0.022, CI +0.018 to +0.027, 5/5 folds).

## Caveat that must travel with every gain we report

Most of the gain is **ontology smoothing**: if a gene is linked to a parent or child of the test disease, the model
predicts the gene for the test disease. This is a legitimate, explainable use of the MONDO hierarchy, but it is
partly a restatement of existing gene-disease knowledge at a neighbouring level of the ontology, not new
biological evidence. Write-up wording: "the enriched graph improves the path-based model, mostly through the
MONDO disease hierarchy; without MONDO the gain is +0.02 AUROC". Do not present AUROC as clinical accuracy.

## Implications

- For E008 (GNN + path features, pre-registered 2026-11-05) the same caveat applies; its report must include the
  drop-MONDO variant if it passes.
- Candidate pruning finding for the Accuracy & Pruning criterion: HPO/GO hierarchy edges are neutral and ChEMBL
  edges slightly negative **for this gene -> disease task only**; this is not evidence that they are wrong.
- Not done (not assessed here): a strict control removing test pairs whose disease has a MONDO parent/child linked
  to the same gene in training.

Files: `G2_B_kfold@diag_e005_{noot,nochembl37,nomondo,nohpo,nogo}.json`, `G2_B_kfold@diag_rebuilt_1m.json`,
`G0_B_kfold@frozen_a_g0_1m.json`.
