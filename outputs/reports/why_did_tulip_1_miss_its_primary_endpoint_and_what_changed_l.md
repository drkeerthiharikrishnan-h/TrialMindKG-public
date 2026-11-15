# TrialMindKG Intelligence Report

**Question:** Why did TULIP-1 miss its primary endpoint, and what changed later?

*Generated with NVIDIA NIM models listed above.* Graph version 9c447feedcab.

## Failure Diagnosis
- TULIP-1 missed its primary endpoint of SRI[4] at Week 52, with a mean difference of -4.2 (95% CI: -14.2 to 5.8) and p-value of 0.412 under the original analysis with restricted medication rules.  
  *prespecified, uncertainty low* - evidence `ee446698536cc3289`
- After TULIP-1 failed, AstraZeneca pivoted to using BICLA as the primary endpoint for TULIP-2, which succeeded and led to US approval of Saphnelo in 2021.  
  *curated summary, uncertainty low* - evidence `ea1720c666f66cedf`
- Post-hoc analyses of TULIP-1 with revised restricted medication rules showed a significant improvement in OCS reduction (mean difference 16.7, 95% CI: 3.5 to 29.8, p=0.013) and BICLA response (mean difference 16.4, 95% CI: 6.7 to 26.2), but these were not the original primary endpoint.  
  *post hoc, uncertainty medium* - evidence `e53a4ee1040daffb3`, `ef992580175db8f32`

## Candidate Responder Population
- TULIP-1 missed its primary endpoint of SRI[4] at Week 52, showing a mean difference of -4.2 (95% CI: -14.2 to 5.8, p=0.412) under original analysis with restricted medication rules.  
  *prespecified, uncertainty low* - evidence `ee446698536cc3289`
- Post-hoc analyses with revised restricted medication rules showed a significant improvement in OCS reduction (≤7.5 mg/day) in the subgroup with baseline OCS ≥10 mg/day, with a mean difference of 16.7 (95% CI: 3.5 to 29.8, p=0.013) at Week 52.  
  *post hoc, uncertainty low* - evidence `e53a4ee1040daffb3`
- After TULIP-1's failure, AstraZeneca pivoted to using BICLA as the primary endpoint for TULIP-2, which succeeded, leading to US approval of Saphnelo in 2021.  
  *curated summary, uncertainty low* - evidence `ea1720c666f66cedf`

## Related Indications
- TULIP-1 (NCT02446912) tested anifrolumab for systemic lupus erythematosus but missed its primary endpoint.  
  *curated summary, uncertainty low* - evidence `e569512e3239982e0`, `e6db1558b36e97d96`, `e7f2fa46f1d3a7646`

## Evidence-Supported Redesign Options
- After TULIP-1's failure, AstraZeneca evaluated the data and chose BICLA as the primary endpoint for TULIP-2, based on a more favorable response observed with this measure.  
  *curated summary, uncertainty low* - evidence `ea1720c666f66cedf`
- TULIP-2 succeeded using BICLA as the primary endpoint, leading to the approval of Saphnelo (anifrolumab) in the US in 2021, confirming the revised endpoint choice prospectively.  
  *prospectively confirmed, uncertainty low* - evidence `ea1720c666f66cedf`
- Post-hoc analyses of TULIP-1 with revised restricted medication rules showed a significant improvement in OCS reduction (≤7.5 mg/day) in the subgroup with baseline OCS ≥10 mg/day, with a mean difference of 16.7 (p=0.013) at Week 52.  
  *post hoc, uncertainty medium* - evidence `e53a4ee1040daffb3`

## Claims removed by the verifier
- (redesign_advisor) TULIP-1 missed its primary endpoint of SRI[4] at Week 52, showing a mean difference of -4.2 (p=0.412) in the original analysis with restricted medication rules. - verifier: The claim states that TULIP-1 missed its primary endpoint of SRI[4] at Week 52 with a mean difference of -4.2 (p=0.412). However, the FACTS show that the mean difference of -4.2 and p-value of 0.412 are associated with the 'Original Analysis With Restricted Medication Rules', not the overall primary analysis. The claim does not specify this restriction, making it potentially misleading or incomplete based solely on the provided facts.
- (redesign_advisor) Post-hoc analyses of TULIP-1 with revised restricted medication rules showed a significant improvement in BICLA response at Week 52, with a mean difference of 16.4 (p_value null, but CI 6.7-26.2 excluding zero). - numbers not in cited evidence: ['6']
