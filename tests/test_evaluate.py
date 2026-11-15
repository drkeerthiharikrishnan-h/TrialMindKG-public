"""Tests for pipelines/evaluate.py (offline run: plumbing and honest statuses, not model quality)."""
import pytest
import evaluate
from kg_query import KGIndex
from nim import NIM


@pytest.fixture(scope="module")
def summary():
    return evaluate.run(NIM(backend="offline"), KGIndex())


def test_graph_metrics_are_computed(summary):
    g = summary["graph"]
    assert g["schema_valid_edge_rate"]["value"] == 1.0 and g["provenance_completeness"]["value"] == 1.0
    cov = g["entity_mapping_coverage"]
    assert 0 < cov["value"] < 1 and cov["by_type"]["trial"]["rate"] == 1.0


def test_unavailable_metrics_say_why(summary):
    assert summary["graph"]["entity_mapping_precision"]["status"] == "pending ground truth"
    assert summary["reports"]["llm_only_baseline_supported_rate"]["status"] == "needs NIM"
    assert "NOT model scores" in summary["note"]


def test_benchmarks_and_negative_control(summary):
    rows = {r["id"]: r for r in summary["reports"]["per_question"]}
    assert set(rows) == {q["id"] for q in evaluate.load_questions()}
    assert rows["B03"]["kind"] == "negative_control" and rows["B03"]["overclaims"] == []
    assert all(r["supported"] > 0 for r in rows.values())
    md = evaluate.to_markdown(summary)
    assert "| graph | schema_valid_edge_rate | 1.0 |" in md


def test_wilson_interval():
    assert evaluate.wilson(0, 0) is None
    lo, hi = evaluate.wilson(20, 30)
    assert lo < 20 / 30 < hi and (lo, hi) == (0.488, 0.808)


def test_program_questions_cover_every_program():
    qs = evaluate.program_questions(KGIndex())
    assert len(qs) == 34 and qs[18]["question"].startswith("Why did Anifrolumab fail in TULIP-1")


def test_nct_mismatch_detects_swapped_ids():
    kg = KGIndex()
    assert evaluate.nct_mismatches("TULIP-2 (NCT02446912) met its endpoint", kg) == [("NCT02446912", "TULIP-1")]
    assert evaluate.nct_mismatches("TULIP-1 (NCT02446912) missed", kg) == []
    assert evaluate.nct_mismatches("NCT02446912 missed", kg) == []


def test_comparison_metrics_count_verdicts():
    rows = [dict(id="B01", kind="benchmark", system=s, claim="x", verdict=v, nct_mismatches=[])
            for s, v in (("llm_only", "contradicted"), ("llm_only", "not_addressed"), ("trialmindkg", "supported"))]
    m = evaluate.comparison_metrics(rows)
    assert m["llm_only_baseline_supported_rate"]["value"] == 0.0
    assert m["llm_only_baseline_contradicted_rate"]["value"] == 0.5
    assert m["trialmindkg_judged_supported_rate"]["value"] == 1.0


def test_all_program_reports_offline(summary):
    a = summary["reports_all_programs"]
    assert a["evidence_supported_claim_rate"]["n"] >= summary["reports"]["evidence_supported_claim_rate"]["n"]
    assert len(a["per_question"]) == 34


def test_nct_mismatch_accepts_acronym_prefix():
    kg = KGIndex()
    assert evaluate.nct_mismatches("Tofersen failed in the VALOR trial (Part C, NCT02623699)", kg) == []
