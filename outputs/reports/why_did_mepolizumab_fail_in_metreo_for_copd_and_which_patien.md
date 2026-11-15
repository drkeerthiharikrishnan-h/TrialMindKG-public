# TrialMindKG Intelligence Report

**Question:** Why did mepolizumab fail in METREO for COPD, and which patients might respond?

*Generated with NVIDIA NIM models listed above.* Graph version 9c447feedcab.

## Failure Diagnosis
- The registry record indicates METREO was negative after multiplicity adjustment, and the broader COPD evidence was inconsistent, leading to the trial being considered a failure.  
  *curated summary, uncertainty low* - evidence `e9cf30d3d00f992d7`
- Post-hoc analysis suggested that patients with higher eosinophil levels might benefit from mepolizumab, as pooled METREX/METREO evidence showed greater benefit with higher eosinophils, informing a biomarker-enrichment strategy.  
  *post hoc, uncertainty medium* - evidence `e9cf30d3d00f992d7`

## Candidate Responder Population
- Post-hoc analysis of pooled METREX/METREO data showed greater benefit of mepolizumab in COPD patients with higher eosinophil counts, supporting a biomarker-enrichment strategy for further development.  
  *post hoc, uncertainty medium* - evidence `e9cf30d3d00f992d7`
- The post-failure analysis indicated that the exploratory modeling of eosinophil subgroups was prespecified within the METREX/METREO pooled analysis plan.  
  *prespecified, uncertainty medium* - evidence `e9cf30d3d00f992d7`

## Related Indications
- Mepolizumab failed in the METREO trial for COPD, as indicated by the trial being listed in a curated summary of failed clinical trials from 2016-2026.  
  *curated summary, uncertainty low* - evidence `ee55bfd2cd0ead718`
- The METREO trial studied mepolizumab in patients with chronic obstructive pulmonary disease (COPD).  
  *registry record, uncertainty low* - evidence `e0836db4c426ba655`, `e887dee3ca1568467`, `ed9d87b62dc6abcb8`
- Mepolizumab is a drug that interacts with interleukin-5 (IL-5), which is the protein product of the IL5 gene.  
  *curated summary, uncertainty low* - evidence `e798ad723eab21834`, `e12c8bb5d2824ce3c`

## Evidence-Supported Redesign Options
- The eosinophil-enriched development strategy was prospectively confirmed in the MATINEE trial, leading to US COPD approval in May 2025.  
  *prospectively confirmed, uncertainty low* - evidence `e9cf30d3d00f992d7`

## Claims removed by the verifier
- (failure_diagnostician) Mepolizumab failed to meet the primary endpoint of rate of moderate or severe exacerbations in METREO, with a rate ratio of 0.8 (95% CI 0.65-0.98) and p-value of 0.068 for the 100 mg dose vs placebo, which was not statistically significant after multiplicity adjustment. - numbers not in cited evidence: ['0']
- (population_stratifier) Mepolizumab failed to meet the primary endpoint of reducing moderate or severe exacerbation rate in the overall METREO COPD population, with a rate ratio of 0.8 (95% CI 0.65-0.98) and p=0.068 for the 100mg dose, which was not statistically significant after multiplicity adjustment. - numbers not in cited evidence: ['0']
- (redesign_advisor) Mepolizumab failed in METREO for COPD because the primary endpoint (rate of moderate or severe exacerbations) did not reach statistical significance after multiplicity adjustment (p=0.068 for 100mg, p=0.14 for 300mg), despite a nominally significant result in one analysis (p=0.034) that was not prespecified for the primary conclusion. - verifier: The claim is not fully supported by the facts. While the facts confirm the p-values for the primary endpoint (0.068 for 100mg and 0.14 for 300mg) and the nominal p=0.034 result, they do not explicitly state that the p=0.034 result was not prespecified for the primary conclusion, nor do they confirm that multiplicity adjustment was the reason for the primary endpoint failure. The facts only provide the statistical outcomes and methods, not the prespecification details or the multiplicity adjustment rationale.
- (redesign_advisor) Post-hoc analysis of METREO and METREX showed greater benefit in patients with higher eosinophil counts, which informed a biomarker-enrichment strategy for further development. - verifier gave no usable answer (malformed model output)
