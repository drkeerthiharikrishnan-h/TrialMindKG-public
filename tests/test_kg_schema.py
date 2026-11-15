"""Tests for pipelines/kg_schema.py: the validation gate must catch every kind of bad graph."""
import re
import pytest
from kg_schema import KG, validate, edge_id, CATEGORIES, PREDICATES, ADC_CATEGORIES, ADC_PREDICATES
from snapshot import ROOT

PROV = dict(primary_knowledge_source="infores:clinicaltrials-gov", knowledge_level="knowledge_assertion",
            agent_type="manual_agent", mapping_confidence="high")


def small():
    kg = KG()
    kg.add_node("P01", "tmkg:ClinicalTrialProgram", name="x")
    kg.add_node("NCT:NCT01900665", "biolink:ClinicalTrial")
    kg.add_edge("P01", "tmkg:has_trial", "NCT:NCT01900665", "ClinicalTrials.gov", "NCT01900665", "registry",
                "2026-10-04T00:00:00Z", **PROV)
    return kg


def test_valid_graph_has_no_problems():
    assert validate(small()) == []


def test_same_edge_added_once():
    kg = small()
    kg.add_edge("P01", "tmkg:has_trial", "NCT:NCT01900665", "ClinicalTrials.gov", "NCT01900665", "registry",
                "2026-10-04T00:00:00Z")
    assert len(kg.edges) == 1


@pytest.mark.parametrize("break_it, expected", [
    (lambda kg: kg.add_node("NCT:12345", "biolink:ClinicalTrial"), "does not match pattern"),
    (lambda kg: kg.add_node("X:1", "biolink:Banana"), "unknown category"),
    (lambda kg: kg.add_edge("P01", "tmkg:has_trial", "NCT:NCT99999999", "s", "r", "registry", "2026-01-01"), "dangling"),
    (lambda kg: kg.add_edge("NCT:NCT01900665", "tmkg:has_trial", "P01", "s", "r", "registry", "2026-01-01"), "not allowed"),
    (lambda kg: kg.add_edge("P01", "tmkg:has_trial", "NCT:NCT01900665", "s", "r", "guess", "2026-01-01"), "evidence_type"),
    (lambda kg: kg.add_edge("P01", "tmkg:has_trial", "NCT:NCT01900665", "s", "r", "registry", "yesterday"), "ISO date"),
    (lambda kg: kg.add_edge("P01", "tmkg:has_trial", "NCT:NCT01900665", "", "r", "registry", "2026-01-01"), "missing source"),
    (lambda kg: next(iter(kg.edges.values())).update(object="P01"), "id does not match"),
    (lambda kg: next(iter(kg.edges.values())).pop("agent_type"), "missing agent_type"),
    (lambda kg: next(iter(kg.edges.values())).update(knowledge_level="hunch"), "not a BioLink value"),
])
def test_validation_catches(break_it, expected):
    kg = small()
    break_it(kg)
    assert any(expected in p for p in validate(kg)), validate(kg)


def test_edge_id_is_stable():
    assert edge_id("a", "p", "b", "s", "r") == edge_id("a", "p", "b", "s", "r") != edge_id("a", "p", "b", "s", "r2")


# downloaded copy if present, else the committed data/reference copy (4.4.6), so the check runs on every machine
BIOLINK = ROOT / "data" / "external" / "biolink" / "biolink-model.yaml"
if not BIOLINK.exists():
    BIOLINK = ROOT / "data" / "reference" / "biolink-model.yaml"

@pytest.mark.skipif(not BIOLINK.exists(), reason="run scripts/download_external_data.sh for the BioLink model")
def test_biolink_terms_exist_in_model():
    """CLAUDE.md rule 6: biolink: only for real BioLink classes/predicates."""
    import yaml
    y = yaml.safe_load(BIOLINK.read_text())
    def words(camel):
        return " ".join(w if w.isupper() else w.lower()
                        for w in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+", camel))
    for c in list(CATEGORIES) + list(ADC_CATEGORIES):
        if c.startswith("biolink:"):
            assert words(c.split(":")[1]) in y["classes"], c
    for p in list(PREDICATES) + list(ADC_PREDICATES):
        if p.startswith("biolink:"):
            assert p.split(":")[1].replace("_", " ") in y["slots"], p


def test_adc_profile_rejects_seed_only_types():
    kg = KG()
    kg.add_node("P01", "tmkg:ClinicalTrialProgram")
    assert any("unknown category" in p for p in validate(kg, "adc"))


@pytest.mark.skipif(not BIOLINK.exists(), reason="run scripts/download_external_data.sh for the BioLink model")
def test_provenance_enums_match_biolink():
    import yaml
    from kg_schema import KNOWLEDGE_LEVELS, AGENT_TYPES
    y = yaml.safe_load(BIOLINK.read_text())
    assert KNOWLEDGE_LEVELS == set(y["enums"]["KnowledgeLevelEnum"]["permissible_values"])
    assert AGENT_TYPES == set(y["enums"]["AgentTypeEnum"]["permissible_values"])
    for s in ("primary knowledge source", "aggregator knowledge source", "supporting data source", "knowledge level",
              "agent type"):
        assert s in y["slots"], s


def test_every_tmkg_term_has_a_biolink_parent():
    from kg_schema import BIOLINK_PARENT, biolink_term
    terms = list(CATEGORIES) + list(PREDICATES) + list(ADC_CATEGORIES) + list(ADC_PREDICATES)
    missing = [t for t in terms if t.startswith("tmkg:") and t not in BIOLINK_PARENT]
    assert not missing
    assert biolink_term("biolink:Drug") == "biolink:Drug"
    assert biolink_term("tmkg:OutcomeMeasure") == "biolink:StudyResult"


@pytest.mark.skipif(not BIOLINK.exists(), reason="run scripts/download_external_data.sh for the BioLink model")
def test_biolink_parents_exist_in_model():
    """CLAUDE.md rule 6: every parent is a real BioLink class (categories) or slot (predicates)."""
    import yaml
    from kg_schema import BIOLINK_PARENT
    y = yaml.safe_load(BIOLINK.read_text())
    for term, parent in BIOLINK_PARENT.items():
        name = parent.split(":")[1]
        if term.split(":")[1][0].isupper():
            words = " ".join(w.lower() for w in re.findall(r"[A-Z][a-z]+", name))
            assert words in y["classes"], parent
        else:
            assert name.replace("_", " ") in y["slots"], parent
    phases = set(y["enums"]["ResearchPhaseEnum"]["permissible_values"])
    import seed_kg
    assert set(seed_kg.RESEARCH_PHASES) == phases


@pytest.mark.parametrize("edge, expected", [
    (dict(evidence_type="registry"), "source_identifier"),
    (dict(evidence_type="curator_confirmed", match_rule="curator_override:high"), "high"),
    (dict(evidence_type="registry", match_rule="acronym_exact"), "high"),
    (dict(evidence_type="registry", match_rule="title_word"), "medium"),
    (dict(evidence_type="registry", chembl_match="exact_name_or_synonym+curated_alias"), "high"),
    (dict(evidence_type="curated", chembl_match="unnamed_top_hit_NEEDS_REVIEW"), "low"),
    (dict(evidence_type="curated", chembl_match="unnamed_top_hit+curator_confirmed"), "high"),
    (dict(evidence_type="computed", match_rule="MeSH term = MONDO exact label"), "medium"),
    (dict(evidence_type="curated", in_registry_arms=False), "medium"),
    (dict(evidence_type="computed", mapping_confidence="low"), "low"),
])
def test_mapping_confidence(edge, expected):
    from kg_schema import mapping_confidence
    assert mapping_confidence(edge) == expected


def test_weakest():
    from kg_schema import weakest
    assert weakest(["source_identifier", "medium", "high"]) == "medium"


def test_biolink_annotate_and_validation():
    from kg_schema import biolink_annotate
    kg = small()
    next(iter(kg.edges.values())).pop("mapping_confidence")
    assert any("missing mapping_confidence" in p for p in validate(kg))
    biolink_annotate(kg)
    e = next(iter(kg.edges.values()))
    assert (e["biolink_predicate"], e["original_predicate"], e["mapping_confidence"]) == \
        ("biolink:related_to", "tmkg:has_trial", "source_identifier")
    assert kg.nodes["P01"]["biolink_category"] == "biolink:Study"
    assert validate(kg) == []
    e["mapping_confidence"] = "sure"
    assert any("mapping_confidence" in p for p in validate(kg))
