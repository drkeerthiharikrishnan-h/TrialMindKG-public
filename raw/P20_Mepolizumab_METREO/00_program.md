# P20: Mepolizumab — METREO

Source: data/curated/failed_clinical_trials_2016_2026.md (curated inventory row).

| Field | Value |
|---|---|
| year | 2017 |
| sponsor | GSK |
| indication | Exacerbation-prone eosinophilic COPD |
| what_failed | Phase III: primary comparisons did not meet multiplicity-adjusted significance. METREX was mixed/positive in its eosinophilic population and is not counted as a separate failure here. |
| data_access | R |
| source_url | https://www.nejm.org/doi/full/10.1056/NEJMoa1708208 |

## Registry trials linked by the pipeline (confirm in the ground-truth workbook)
- NCT02105961: Efficacy and Safety of Mepolizumab as an Add-on Treatment in Chronic Obstructive Pulmonary Disease (COPD) (match rule: curator_override:high) -> trials/NCT02105961/

## Curated history after the failure
### DEV:data_access:6 (data_access)
- access_route: METREX/METREO analysis includes a data-sharing statement; METREO registry describes proposal approval.
- limitation: Access by request; METREX is not included in the failed-trial denominator.
- source_urls: ["https://pmc.ncbi.nlm.nih.gov/articles/PMC8215850/", "https://clinicaltrials.gov/study/NCT02105961"]
- trials: METREO
### DEV:post_failure:6 (post_failure)
- analysis_showed: Pooled METREX/METREO evidence supported greater benefit with higher eosinophils. The meta-analysis was prespecified and included exploratory modeling.
- case: Mepolizumab; GSK
- original_problem: METREO was negative after multiplicity adjustment; the broader COPD evidence was inconsistent.
- outcome_evidence: US COPD approval in May 2025. A biomarker-enrichment development story with prospective confirmation, not purely a post hoc rescue.
- pivot_decision: Further development focused on eosinophilic COPD; MATINEE subsequently confirmed benefit.
- source_urls: ["https://pubmed.ncbi.nlm.nih.gov/34163157/", "https://www.gsk.com/en-gb/media/press-releases/gsk-announces-positive-results-from-phase-iii-trial-of-nucala-mepolizumab-in-copd/", "https://www.gsk.com/en-gb/media/press-releases/nucala-mepolizumab-approved-by-us-fda/"]
