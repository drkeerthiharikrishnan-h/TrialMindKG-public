"""CC-16: Open Targets mechanism-of-action records that list several drugs. The Parquet list column arrived as a numpy array,
str() gave "['CHEMBL1026' 'CHEMBL588']" and ast.literal_eval joined the two literals into 'CHEMBL1026CHEMBL588'. Now a list
cell is parsed without literal_eval (and arrays become JSON on read), one edge is made per drug, and a validator rejects any
id that joins several accessions. Fixture tables only."""
import ast
import gzip
import json

import numpy as np
import pandas as pd
import pytest

from kgbuilder import engine
from kgbuilder.biolink import BioLink
import kg_schema

BL = BioLink()
CARD = {"name": "otmoa", "version": "t", "aggregator": "infores:opentargets", "layer": "L1", "tables_dir": "x",
        "nodes": [],
        "edges": [{"name": "ot_drug_mechanism", "table": "mechanismOfAction",
                   "subject": {"column": "chemblIds", "prefix": "CHEMBL.COMPOUND", "category": "biolink:SmallMolecule"},
                   "predicate": "biolink:directly_physically_interacts_with",
                   "object": {"columns": [{"column": "targets", "prefix": "ENSEMBL"}], "category": "biolink:Gene"},
                   "explode_json": ["chemblIds", "targets"], "record_columns": ["chemblIds", "targets"],
                   "source": "Open Targets 26.09 drug mechanism", "primary_knowledge_source": "infores:opentargets",
                   "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
                   "properties": {"action_type": "actionType", "mechanism": "mechanismOfAction"}}]}
NUMPY_CELL = str(np.array(["CHEMBL1026", "CHEMBL588"], dtype=object))


def table(chembl, targets, tmp_path, how="parquet"):
    df = pd.DataFrame({"chemblIds": chembl, "targets": targets, "actionType": ["INHIBITOR"] * len(chembl),
                       "mechanismOfAction": ["mech"] * len(chembl)})
    if how == "parquet":
        df.to_parquet(tmp_path / "mechanismOfAction.parquet")
    else:
        with gzip.open(tmp_path / "mechanismOfAction.tsv.gz", "wt") as f:
            df.to_csv(f, sep="\t", index=False)
    return tmp_path


def build(tmp_path):
    return engine.build(CARD, tables_dir=tmp_path, bl=BL)


def test_the_numpy_repr_is_what_literal_eval_glued():
    assert NUMPY_CELL == "['CHEMBL1026' 'CHEMBL588']"
    assert ast.literal_eval(NUMPY_CELL) == ["CHEMBL1026CHEMBL588"]      # adjacent literals concatenate: ONE glued item                    # the bug, reproduced
    assert engine.parse_list_cell(NUMPY_CELL) == ["CHEMBL1026", "CHEMBL588"]         # the fix


@pytest.mark.parametrize("cell,want", [
    ('["CHEMBL1","CHEMBL2"]', ["CHEMBL1", "CHEMBL2"]),                               # JSON
    ("['CHEMBL1', 'CHEMBL2']", ["CHEMBL1", "CHEMBL2"]),                              # Python list repr
    ("['CHEMBL1' 'CHEMBL2' 'CHEMBL3']", ["CHEMBL1", "CHEMBL2", "CHEMBL3"]),          # numpy repr, three items
    ("['CHEMBL1']", ["CHEMBL1"]), ('["CHEMBL1"]', ["CHEMBL1"]), ("[]", []),
    ('["a b","c"]', ["a b", "c"]),                                                   # a space inside an item stays
    ("[1 2 3]", ["1", "2", "3"]),                                                    # unquoted numpy ints
])
def test_parse_list_cell_forms(cell, want):
    assert engine.parse_list_cell(cell) == want


def test_stringify_lists_makes_arrays_json_and_leaves_the_rest():
    df = pd.DataFrame({"a": [np.array(["x", "y"], dtype=object), np.array(["z"], dtype=object)], "b": ["p", "q"], "c": [["l1", "l2"], []],
                       "n": [1, 2]})
    out = engine.stringify_lists(df)
    assert list(out["a"]) == ['["x", "y"]', '["z"]'] and list(out["c"]) == ['["l1", "l2"]', "[]"]
    assert list(out["b"]) == ["p", "q"] and list(out["n"]) == [1, 2]
    assert isinstance(df["a"][0], np.ndarray)                                          # the input is not changed


def test_two_drug_record_gives_two_edges_two_nodes_and_no_glued_id(tmp_path):
    table([np.array(["CHEMBL1026", "CHEMBL588"], dtype=object), np.array(["CHEMBL25"], dtype=object)],
          [np.array(["ENSG00000184845"], dtype=object), np.array(["ENSG00000012048"], dtype=object)], tmp_path)
    b = build(tmp_path)
    assert b.problems == []
    subj = sorted(b.edges["subject"])
    assert subj == ["CHEMBL.COMPOUND:CHEMBL1026", "CHEMBL.COMPOUND:CHEMBL25", "CHEMBL.COMPOUND:CHEMBL588"]
    assert not any(kg_schema.glued_id(i) for i in list(b.nodes["id"]) + subj)
    assert sorted(i for i in b.nodes["id"] if i.startswith("CHEMBL.COMPOUND")) == subj          # a node per real compound id
    e = b.edges.set_index("subject")
    assert e.loc["CHEMBL.COMPOUND:CHEMBL1026", "object"] == e.loc["CHEMBL.COMPOUND:CHEMBL588", "object"] == "ENSEMBL:ENSG00000184845"
    assert e.loc["CHEMBL.COMPOUND:CHEMBL1026", "action_type"] == e.loc["CHEMBL.COMPOUND:CHEMBL588", "action_type"] == "INHIBITOR"
    assert len(set(b.edges["id"])) == 3                                                # one edge id per drug
    again = build(tmp_path)
    assert again.edges["id"].tolist() == b.edges["id"].tolist()                        # ids from the single drug id, stable


def test_several_targets_are_not_glued_either(tmp_path):
    table([np.array(["CHEMBL1"], dtype=object)], [np.array(["ENSG00000000001", "ENSG00000000002"], dtype=object)], tmp_path)
    b = build(tmp_path)
    assert sorted(b.edges["object"]) == ["ENSEMBL:ENSG00000000001", "ENSEMBL:ENSG00000000002"] and b.problems == []


def test_list_json_text_and_single_string_inputs_give_the_same_edges(tmp_path):
    arr, js, one = tmp_path / "a", tmp_path / "j", tmp_path / "o"
    for d in (arr, js, one):
        d.mkdir()
    table([np.array(["CHEMBL1", "CHEMBL2"], dtype=object)], [np.array(["ENSG00000000001"], dtype=object)], arr)
    table(['["CHEMBL1","CHEMBL2"]'], ["ENSG00000000001"], js, how="tsv")
    table(["CHEMBL1"], ["ENSG00000000001"], one, how="tsv")
    a, j, o = build(arr), build(js), build(one)
    assert a.edges["id"].tolist() == j.edges["id"].tolist() and len(a.edges) == 2
    assert o.edges["subject"].tolist() == ["CHEMBL.COMPOUND:CHEMBL1"] and len(o.edges) == 1
    assert set(o.edges["id"]) <= set(a.edges["id"])                                   # the single-drug edge is one of the two


def test_the_build_reports_how_many_records_listed_several_values(tmp_path):
    table([np.array(["CHEMBL1", "CHEMBL2"], dtype=object), np.array(["CHEMBL3"], dtype=object),
           np.array(["CHEMBL4", "CHEMBL5", "CHEMBL6"], dtype=object)],
          [np.array(["ENSG00000000001"], dtype=object)] * 3, tmp_path)
    b = build(tmp_path)
    assert b.multi_value == [("ot_drug_mechanism", "chemblIds", 2, 5)]               # 2 records with 2 + 3 drugs; targets: none
    md = engine.summary_markdown(b)
    assert "## Source records that list several values" in md and "| ot_drug_mechanism | chemblIds | 2 | 5 |" in md
    single = tmp_path / "s"
    single.mkdir()
    table(["CHEMBL1"], ["ENSG00000000001"], single, how="tsv")
    assert "list several values" not in engine.summary_markdown(build(single))


@pytest.mark.parametrize("nid,glued", [
    ("CHEMBL.COMPOUND:CHEMBL1026CHEMBL588", True), ("ENSEMBL:ENSG00000184845ENSG00000012048", True),
("CHEMBL.COMPOUND:CHEMBL1026", False), ("ENSEMBL:ENSG00000184845", False),
    ("UniProtKB:P35354", False), ("NCBIGene:5743", False), ("GO:0006954", False), ("MONDO:0004975", False),
    ("NCT01739348", False), ("ARM:NCT01739348:A1", False), ("HP:0001250", False)])
def test_glued_id_rule(nid, glued):
    assert kg_schema.glued_id(nid) is glued


def test_the_builder_flags_a_glued_id_in_its_input(tmp_path):
    table(["CHEMBL1026CHEMBL588"], ["ENSG00000000001"], tmp_path, how="tsv")            # a table that is already glued
    b = build(tmp_path)
    assert any("join several accessions" in p and "CHEMBL.COMPOUND:CHEMBL1026CHEMBL588" in p for p in b.problems)
    assert "join several accessions" in engine.summary_markdown(b)


def test_kg_schema_validate_flags_a_glued_node():
    kg = kg_schema.KG()
    kg.add_node("CHEMBL:CHEMBL1026CHEMBL588", "biolink:SmallMolecule")
    problems = kg_schema.validate(kg)
    assert any("joined without a separator" in p for p in problems)


def test_the_real_card_explodes_both_list_columns():
    card = engine.load_card(engine.ROOT / "sources" / "opentargets_layer.yaml")
    spec = [e for e in card["edges"] if e["name"] == "ot_drug_mechanism"][0]
    assert spec["explode_json"] == ["chemblIds", "targets"] and engine.check_card(card, BL) == []
