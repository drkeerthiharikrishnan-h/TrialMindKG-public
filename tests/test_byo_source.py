"""The 'bring your own source' example (docs/BRING_YOUR_OWN_SOURCE.md) must keep building exactly as documented."""
import yaml
from kgbuilder import engine
from kgbuilder.biolink import BioLink

CARD_PATH = engine.ROOT / "examples" / "byo_source" / "lab_screen.yaml"
BL = BioLink()


def test_example_card_is_valid_biolink_and_labelled_synthetic():
    card = engine.load_card(CARD_PATH)
    assert engine.check_card(card, BL) == []
    assert "SYNTHETIC" in card["version"] and "SYNTHETIC" in card["edges"][0]["source"]


def test_example_builds_as_documented():
    card = engine.load_card(CARD_PATH)
    b = engine.build(card, tables_dir=engine.ROOT / card["tables_dir"], bl=BL)
    assert b.problems == []
    assert len(b.nodes) == 9 and len(b.edges) == 6          # numbers quoted in the tutorial
    assert b.unmapped.empty or int(b.unmapped["rows"].sum()) == 0
    for col in ("primary_knowledge_source", "knowledge_level", "agent_type", "source"):
        assert b.edges[col].notna().all() and (b.edges[col] != "").all(), col
    cats = b.nodes.set_index("id")["category"]
    assert set(cats) == {"biolink:SmallMolecule", "biolink:Protein"}
    assert all(i.startswith(("CHEMBL.COMPOUND:", "UniProtKB:")) for i in cats.index)


def test_example_protein_ids_are_the_same_system_as_the_pharos_core():
    ex = engine.load_card(CARD_PATH)["edges"][0]["object"]
    pharos = yaml.safe_load((engine.ROOT / "sources" / "pharos.yaml").read_text())
    pharos_prefixes = {n["id"].get("prefix") for n in pharos["nodes"] if n["category"] == "biolink:Protein"}
    assert ex["prefix"] in pharos_prefixes                    # the join key that makes the layers meet on one node
