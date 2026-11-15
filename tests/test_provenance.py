"""Tests for pipelines/provenance.py: mapping rules, registry check, committed graph fully annotated."""
import csv, json
import provenance
from kg_schema import KG
from snapshot import Snapshot, ROOT


def edge(source, et, pred):
    return dict(source=source, evidence_type=et, predicate=pred)


def test_first_matching_row_wins():
    rows = provenance.load_map()
    assert provenance.rule_for(edge("ClinicalTrials.gov", "curator_confirmed", "tmkg:has_trial"), rows)["agent_type"] \
        == "manual_validation_of_automated_agent"
    assert provenance.rule_for(edge("ADAE", "synthetic", "tmkg:observed"), rows)["primary_knowledge_source"] \
        == "tmkg:SYNTHETIC-internal"
    r = provenance.rule_for(edge("curated: sponsor press release (x)", "literature", "biolink:interacts_with"), rows)
    assert "press release" in r["note"]
    assert provenance.rule_for(edge("Unknown DB", "database", "biolink:related_to"), rows) is None


def test_annotate_checks_registry_and_coverage(tmp_path):
    snap = Snapshot(mode="offline")
    kg = KG()
    kg.add_node("P01", "tmkg:ClinicalTrialProgram")
    kg.add_node("NCT:NCT01900665", "biolink:ClinicalTrial")
    eid = kg.add_edge("P01", "tmkg:has_trial", "NCT:NCT01900665", "ClinicalTrials.gov", "x", "registry", "2026-01-01")
    assert provenance.annotate(kg, snap) == []
    assert kg.edges[eid]["supporting_data_source"] == json.dumps(["infores:clinicaltrials-gov"])
    bad = tmp_path / "map.csv"
    rows = provenance.load_map()
    rows[0]["primary_knowledge_source"] = "infores:clinicaltrials"          # deprecated in the registry
    with open(bad, "w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0])); w.writeheader(); w.writerows(rows)
    assert any("deprecated" in p for p in provenance.annotate(kg, snap, bad))


def test_committed_graphs_are_fully_annotated():
    for f in (ROOT / "outputs" / "kg_edges.csv", ROOT / "outputs" / "adc" / "kg_edges.csv"):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            assert r["primary_knowledge_source"].startswith(("infores:", "tmkg:")) and r["knowledge_level"] \
                and r["agent_type"], r["id"]
