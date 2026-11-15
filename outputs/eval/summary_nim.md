# TrialMindKG evaluation

Backend: **nim** - NVIDIA NIM models from config/nim_models.yaml

| Group | Metric | Value | n | Status | Definition |
|---|---|---|---|---|---|
| graph | schema_valid_edge_rate | 1.0 | 5220 | computed | edges passing kg_schema.validate (domain/range, ids, evidence type, dates) |
| graph | provenance_completeness | 1.0 | 5220 | computed | edges with every provenance field filled: id, subject, predicate, object, source, source_record, evidence_type, retrieved, primary_knowledge_source, knowledge_level, agent_type, mapping_confidence |
| graph | entity_mapping_coverage | 0.94 | 166 | computed | entities resolved automatically without a NEEDS_REVIEW flag |
| graph | entity_mapping_precision | - | 0 | pending ground truth | pipeline ids that match the reviewer's confirmed ids (Trials, Drugs sheets) |
| agents | golden_pass_rate | 1.0 | 11 | computed | golden questions whose expected value is returned (data/curated/golden_questions.csv) |
| agents | querier_execution_accuracy | 0.6 | 10 | computed | generated Cypher returns exactly the gold query's evidence edges |
| agents | resolver_top1 | 0.956 | 522 | computed | ChEMBL research code / trade name -> correct molecule at rank 1 (leave-one-out) |
| agents | resolver_top1_same_active_ingredient | 0.979 | 522 | computed | as resolver_top1, but a salt / parent form of the correct molecule also counts (the graph compares drugs by parent) |
| agents | stop_reason_quote_verified | 0.978 | 90 | computed | extractions whose evidence quote appears word for word in the text |
| agents | stop_reason_agreement | - | 0 | pending ground truth | category agrees with reviewer's failure category |
| reports | evidence_supported_claim_rate | 0.897 (95% CI 0.736-0.964) | 29 | computed | drafted claims that end up passing every verifier check (citations in subgraph, numbers in cited evidence, verifier model), after at most one rewrite of a rejected claim |
| reports | first_draft_supported_rate | 0.828 (95% CI 0.655-0.924) | 29 | computed | drafted claims passing every check as first written |
| reports | unsupported_claim_rate | 0.103 (95% CI 0.036-0.264) | 29 | computed | drafted claims removed by the verifier (never shown) |
| reports | repaired_claims | 2 | 29 | computed | rejected claims rewritten once and then passing |
| reports | absence_statements | 0 | 3 | computed | drafted statements that cite nothing and only say the facts are insufficient (not claims: dropped before verification, not counted in the rates) |
| reports | mixed_claims | 0 | 26 | computed | shown claims the judge finds contradicted by other facts retrieved for the question |
| reports | causal_wording_rejections | 1 | 29 | computed | claims still rejected for causal wording on post hoc / curated evidence |
| reports | nct_id_mismatches | 0 | 26 | computed | shown claims giving a trial an NCT id the registry assigns to another trial |
| reports | negative_control_overclaims | 0 | 1 | computed | claims labelled prospectively_confirmed on negative-control questions |
| reports | latency_seconds | 53.78 | 3 | computed | mean seconds per report |
| reports | llm_only_baseline_supported_rate | 0.375 (95% CI 0.212-0.573) | 24 | computed | LLM-only (same model, no graph) claims the judge finds supported by the question's graph evidence |
| reports | llm_only_baseline_contradicted_rate | 0.042 (95% CI 0.007-0.202) | 24 | computed | LLM-only (same model, no graph) claims the judge finds contradicted by that evidence |
| reports | llm_only_baseline_nct_id_mismatches | 1 | 24 | computed | LLM-only (same model, no graph): NCT ids given to the wrong trial (registry check, no model) |
| reports | rag_baseline_supported_rate | 0.917 (95% CI 0.646-0.985) | 12 | computed | Document RAG (same model, program documents, no graph) claims the judge finds supported by the question's graph evidence |
| reports | rag_baseline_contradicted_rate | 0.0 (95% CI 0.0-0.243) | 12 | computed | Document RAG (same model, program documents, no graph) claims the judge finds contradicted by that evidence |
| reports | rag_baseline_nct_id_mismatches | 0 | 12 | computed | Document RAG (same model, program documents, no graph): NCT ids given to the wrong trial (registry check, no model) |
| reports | trialmindkg_judged_supported_rate | 0.962 (95% CI 0.811-0.993) | 26 | computed | TrialMindKG report claims the judge finds supported by the question's graph evidence |
| reports | trialmindkg_judged_contradicted_rate | 0.0 (95% CI 0.0-0.129) | 26 | computed | TrialMindKG report claims the judge finds contradicted by that evidence |
| reports | trialmindkg_judged_nct_id_mismatches | 0 | 26 | computed | TrialMindKG report: NCT ids given to the wrong trial (registry check, no model) |
| reports_all_programs | evidence_supported_claim_rate | 0.827 (95% CI 0.783-0.864) | 330 | computed | drafted claims that end up passing every verifier check (citations in subgraph, numbers in cited evidence, verifier model), after at most one rewrite of a rejected claim |
| reports_all_programs | first_draft_supported_rate | 0.739 (95% CI 0.689-0.784) | 330 | computed | drafted claims passing every check as first written |
| reports_all_programs | unsupported_claim_rate | 0.173 (95% CI 0.136-0.217) | 330 | computed | drafted claims removed by the verifier (never shown) |
| reports_all_programs | repaired_claims | 29 | 330 | computed | rejected claims rewritten once and then passing |
| reports_all_programs | absence_statements | 4 | 34 | computed | drafted statements that cite nothing and only say the facts are insufficient (not claims: dropped before verification, not counted in the rates) |
| reports_all_programs | mixed_claims | 3 | 273 | computed | shown claims the judge finds contradicted by other facts retrieved for the question |
| reports_all_programs | causal_wording_rejections | 1 | 330 | computed | claims still rejected for causal wording on post hoc / curated evidence |
| reports_all_programs | nct_id_mismatches | 0 | 273 | computed | shown claims giving a trial an NCT id the registry assigns to another trial |
| reports_all_programs | negative_control_overclaims | 0 | 1 | computed | claims labelled prospectively_confirmed on negative-control questions |
| reports_all_programs | latency_seconds | 35.95 | 34 | computed | mean seconds per report |
| reports_all_programs | llm_only_baseline_supported_rate | 0.588 (95% CI 0.529-0.645) | 272 | computed | LLM-only (same model, no graph) claims the judge finds supported by the question's graph evidence |
| reports_all_programs | llm_only_baseline_contradicted_rate | 0.099 (95% CI 0.069-0.141) | 272 | computed | LLM-only (same model, no graph) claims the judge finds contradicted by that evidence |
| reports_all_programs | llm_only_baseline_nct_id_mismatches | 1 | 272 | computed | LLM-only (same model, no graph): NCT ids given to the wrong trial (registry check, no model) |
| reports_all_programs | rag_baseline_supported_rate | 0.617 (95% CI 0.526-0.701) | 115 | computed | Document RAG (same model, program documents, no graph) claims the judge finds supported by the question's graph evidence |
| reports_all_programs | rag_baseline_contradicted_rate | 0.096 (95% CI 0.054-0.163) | 115 | computed | Document RAG (same model, program documents, no graph) claims the judge finds contradicted by that evidence |
| reports_all_programs | rag_baseline_nct_id_mismatches | 1 | 115 | computed | Document RAG (same model, program documents, no graph): NCT ids given to the wrong trial (registry check, no model) |
| reports_all_programs | trialmindkg_judged_supported_rate | 0.971 (95% CI 0.943-0.985) | 273 | computed | TrialMindKG report claims the judge finds supported by the question's graph evidence |
| reports_all_programs | trialmindkg_judged_contradicted_rate | 0.011 (95% CI 0.004-0.032) | 273 | computed | TrialMindKG report claims the judge finds contradicted by that evidence |
| reports_all_programs | trialmindkg_judged_nct_id_mismatches | 0 | 273 | computed | TrialMindKG report: NCT ids given to the wrong trial (registry check, no model) |
