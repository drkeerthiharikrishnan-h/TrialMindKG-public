# P19: Anifrolumab — TULIP-1

Source: data/curated/failed_clinical_trials_2016_2026.md (curated inventory row).

| Field | Value |
|---|---|
| year | 2018 |
| sponsor | AstraZeneca |
| indication | Moderate–severe systemic lupus |
| what_failed | Phase III: SRI-4 primary endpoint missed. |
| data_access | R |
| source_url | https://www.astrazeneca.com/media-centre/press-releases/2018/update-on-tulip-1-phase-iii-trial-for-anifrolumab-in-systemic-lupus-erythematosus-31082018.html |

## Registry trials linked by the pipeline (confirm in the ground-truth workbook)
- NCT02446912: Efficacy and Safety of Two Doses of Anifrolumab Compared to Placebo in Adult Subjects With Active Systemic Lupus Erythematosus (match rule: curator_override:high) -> trials/NCT02446912/

## Curated history after the failure
### DEV:data_access:5 (data_access)
- access_route: Pooled TULIP analysis identifies access through Vivli.
- limitation: Request approval and permitted reuse apply.
- source_urls: ["https://pmc.ncbi.nlm.nih.gov/articles/PMC9213795/"]
- trials: TULIP-1
### DEV:post_failure:1 (post_failure)
- analysis_showed: Evaluation showed a more favorable response using BICLA, an alternative measure already collected in the program.
- case: Anifrolumab; AstraZeneca
- original_problem: TULIP-1 missed SRI-4, a composite lupus response measure.
- outcome_evidence: TULIP-2 succeeded; Saphnelo received US approval in 2021. Strong example of learning from failure and confirming a revised endpoint in another trial, not retroactively converting TULIP-1 into a success.
- pivot_decision: AstraZeneca chose BICLA for TULIP-2 after evaluating TULIP-1.
- source_urls: ["https://www.astrazeneca.com/media-centre/press-releases/2019/anifrolumab-phase-iii-trial-meets-primary-endpoint-in-systemic-lupus-erythematosus-29082019.html", "https://www.accessdata.fda.gov/drugsatfda_docs/appletter/2021/761123Orig1s000ltr.pdf"]
