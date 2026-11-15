"""sources/dto.yaml: the Drug Target Ontology card builds on a small fixture shaped like data/pharos/dto.tsv.gz."""
import gzip
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
from kgbuilder import engine  # noqa: E402
from kgbuilder.biolink import DEFAULT as BIOLINK_YAML, BioLink  # noqa: E402

pytestmark = pytest.mark.skipif(not BIOLINK_YAML.exists(), reason="biolink-model.yaml not downloaded")

# rows copied in shape from the real table: a target appears once per DTO class, some targets have no class at all
DTO = pd.DataFrame({
    "id": ["1", "1", "1", "1", "2", "3"],
    "UniProt": ["P32929", "P32929", "P32929", "P32929", "A4D0Y5", "P00533"],
    "Symbol": ["CTH", "CTH", "CTH", "CTH", "C7orf77", "EGFR"],
    "DTO Class": ["Cystathionine gamma-lyase", "Lyase", "Enzyme", "", "", "Kinase"],
    "DTO ID": ["DTO:05004572", "DTO:05007652", "DTO:05007624", "", "", "DTO:05007741"],
    "DTO Definition": ["", "", "", "", "", ""]})


@pytest.fixture
def built(tmp_path):
    d = tmp_path / "pharos"
    d.mkdir()
    with gzip.open(d / "dto.tsv.gz", "wt") as f:
        DTO.to_csv(f, sep="\t", index=False)
    bl = BioLink()
    card = engine.load_card("sources/dto.yaml")
    return card, engine.build(card, tables_dir=d, bl=bl), bl


def test_dto_card_is_valid_biolink(built):
    card, _, bl = built
    assert engine.check_card(card, bl) == []
    assert card["layer"] == "L0" and card["name"] == "dto"


def test_dto_card_builds_protein_to_class_edges(built):
    _, b, _ = built
    assert b.problems == []
    e = b.edges
    assert set(e["predicate"]) == {"biolink:member_of"}
    assert sorted(zip(e["subject"], e["object"])) == [
        ("UniProtKB:P00533", "DTO:05007741"), ("UniProtKB:P32929", "DTO:05004572"),
        ("UniProtKB:P32929", "DTO:05007624"), ("UniProtKB:P32929", "DTO:05007652")]
    # rows with no DTO ID make no edge and no invented object
    assert not e["subject"].eq("UniProtKB:A4D0Y5").any()
    cats = dict(zip(b.nodes["id"], b.nodes["category"]))
    assert cats["DTO:05007652"] == "biolink:ProteinFamily" and cats["UniProtKB:P32929"] == "biolink:Protein"
    assert dict(zip(b.nodes["id"], b.nodes["name"]))["DTO:05007652"] == "Lyase"


def test_dto_edges_carry_provenance_and_are_never_pruned(built):
    card, b, _ = built
    for col in ("primary_knowledge_source", "knowledge_level", "agent_type", "source_record"):
        assert b.edges[col].notna().all(), col
    assert set(b.edges["primary_knowledge_source"]) == {"infores:pharos"}
    assert card["prune"]["never_prune_predicates"] == ["biolink:member_of"]


def test_unlisted_prefix_is_a_problem_unless_the_card_declares_it(tmp_path):
    d = tmp_path / "pharos"
    d.mkdir()
    with gzip.open(d / "dto.tsv.gz", "wt") as f:
        DTO.to_csv(f, sep="\t", index=False)
    card = engine.load_card("sources/dto.yaml")
    card.pop("extra_prefixes")
    problems = engine.build(card, tables_dir=d, bl=BioLink()).problems
    assert len(problems) == 1 and "prefixes not listed by BioLink" in problems[0] and "DTO" in problems[0]
