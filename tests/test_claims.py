"""Tests for pipelines/claims.py (P5 controller rules)."""
import claims as cr
import nim


def test_causal_wording_only_on_prespecified_or_registry_evidence():
    c = dict(text="TULIP-1 failed because of the restricted medication rules", evidence_status="post_hoc")
    assert cr.causal_problem(dict(c, evidence_status="curated_summary")) is None
    assert "because" in cr.causal_problem(c)
    assert cr.causal_problem(dict(c, evidence_status="registry_record")) is None
    assert cr.causal_problem(dict(text="TULIP-1 missed its primary endpoint", evidence_status="post_hoc")) is None


def test_dedupe_keeps_first_section_and_records_the_other():
    a = dict(role="failure_diagnostician", text="METREO missed its primary endpoint (rate ratio 0.8)", evidence_edges=["e1"])
    b = dict(role="redesign_advisor", text="METREO missed its primary endpoint, rate ratio 0.8", evidence_edges=["e1"])
    c = dict(role="population_stratifier", text="Eosinophils >= 300 subgroup was post hoc", evidence_edges=["e2"])
    out = cr.dedupe([b, c, a], ["failure_diagnostician", "population_stratifier", "indication_explorer", "redesign_advisor"])
    assert [x["role"] for x in out] == ["failure_diagnostician", "population_stratifier"]
    assert out[0]["also_in"] == ["redesign_advisor"]


def test_indicators_take_the_weakest_cited_edge():
    class KG:
        edges = {"e1": dict(knowledge_level="knowledge_assertion", evidence_type="registry", mapping_confidence="high"),
                 "e2": dict(knowledge_level="statistical_association", evidence_type="database",
                            mapping_confidence="source_identifier"),
                 "e3": dict(knowledge_level="logical_entailment", evidence_type="computed", mapping_confidence="medium")}
    ind = cr.indicators(dict(evidence_edges=["e1", "e2"], uncertainty="low"), KG())
    assert ind == dict(evidence_strength="moderate", mapping_confidence="high", model_uncertainty="low")
    assert cr.indicators(dict(evidence_edges=["e3"], uncertainty="high"), KG())["evidence_strength"] == "limited"


def test_support_status():
    assert cr.support_status(dict(evidence_status="prespecified"), "contradicted") == "mixed"
    assert cr.support_status(dict(evidence_status="post_hoc"), "not_addressed") == "hypothesis_generating"
    assert cr.support_status(dict(evidence_status="registry_record"), "supported") == "supported"
    assert cr.contradiction_verdict(nim.NIM(backend="offline"), "x", ["f"]) == "not_addressed"
