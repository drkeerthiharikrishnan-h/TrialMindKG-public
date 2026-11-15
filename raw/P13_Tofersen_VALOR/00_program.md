# P13: Tofersen — VALOR

Source: data/curated/failed_clinical_trials_2016_2026.md (curated inventory row).

| Field | Value |
|---|---|
| year | 2021 |
| sponsor | Biogen / Ionis |
| indication | SOD1-associated ALS |
| what_failed | Phase III: 28-week clinical primary endpoint missed; later biomarker-based regulatory pathway. |
| data_access | U |
| source_url | https://investors.biogen.com/news-releases/news-release-details/biogen-announces-topline-results-tofersen-phase-3-study-and-its |

## Registry trials linked by the pipeline (confirm in the ground-truth workbook)
- NCT02623699: An Efficacy, Safety, Tolerability, Pharmacokinetics and Pharmacodynamics Study of BIIB067 (Tofersen) in Adults With Inherited Amyotrophic Lateral Sclerosis (ALS) (match rule: acronym_contains) -> trials/NCT02623699/

## Curated history after the failure
### DEV:post_failure:7 (post_failure)
- analysis_showed: Neurofilament light reduction and longer follow-up supplied additional biological and clinical context.
- case: Tofersen; Biogen/Ionis
- original_problem: VALOR missed the short-duration clinical primary endpoint.
- outcome_evidence: FDA accelerated approval in April 2023 for SOD1-ALS. A biomarker/regulatory strategy; not proof that post hoc clinical analysis made the original primary result positive.
- pivot_decision: Pursued accelerated approval using plasma neurofilament light as a surrogate, alongside further testing.
- source_urls: ["https://www.fda.gov/drugs/news-events-human-drugs/fda-approves-treatment-amyotrophic-lateral-sclerosis-associated-mutation-sod1-gene", "https://www.fda.gov/media/186135/download"]
