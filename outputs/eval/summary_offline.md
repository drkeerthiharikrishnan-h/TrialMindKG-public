# TrialMindKG evaluation

Backend: **offline** - offline stand-ins: rule-based placeholders that prove the pipeline runs; these are NOT model scores

| Group | Metric | Value | n | Status | Definition |
|---|---|---|---|---|---|
| graph | schema_valid_edge_rate | 1.0 | 4336 | computed | edges passing kg_schema.validate (domain/range, ids, evidence type, dates) |
| graph | provenance_completeness | 1.0 | 4336 | computed | edges with every provenance field filled: id, subject, predicate, object, source, source_record, evidence_type, retrieved, primary_knowledge_source, knowledge_level, agent_type, mapping_confidence |
| graph | entity_mapping_coverage | 0.94 | 166 | computed | entities resolved automatically without a NEEDS_REVIEW flag |
| graph | entity_mapping_precision | - | 0 | pending ground truth | pipeline ids that match the reviewer's confirmed ids (Trials, Drugs sheets) |
| agents | golden_pass_rate | 1.0 | 11 | computed | golden questions whose expected value is returned (data/curated/golden_questions.csv) |
| agents | querier_execution_accuracy | 1.0 | 10 | computed | generated Cypher returns exactly the gold query's evidence edges |
| agents | resolver_top1 | 0.944 | 522 | computed | ChEMBL research code / trade name -> correct molecule at rank 1 (leave-one-out) |
| agents | resolver_top1_same_active_ingredient | 0.964 | 522 | computed | as resolver_top1, but a salt / parent form of the correct molecule also counts (the graph compares drugs by parent) |
| agents | stop_reason_quote_verified | 1.0 | 90 | computed | extractions whose evidence quote appears word for word in the text |
| agents | stop_reason_agreement | - | 0 | pending ground truth | category agrees with reviewer's failure category |
| reports | evidence_supported_claim_rate | 1.0 (95% CI 0.871-1.0) | 26 | computed | drafted claims that end up passing every verifier check (citations in subgraph, numbers in cited evidence, verifier model), after at most one rewrite of a rejected claim |
| reports | first_draft_supported_rate | 1.0 (95% CI 0.871-1.0) | 26 | computed | drafted claims passing every check as first written |
| reports | unsupported_claim_rate | 0.0 (95% CI 0.0-0.129) | 26 | computed | drafted claims removed by the verifier (never shown) |
| reports | repaired_claims | 0 | 26 | computed | rejected claims rewritten once and then passing |
| reports | mixed_claims | 0 | 26 | computed | shown claims the judge finds contradicted by other facts retrieved for the question |
| reports | causal_wording_rejections | 0 | 26 | computed | claims still rejected for causal wording on post hoc / curated evidence |
| reports | nct_id_mismatches | 0 | 26 | computed | shown claims giving a trial an NCT id the registry assigns to another trial |
| reports | negative_control_overclaims | 0 | 1 | computed | claims labelled prospectively_confirmed on negative-control questions |
| reports | latency_seconds | 0.24 | 3 | computed | mean seconds per report |
| reports | llm_only_baseline_supported_rate | - | 0 | needs NIM | LLM-only claims supported by the graph evidence |
| reports_all_programs | evidence_supported_claim_rate | 0.99 (95% CI 0.965-0.997) | 206 | computed | drafted claims that end up passing every verifier check (citations in subgraph, numbers in cited evidence, verifier model), after at most one rewrite of a rejected claim |
| reports_all_programs | first_draft_supported_rate | 0.99 (95% CI 0.965-0.997) | 206 | computed | drafted claims passing every check as first written |
| reports_all_programs | unsupported_claim_rate | 0.01 (95% CI 0.003-0.035) | 206 | computed | drafted claims removed by the verifier (never shown) |
| reports_all_programs | repaired_claims | 0 | 206 | computed | rejected claims rewritten once and then passing |
| reports_all_programs | mixed_claims | 0 | 204 | computed | shown claims the judge finds contradicted by other facts retrieved for the question |
| reports_all_programs | causal_wording_rejections | 2 | 206 | computed | claims still rejected for causal wording on post hoc / curated evidence |
| reports_all_programs | nct_id_mismatches | 0 | 204 | computed | shown claims giving a trial an NCT id the registry assigns to another trial |
| reports_all_programs | negative_control_overclaims | 0 | 1 | computed | claims labelled prospectively_confirmed on negative-control questions |
| reports_all_programs | latency_seconds | 0.23 | 34 | computed | mean seconds per report |
| reports_all_programs | llm_only_baseline_supported_rate | - | 0 | needs NIM | LLM-only claims supported by the graph evidence |
