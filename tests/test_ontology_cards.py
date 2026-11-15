"""Tests for pipelines/ontology_prepare.py and the three L2 hierarchy cards (hpo, go, mondo) on a tiny OBO Graphs
fixture; no network. The BioLink checks need data/external/biolink/biolink-model.yaml (skipped without it)."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import ontology_prepare as op  # noqa: E402
from kgbuilder import engine  # noqa: E402
from kgbuilder.biolink import DEFAULT as BIOLINK_YAML, BioLink  # noqa: E402

OBO = "http://purl.obolibrary.org/obo/"
NS = "http://www.geneontology.org/formats/oboInOwl#hasOBONamespace"


def go_doc():
    """A go-basic-shaped document: 3 BP terms, 1 MF, 1 CC, one deprecated term, one imported (non-GO) term."""
    def node(id_, lbl, ns, deprecated=False):
        meta = {"basicPropertyValues": [{"pred": NS, "val": ns}]}
        if deprecated:
            meta["deprecated"] = True
        return {"id": OBO + id_, "lbl": lbl, "type": "CLASS", "meta": meta}
    nodes = [node("GO_0008150", "biological_process", "biological_process"),
             node("GO_0006915", "apoptotic process", "biological_process"),
             node("GO_0012501", "programmed cell death", "biological_process"),
             node("GO_0003674", "molecular_function", "molecular_function"),
             node("GO_0005575", "cellular_component", "cellular_component"),
             node("GO_0000001", "old term", "biological_process", deprecated=True),
             {"id": OBO + "CHEBI_24431", "lbl": "chemical entity", "type": "CLASS"}]
    edges = [{"sub": OBO + "GO_0006915", "pred": "is_a", "obj": OBO + "GO_0012501"},
             {"sub": OBO + "GO_0012501", "pred": "is_a", "obj": OBO + "GO_0008150"},
             {"sub": OBO + "GO_0006915", "pred": OBO + "BFO_0000050", "obj": OBO + "GO_0008150"},
             {"sub": OBO + "GO_0000001", "pred": "is_a", "obj": OBO + "GO_0008150"},        # deprecated: dropped
             {"sub": OBO + "GO_0006915", "pred": OBO + "RO_0002211", "obj": OBO + "GO_0012501"},  # regulates: dropped
             {"sub": OBO + "GO_0003674", "pred": OBO + "BFO_0000050", "obj": OBO + "GO_0008150"}]  # cross-aspect
    return {"graphs": [{"id": OBO + "go.owl", "meta": {"version": OBO + "go/releases/2026-09-01/go-basic.json"},
                        "nodes": nodes, "edges": edges}]}


def simple_doc(prefix, version):
    """An hp.json / mondo.json-shaped document with a 3-term is_a chain and one deprecated term."""
    ids = [f"{prefix}_0000001", f"{prefix}_0000118", f"{prefix}_0001250"]
    nodes = [{"id": OBO + i, "lbl": f"term {i}", "type": "CLASS"} for i in ids]
    nodes.append({"id": OBO + f"{prefix}_0009999", "lbl": "gone", "type": "CLASS", "meta": {"deprecated": True}})
    nodes.append({"id": "http://identifiers.org/hgnc/10001", "lbl": "RGS5", "type": "CLASS"})
    edges = [{"sub": OBO + ids[2], "pred": "is_a", "obj": OBO + ids[1]},
             {"sub": OBO + ids[1], "pred": "is_a", "obj": OBO + ids[0]},
             {"sub": OBO + f"{prefix}_0009999", "pred": "is_a", "obj": OBO + ids[0]}]
    return {"graphs": [{"id": OBO + f"{prefix.lower()}.owl", "meta": {"version": version}, "nodes": nodes,
                        "edges": edges}]}


def test_curie_and_parse_go():
    assert op.curie(OBO + "GO_0008150", "GO") == "GO:0008150" and op.curie(OBO + "CHEBI_1", "GO") is None
    terms, rels, version = op.parse(go_doc()["graphs"][0], "GO")
    assert version.endswith("2026-09-01/go-basic.json")
    assert set(terms["id"]) == {"GO:0008150", "GO:0006915", "GO:0012501", "GO:0003674", "GO:0005575"}
    assert dict(zip(terms["id"], terms["namespace"]))["GO:0003674"] == "molecular_function"
    assert len(rels) == 4 and set(rels["relation"]) == {"is_a", "part_of"}
    assert "GO:0000001" not in set(rels["subject"])                       # deprecated term's edge dropped
    cross = rels[(rels["subject"] == "GO:0003674")]
    assert len(cross) == 1 and cross.iloc[0]["object_namespace"] == "biological_process"


def test_convert_writes_tables_and_report(tmp_path):
    src = tmp_path / "go-basic.json"
    src.write_text(json.dumps(go_doc()))
    rep = op.convert(src, "GO", tmp_path / "go")
    assert rep["n_terms"] == 5 and rep["n_relations"] == 4 and rep["by_relation"] == {"is_a": 2, "part_of": 2}
    for t in ("terms", "relations", "terms_biological_process", "terms_molecular_function", "terms_cellular_component"):
        assert (tmp_path / "go" / f"{t}.tsv.gz").exists()
    bp = pd.read_csv(tmp_path / "go" / "terms_biological_process.tsv.gz", sep="\t")
    assert len(bp) == 3
    assert (tmp_path / "go" / "version.txt").read_text().strip() == rep["version"]
    md = op.report_markdown("go", rep)
    assert "| is_a | 2 |" in md and "| biological_process | 3 |" in md
    # HP / MONDO: one namespace -> no per-namespace tables
    src2 = tmp_path / "hp.json"
    src2.write_text(json.dumps(simple_doc("HP", OBO + "hp/releases/2026-10-01/hp.json")))
    rep2 = op.convert(src2, "HP", tmp_path / "hpo")
    assert rep2["n_terms"] == 3 and rep2["n_relations"] == 2
    assert not (tmp_path / "hpo" / "terms_hp.tsv.gz").exists()


@pytest.mark.skipif(not BIOLINK_YAML.exists(), reason="biolink-model.yaml not downloaded")
def test_hierarchy_cards_are_valid_biolink():
    bl = BioLink()
    for stem in ("hpo_hierarchy", "go_hierarchy", "mondo_hierarchy"):
        card = engine.load_card(Path("sources") / f"{stem}.yaml")
        assert engine.check_card(card, bl) == [], stem
        assert card["layer"] == "L2" and card["prune"]["never_prune_predicates"]


@pytest.mark.skipif(not BIOLINK_YAML.exists(), reason="biolink-model.yaml not downloaded")
def test_build_hierarchy_cards_on_fixture(tmp_path):
    bl = BioLink()
    (tmp_path / "go-basic.json").write_text(json.dumps(go_doc()))
    (tmp_path / "hp.json").write_text(json.dumps(simple_doc("HP", "v1")))
    (tmp_path / "mondo.json").write_text(json.dumps(simple_doc("MONDO", "v2")))
    op.convert(tmp_path / "go-basic.json", "GO", tmp_path / "go")
    op.convert(tmp_path / "hp.json", "HP", tmp_path / "hpo")
    op.convert(tmp_path / "mondo.json", "MONDO", tmp_path / "mondo")

    b = engine.build(engine.load_card("sources/go_hierarchy.yaml"), tables_dir=tmp_path / "go", bl=bl)
    assert b.problems == []
    cats = dict(zip(b.nodes["id"], b.nodes["category"]))
    assert cats["GO:0006915"] == "biolink:BiologicalProcess" and cats["GO:0003674"] == "biolink:MolecularActivity"
    assert cats["GO:0005575"] == "biolink:CellularComponent" and "GO:0000001" not in cats
    preds = b.edges.groupby("predicate").size().to_dict()
    assert preds == {"biolink:part_of": 1, "biolink:subclass_of": 2}      # the cross-aspect part_of is not matched
    e = b.edges.set_index(["subject", "object"])
    assert e.loc[("GO:0006915", "GO:0012501"), "predicate"] == "biolink:subclass_of"
    assert (b.edges["layer"] == "L2").all() and (b.edges["knowledge_level"] == "knowledge_assertion").all()
    assert set(b.edges["original_predicate"]) == {"rdfs:subClassOf", "BFO:0000050"}

    for stem, folder, cat, child in (("hpo_hierarchy", "hpo", "biolink:PhenotypicFeature", "HP:0001250"),
                                     ("mondo_hierarchy", "mondo", "biolink:Disease", "MONDO:0001250")):
        b = engine.build(engine.load_card(f"sources/{stem}.yaml"), tables_dir=tmp_path / folder, bl=bl)
        assert b.problems == [], stem
        assert set(b.nodes["category"]) == {cat} and len(b.nodes) == 3 and len(b.edges) == 2
        assert child in set(b.edges["subject"]) and (b.edges["predicate"] == "biolink:subclass_of").all()


def test_make_graphs_layer_names_match_the_cards():
    import make_graphs as mg
    for stem in ("hpo_hierarchy", "go_hierarchy", "mondo_hierarchy"):
        assert stem in mg.L1_L2
        card = engine.load_card(Path("sources") / f"{stem}.yaml")
        assert card["name"] == stem                       # make_graphs reads data/kg/<card name>/
