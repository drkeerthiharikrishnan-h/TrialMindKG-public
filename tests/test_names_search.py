"""CC-15: names in search and cards. A name that Pharos leaves empty is taken from a later layer (per-field merge), ChEMBL
compounds are named from data/reference/chembl_synonyms.tsv.gz (pref_name, else the parent's), the synonyms are searchable,
an exact match ranks first, and the card index gives the same names, cards and search results as the live graph. Fixture
files only (tests/fixtures/pharos_fixture.py plus a few nodes and a tiny synonyms file)."""
import gzip
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import pharos_app  # noqa: E402
from pharos_app import PharosGraph, chembl_names, merge_node_frames  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402

from fixtures.pharos_fixture import build, write, CMP  # noqa: E402

IMA, CHILD, LONG, ORPHAN = ("CHEMBL.COMPOUND:CHEMBL941", "CHEMBL.COMPOUND:CHEMBL5", "CHEMBL.COMPOUND:CHEMBL942",
                            "CHEMBL.COMPOUND:CHEMBL999")
HP, PATH = "HP:0001250", "WP:WP1"
SYN = ("# source: ChEMBL chembl_37 (fixture)\n"
       "chembl_id\tparent_chembl_id\tpref_name\tmax_phase\tmolecule_type\tsynonyms\n"
       "CHEMBL941\tCHEMBL941\tIMATINIB\t4.0\tSmall molecule\tGleevec::TRADE_NAME|STI-571::RESEARCH_CODE|Imatinib::INN|imatinib::OTHER\n"
       "CHEMBL5\tCHEMBL941\t\t\tSmall molecule\t\n"
       "CHEMBL942\tCHEMBL942\tSOME VERY LONG COMPOUND NAME HERE\t\tSmall molecule\tImatinib::OTHER\n"
       "CHEMBL521\tCHEMBL521\tIBUPROFEN FROM CHEMBL\t4.0\tSmall molecule\tAdvil::TRADE_NAME\n")


def write_synonyms(root, text=SYN):
    p = root / "data" / "reference" / "chembl_synonyms.tsv.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt") as f:
        f.write(text)
    return p


@pytest.fixture
def repo(tmp_path):
    root = build(tmp_path / "repo")
    pharos = root / "data" / "kg" / "pharos"
    nodes = pd.read_csv(pharos / "nodes.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    extra = pd.DataFrame([dict(id=HP, category="biolink:PhenotypicFeature", name=""),             # pharos: no name
                          dict(id=IMA, category="biolink:SmallMolecule", name=""),
                          dict(id=CHILD, category="biolink:SmallMolecule", name=""),
                          dict(id=LONG, category="biolink:SmallMolecule", name=""),
                          dict(id=ORPHAN, category="biolink:SmallMolecule", name=""),
                          dict(id=PATH, category="biolink:Pathway", name="imatinib pathway")])
    write(pharos, "nodes", pd.concat([nodes, extra], ignore_index=True).fillna(""))
    ot = root / "data" / "kg" / "opentargets"
    write(ot, "nodes", pd.DataFrame([dict(id=HP, category="biolink:PhenotypicFeature", name="Seizure", symbol="")]))
    write_synonyms(root)
    return root


@pytest.fixture
def both(repo):
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    return stats, CardIndex(stats["path"]), PharosGraph(root=repo)


def test_merge_takes_the_first_non_empty_value_per_field():
    a = pd.DataFrame([dict(id="x", category="biolink:Gene", name="", symbol="S1"), dict(id="y", category="c", name="", symbol="")])
    b = pd.DataFrame([dict(id="x", category="biolink:Other", name="Later name", symbol="S2", extra="e"), dict(id="z", category="c", name="Z")])
    m = merge_node_frames([a, b]).set_index("id")
    assert list(m.index) == ["x", "y", "z"]                                   # order of first appearance
    assert (m.loc["x", "category"], m.loc["x", "name"], m.loc["x", "symbol"], m.loc["x", "extra"]) == ("biolink:Gene", "Later name", "S1", "e")
    assert m.loc["y", "name"] == "" and m.loc["z", "extra"] == ""            # nothing invented


def test_a_name_from_a_later_layer_survives(both):
    _, idx, g = both
    assert g.nodes[HP]["name"] == "Seizure" and g.label(HP) == "Seizure"
    assert idx.card(HP)["about"]["label"] == "Seizure" and idx.search("seizure")[0]["id"] == HP
    assert pharos_app.PharosGraph(root=g.root).nodes["NCBIGene:5743"]["name"] == "PTGS2"       # pharos fields still first


def test_chembl_names_parse_name_parent_and_synonyms(repo):
    c = chembl_names(repo)
    assert c["source"] == "source: ChEMBL chembl_37 (fixture)" and c["path"].endswith("chembl_synonyms.tsv.gz")
    assert c["names"][IMA] == "IMATINIB" and c["names"][CHILD] == "IMATINIB"                  # CHILD: its parent's name
    assert ORPHAN not in c["names"]
    assert c["synonyms"][IMA] == ["Gleevec", "STI-571"]            # '::' part only, 'Imatinib' / 'imatinib' = the name, no duplicates
    assert CHILD not in c["synonyms"]
    assert chembl_names(repo / "nowhere") == dict(names={}, synonyms={}, source="", path=None)


def test_compounds_are_named_from_the_file_and_nothing_is_invented(both):
    _, idx, g = both
    assert g.label(IMA) == g.label(CHILD) == "IMATINIB"
    assert g.label(ORPHAN) == ORPHAN and "name" not in g.nodes[ORPHAN]                        # no name anywhere: the id
    assert g.label(CMP) == "IBUPROFEN"                                                       # a layer's own name is never replaced
    for nid in (IMA, CHILD, ORPHAN, CMP):
        assert idx.card(nid)["about"]["label"] == g.label(nid)


def test_synonym_search_finds_the_compound(both):
    _, idx, g = both
    for q in ("gleevec", "STI-571", "Gleevec"):
        assert [h["id"] for h in g.search(q)] == [IMA], q
        assert idx.search(q) == g.search(q)
    assert g.search("gleevec")[0]["label"] == "IMATINIB"


def test_an_exact_match_ranks_above_a_substring_match_even_when_longer(both):
    _, idx, g = both
    ids = [h["id"] for h in g.search("imatinib")]
    # exact name (IMATINIB, and CHEMBL5 which takes its parent's name), then the long-named compound whose exact synonym is
    # 'Imatinib', and only then the SHORTER 'imatinib pathway' that merely contains the word
    assert ids == [CHILD, IMA, LONG, PATH]
    assert idx.search("imatinib") == g.search("imatinib")


def test_index_search_equals_live_search_for_many_queries(both):
    _, idx, g = both
    for q in ("imatinib", "IMATINIB", "sti", "gleevec", "advil", "chembl941", "seizure", "seiz", "pathway", "ptgs2", "", "zzz", "a"):
        assert idx.search(q) == g.search(q), q
    assert idx.search("a", limit=3) == g.search("a", limit=3)


def test_cards_of_the_new_nodes_equal_live_cards(both):
    _, idx, g = both
    for nid in (HP, IMA, CHILD, LONG, ORPHAN, PATH, CMP):
        live, fast = g.card(nid), idx.card(nid)
        assert fast["served_from"] == "card index"
        assert {k: v for k, v in fast.items() if k not in ("served_from", "mechanism_neighbours")} == live, nid


def test_meta_records_the_names_file_and_stale_notices_a_change(repo):
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    idx = CardIndex(stats["path"])
    ns = json.loads(idx.status()["names_source"])
    assert ns["path"].endswith("chembl_synonyms.tsv.gz") and ns["source"] == "source: ChEMBL chembl_37 (fixture)" and ns["named"] == 4
    assert idx.stale(repo) == []
    p = write_synonyms(repo, SYN + "CHEMBL7\tCHEMBL7\tNEW\t\tSmall molecule\t\n")
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
    assert idx.stale(repo) == ["data/reference/chembl_synonyms.tsv.gz"]


def test_without_the_synonyms_file_compounds_keep_their_ids(tmp_path):
    root = build(tmp_path / "bare")
    stats = build_index(root, chunk=3, log=lambda *_: None)
    idx, g = CardIndex(stats["path"]), PharosGraph(root=root)
    assert json.loads(idx.status()["names_source"])["path"] is None
    assert idx.search("gleevec") == g.search("gleevec") == []
    assert idx.card(CMP)["about"]["label"] == g.label(CMP) == "IBUPROFEN"
