# P11: Prasinezumab — PASADENA / PADOVA

Source: data/curated/failed_clinical_trials_2016_2026.md (curated inventory row).

| Field | Value |
|---|---|
| year | 2020 / 2024 |
| sponsor | Roche / Prothena |
| indication | Early Parkinson’s disease |
| what_failed | Phase II/IIb: primary endpoints missed; PADOVA p=0.0657. |
| data_access | R: PASADENA only |
| source_url | https://www.roche.com/media/releases/med-cor-2024-12-19 |

## Registry trials linked by the pipeline (confirm in the ground-truth workbook)
- NCT03100149: A Study to Evaluate the Efficacy of Prasinezumab (RO7046015/PRX002) in Participants With Early Parkinson's Disease (match rule: acronym_exact) -> trials/NCT03100149/
- NCT04777331: A Study to Evaluate the Efficacy and Safety of Intravenous Prasinezumab in Participants With Early Parkinson's Disease (match rule: acronym_exact) -> trials/NCT04777331/

## Curated history after the failure
### DEV:data_access:3 (data_access)
- access_route: Trial investigators’ secondary-analysis paper includes a patient-level request statement.
- limitation: Does not establish that PADOVA is already available.
- source_urls: ["https://www.nature.com/articles/s41591-024-02886-y"]
- trials: PASADENA
### DEV:post_failure:3 (post_failure)
- analysis_showed: PASADENA exploratory analyses suggested greater motor effects in faster progressors. PADOVA had supportive trends and a nominal signal in a prespecified levodopa subgroup.
- case: Prasinezumab; Roche/Prothena
- original_problem: PASADENA missed its global primary measure; PADOVA later narrowly missed its primary endpoint.
- outcome_evidence: Development continuation/redesign, not a demonstrated clinical rescue. The PADOVA subgroup was prespecified, so it should not be described as a purely post hoc finding.
- pivot_decision: Roche announced advancement to phase III in June 2025 using the total evidence.
- source_urls: ["https://www.nature.com/articles/s41591-024-02886-y", "https://www.roche.com/media/releases/med-cor-2025-06-16"]
