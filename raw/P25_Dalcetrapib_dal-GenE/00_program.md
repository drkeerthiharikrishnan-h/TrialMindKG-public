# P25: Dalcetrapib — dal-GenE

Source: data/curated/failed_clinical_trials_2016_2026.md (curated inventory row).

| Field | Value |
|---|---|
| year | 2022 |
| sponsor | DalCor |
| indication | Genetically selected patients after acute coronary syndrome |
| what_failed | Phase III: primary intention-to-treat cardiovascular endpoint not significant. |
| data_access | U |
| source_url | https://pubmed.ncbi.nlm.nih.gov/35856777/ |

## Registry trials linked by the pipeline (confirm in the ground-truth workbook)
- NCT02525939: Effect of Dalcetrapib vs Placebo on CV Risk in a Genetically Defined Population With a Recent ACS (match rule: acronym_exact) -> trials/NCT02525939/

## Curated history after the failure
### DEV:post_failure:8 (post_failure)
- analysis_showed: Retrospective pharmacogenomics suggested an ADCY9-defined responder group.
- case: Dalcetrapib; DalCor
- original_problem: Historical dal-OUTCOMES failed in 2012, outside this review’s failure window.
- outcome_evidence: dal-GenE still missed the primary intention-to-treat endpoint. A valuable counterexample: plausible post hoc precision-medicine findings may not confirm.
- pivot_decision: A genotype-selected phase III trial, dal-GenE, tested the hypothesis; its 2022 readout is inside the window.
- source_urls: ["https://pubmed.ncbi.nlm.nih.gov/35856777/"]
