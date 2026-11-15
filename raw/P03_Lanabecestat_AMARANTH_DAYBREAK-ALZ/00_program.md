# P03: Lanabecestat — AMARANTH / DAYBREAK-ALZ

Source: data/curated/failed_clinical_trials_2016_2026.md (curated inventory row).

| Field | Value |
|---|---|
| year | 2018 |
| sponsor | Lilly / AstraZeneca |
| indication | Early / mild Alzheimer’s |
| what_failed | Phase II/III and III: stopped for futility. |
| data_access | D: AMARANTH only |
| source_url | https://www.astrazeneca.com/media-centre/press-releases/2018/update-on-phase-iii-clinical-trials-of-lanabecestat-for-alzheimers-disease-12062018.html |

## Registry trials linked by the pipeline (confirm in the ground-truth workbook)
- NCT02245737: An Efficacy and Safety Study of Lanabecestat (LY3314814) in Early Alzheimer's Disease (match rule: acronym_exact) -> trials/NCT02245737/
- NCT02783573: A Study of Lanabecestat (LY3314814) in Participants With Mild Alzheimer's Disease Dementia (match rule: acronym_exact) -> trials/NCT02783573/

## Curated history after the failure
### DEV:data_access:7 (data_access)
- access_route: Cambridge research repository provides data_notebooks.zip and analysis code.
- limitation: This is an associated research artifact, not verified as the complete sponsor clinical database.
- source_urls: ["https://www.repository.cam.ac.uk/items/3dca61d5-ce6e-41b6-b613-af21f2dfaa8c"]
- trials: AMARANTH — derived resources
### DEV:post_failure:4 (post_failure)
- analysis_showed: A 2025 retrospective AI analysis stratified progression using baseline biomarkers; authors reported 46% less cognitive decline in a selected slow-progressing subgroup at 50 mg.
- case: Lanabecestat; trial sponsors, Cambridge researchers, potential trial-design vendors
- original_problem: AMARANTH was negative in its broad trial population.
- outcome_evidence: Research proof of concept. No sponsor restart, prospective replication or approval attributable to this finding was verified. The 46% is a selected retrospective estimate, not established treatment efficacy.
- pivot_decision: Published a candidate enrichment strategy plus associated data/code.
- source_urls: ["https://www.nature.com/articles/s41467-025-61355-3"]
