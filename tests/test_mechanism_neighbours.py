"""Tests for pipelines/mechanism_neighbours.py (CC-23) and its card-index table: the tiny Pharos fixture plus a fixture
ChEMBL 37 layer (mechanisms, activities, indications) and a fixture DTO table. No real data."""
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import mechanism_neighbours as mn  # noqa: E402
from pharos_app import KGX  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402

from fixtures.pharos_fixture import build, edge, write, GENE, PROT, DIS1, DIS2  # noqa: E402

COX1, OTHER, LONE = "UniProtKB:P23219", "UniProtKB:Q99999", "UniProtKB:Q11111"
DRUGS = [f"CHEMBL.COMPOUND:CHEMBL{i}" for i in (25, 1, 2, 3, 4)]
ACT_ONLY = "CHEMBL.COMPOUND:CHEMBL9"
DTO = pd.DataFrame({"id": ["1"] * 3 + ["2"] * 2 + ["3"] * 2 + ["4"],
                    "UniProt": ["P35354", "P35354", "P35354", "P23219", "P23219", "Q99999", "Q99999", "Q11111"],
                    "Symbol": ["PTGS2", "PTGS2", "PTGS2", "PTGS1", "PTGS1", "X", "X", "Y"],
                    "DTO Class": ["Prostaglandin G/H synthase 2", "Cyclooxygenase", "Enzyme", "Cyclooxygenase", "Enzyme",
                                  "Cyclooxygenase", "Enzyme", "Enzyme"],
                    "DTO ID": ["DTO:SELF", "DTO:COX", "DTO:ENZ", "DTO:COX", "DTO:ENZ", "DTO:COX", "DTO:ENZ", "DTO:ENZ"],
                    "DTO Definition": [""] * 8})


def chembl_layer(root):
    nodes = pd.DataFrame([dict(id=COX1, category="biolink:Protein", name="Prostaglandin G/H synthase 1", symbol="PTGS1"),
                          dict(id=OTHER, category="biolink:Protein", name="Other synthase"),
                          dict(id=ACT_ONLY, category="biolink:SmallMolecule", name="TOOLCOMPOUND")] +
                         [dict(id=d, category="biolink:SmallMolecule", name=n)
                          for d, n in zip(DRUGS, ["ASPIRIN", "DRUG-A", "DRUG-B", "DRUG-C", "DRUG-D"])])
    phases = ["4", "1", "3", "2", ""]
    e = [edge(200 + i, d, "biolink:directly_physically_interacts_with", COX1, "ChEMBL 37 mechanism", "chembl37", "37",
              mechanism="Cyclooxygenase inhibitor", action_type="INHIBITOR", max_phase=p)
         for i, (d, p) in enumerate(zip(DRUGS, phases))]
    e.append(edge(210, ACT_ONLY, "biolink:directly_physically_interacts_with", OTHER, "ChEMBL 37 activity", "chembl37",
                  "37", pchembl_max="8"))                                   # an activity only: not a mechanism target
    e += [edge(220, DRUGS[0], "biolink:treats_or_applied_or_studied_to_treat", DIS1, "ChEMBL 37 drug indication",
               "chembl37", "37", max_phase_for_ind="2"),
          edge(221, DRUGS[0], "biolink:treats_or_applied_or_studied_to_treat", DIS2, "ChEMBL 37 drug indication",
               "chembl37", "37", max_phase_for_ind="4"),
          edge(222, DRUGS[0], "biolink:treats_or_applied_or_studied_to_treat", "EFO:0000001", "ChEMBL 37 drug indication",
               "chembl37", "37", max_phase_for_ind="4")]                    # not MONDO: not listed
    cols = KGX + ["mechanism", "action_type", "max_phase", "pchembl_max", "max_phase_for_ind"]
    (root / "sources" / "chembl37.yaml").write_text("name: chembl37\n")
    write(root / "data" / "kg" / "chembl37", "nodes", nodes)
    write(root / "data" / "kg" / "chembl37", "edges", pd.DataFrame(e).reindex(columns=cols))
    write(root / "data" / "pharos", "dto", DTO)


@pytest.fixture(scope="module")
def idx(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("repo"))
    chembl_layer(root)
    stats = build_index(root, chunk=4, log=lambda *_: None)
    return CardIndex(stats["path"])


def test_family_is_the_most_specific_class_with_other_members():
    dto = mn.read_dto_frame(DTO)
    fam, members = mn.families(pd.DataFrame(columns=["id", "subject", "object"]), dto, {})
    assert fam[PROT]["id"] == "DTO:COX" and fam[PROT]["size"] == 3        # DTO:SELF has only PTGS2 itself
    assert fam[PROT]["name"] == "Cyclooxygenase" and fam[PROT]["edge"] is None
    assert fam[LONE]["id"] == "DTO:ENZ" and members["DTO:COX"] == sorted([PROT, COX1, OTHER])


def test_gene_card_lists_mechanism_members_with_drugs_and_indications(idx):
    c = idx.card(GENE)["mechanism_neighbours"]
    assert c["kind"] == "gene" and c["empty"] is None and c["family"]["id"] == "DTO:COX"
    assert c["header"] == "Drugs that act on other members of this gene's family (Cyclooxygenase) and what they treat"
    assert c["note"] == mn.NOTE_GENE
    assert [m["id"] for m in c["members"]] == [COX1]                     # OTHER has an activity only
    m = c["members"][0]
    assert m["n_drugs"] == 5 and len(m["drugs"]) == mn.MAX_DRUGS
    assert [d["label"] for d in m["drugs"]] == ["ASPIRIN", "DRUG-B", "DRUG-C"]     # max phase 4, 3, 2
    asp = m["drugs"][0]
    assert asp["action_type"] == "INHIBITOR" and asp["max_phase"] == 4.0
    assert [(x["id"], x["max_phase"]) for x in asp["indications"]] == [(DIS2, 4.0), (DIS1, 2.0)]
    assert c["lead"][0]["edge"] == f"e{4:016x}"                          # PTGS2 gene encodes the protein


def test_disease_card_lists_drugs_targets_and_families(idx):
    c = idx.card(DIS2)["mechanism_neighbours"]
    assert c["kind"] == "disease" and c["n_drugs"] == 1 and c["note"] == mn.NOTE_DISEASE
    d = c["drugs"][0]
    assert d["label"] == "ASPIRIN" and d["max_phase"] == 4.0 and d["edge"] == f"e{221:016x}"
    assert [(t["id"], t["family"]["name"]) for t in d["targets"]] == [(COX1, "Cyclooxygenase")]


def test_every_hop_resolves_in_explain(idx):
    ids = mn.edge_ids({"g": idx.card(GENE)["mechanism_neighbours"], "d": idx.card(DIS2)["mechanism_neighbours"]})
    assert len(ids) >= 6
    for eid in ids:
        assert idx.explain_edge(eid) is not None, eid


def test_empty_state_and_other_categories(idx):
    sod1 = idx.card("NCBIGene:6647")["mechanism_neighbours"]
    assert sod1["empty"] == mn.EMPTY_GENE and sod1["members"] == []
    assert idx.card("GO:0006954")["mechanism_neighbours"] is None
    assert mn.empty_context("biolink:Disease")["empty"] == mn.EMPTY_DISEASE


def test_member_cap():
    prots = [f"UniProtKB:P{i:05d}" for i in range(13)]
    dto = pd.DataFrame({"protein": prots, "family": "DTO:F", "family_name": "Big family"})
    mech = pd.DataFrame([dict(id=f"m{i}", subject=f"CHEMBL.COMPOUND:C{i}", object=p, action_type="", max_phase="")
                         for i, p in enumerate(prots[1:])])
    parts = mn.concat_parts([dict(gp=pd.DataFrame(columns=["id", "subject", "object"]), mech=mech,
                                  ind=pd.DataFrame(columns=["id", "subject", "object", "phase"]),
                                  mem=pd.DataFrame(columns=["id", "subject", "object"]))])
    c = mn.build_contexts(parts, {}, dto)[prots[0]]
    assert c["n_members"] == 12 and len(c["members"]) == mn.MAX_MEMBERS


def test_old_index_without_the_table(idx):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    old = CardIndex.__new__(CardIndex)
    old.con = con
    assert old.mechanism(GENE, "biolink:Gene") is None


def test_pack_stores_member_rows_once_and_unpacks_to_the_same_payload():
    prots = [f"UniProtKB:P{i}" for i in range(4)]
    dto = pd.DataFrame({"protein": prots, "family": "DTO:F", "family_name": "F"})
    mech = pd.DataFrame([dict(id=f"m{i}", subject=f"CHEMBL.COMPOUND:C{i}", object=p, action_type="", max_phase="1")
                         for i, p in enumerate(prots)])
    empty = lambda cols: pd.DataFrame(columns=cols)
    ctx = mn.build_contexts(dict(gp=empty(["id", "subject", "object"]), mech=mech, ind=empty(["id", "subject", "object", "phase"]),
                                 mem=empty(["id", "subject", "object"])), {}, dto)
    nodes, parts = mn.pack(ctx)
    assert len(nodes) == 4 and len(parts) == 4                            # 4 cards x 3 members -> 4 stored rows
    for nid, c in ctx.items():
        assert mn.unpack(nodes[nid], parts.__getitem__) == c
