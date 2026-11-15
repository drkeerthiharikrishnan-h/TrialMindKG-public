"""Persona files and the wording tables behind the web app's Start-here page and entity card."""
import pytest

from personas import (load_personas, wording, evidence_kind, confidence_word, predicate_phrase, PERSONAS_DIR,
                      EVIDENCE_KINDS)


def test_five_personas_load_with_every_required_field():
    ps = load_personas()
    assert [p["id"] for p in ps][:2] == ["physician_scientist", "clinician"] and len(ps) == 5   # order key
    ids = {p["id"] for p in ps}
    assert {"physician_scientist", "clinician", "bench_scientist", "drug_developer", "computational_biologist"} == ids
    for p in ps:
        assert p["questions"] and all(q["text"] and q["tab"] for q in p["questions"])
        assert p["color"].startswith("#") and p["emoji"] and p["welcome"]


def test_broken_persona_file_is_rejected(tmp_path):
    (tmp_path / "x.yaml").write_text("name: X\n")
    with pytest.raises(ValueError, match="missing"):
        load_personas(tmp_path)
    good = (PERSONAS_DIR / "clinician.yaml").read_text().replace("start_tab: pharos", "start_tab: nowhere")
    (tmp_path / "x.yaml").write_text(good)
    with pytest.raises(ValueError, match="unknown tab"):
        load_personas(tmp_path)


def test_example_questions_name_entities_the_demo_fixture_holds():
    """Example questions must stay answerable: they use the entities of the fixed demo questions."""
    known = ("PTGS2", "Alzheimer", "diabetes", "ibuprofen", "SOD1", "TULIP-1", "METREO", "dal-GenE")
    for p in load_personas():
        for q in p["questions"]:
            assert any(k.lower() in q["text"].lower() for k in known), (p["id"], q["text"])


def test_evidence_kind_rules():
    assert evidence_kind(dict(predicate="biolink:gene_associated_with_condition", knowledge_level="text_co_occurrence",
                              source="JensenLab Text Mining")) == "text mining"
    assert evidence_kind(dict(predicate="biolink:genetically_associated_with", source="TIGA (GWAS Catalog)")) == "human genetics"
    assert evidence_kind(dict(predicate="biolink:directly_physically_interacts_with", source="CHEMBL")) == "binding or activity"
    assert evidence_kind(dict(predicate="biolink:gene_associated_with_condition", knowledge_level="knowledge_assertion",
                              source="DisGeNET")) == "curated"
    assert evidence_kind(dict(predicate="biolink:actively_involved_in", source="GO annotations")) == "annotation"
    assert evidence_kind(dict(predicate="biolink:gene_associated_with_condition", knowledge_level="statistical_association",
                              source="Expression Atlas")) == "expression or statistics"
    assert evidence_kind(dict(predicate="biolink:treats_or_applied_or_studied_to_treat", source="DrugCentral Indication")) == "clinical"
    kinds = {k for k, _, _ in EVIDENCE_KINDS}
    assert {"text mining", "human genetics", "binding or activity", "curated", "annotation", "expression or statistics",
            "clinical"} <= kinds


def test_words():
    assert predicate_phrase("biolink:gene_associated_with_condition") == "is associated with"
    assert predicate_phrase("tmkg:some_new_thing") == "some new thing"
    assert confidence_word("keep", 0.9) == "kept" and confidence_word("prune", 0.1) == "pruned"
    assert confidence_word(None, None) == "unscored" and confidence_word(predicted=True) == "predicted"
    w = wording()
    assert w["knowledge_levels"]["text_co_occurrence"] == "text mining only"
    assert w["categories"]["SmallMolecule"] == "compound" and len(w["evidence_kinds"]) == len(EVIDENCE_KINDS)
