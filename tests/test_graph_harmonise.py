"""One class per id prefix across layers (make_graphs.harmonise_categories), EFO / Orphanet -> MONDO tables
(mondo_ref.extract) and the run.py options --min-type-edges / --positives assertions."""
import sys
sys.path.insert(0, "pipelines")

import pandas as pd
import pytest

import make_graphs as mg
import mondo_ref


def test_harmonise_gives_one_class_per_prefix_and_lets_the_go_layer_type_go_terms():
    nodes = pd.DataFrame({
        "id": ["MONDO:1", "HP:1", "HP:1", "GO:1", "GO:1", "EFO:1", "EFO:1", "CHEMBL.COMPOUND:1", "NCBIGene:1"],
        "category": ["biolink:Disease", "biolink:Disease", "biolink:PhenotypicFeature", "biolink:Disease",
                     "biolink:BiologicalProcess", "biolink:Disease", "biolink:DiseaseOrPhenotypicFeature",
                     "biolink:SmallMolecule", "biolink:Gene"],
        "layer": ["pharos", "opentargets", "hpo_hierarchy", "opentargets", "go_hierarchy", "opentargets", "pharos",
                  "chembl37", "pharos"]})
    out, n = mg.harmonise_categories(nodes)
    cls = dict(zip(out["id"] + "|" + out["layer"], out["category"]))
    assert cls["HP:1|opentargets"] == "biolink:PhenotypicFeature"          # OT's disease index listed an HP term
    assert cls["GO:1|opentargets"] == "biolink:BiologicalProcess"          # the GO layer owns GO ids
    assert cls["EFO:1|opentargets"] == "biolink:DiseaseOrPhenotypicFeature"
    assert cls["CHEMBL.COMPOUND:1|chembl37"] == "biolink:SmallMolecule" and cls["NCBIGene:1|pharos"] == "biolink:Gene"
    assert n == 3
    out2, n2 = mg.harmonise_categories(nodes.drop(columns="layer"))        # no layer column: GO keeps its class
    assert n2 == 2 and set(out2[out2["id"] == "GO:1"]["category"]) == {"biolink:Disease", "biolink:BiologicalProcess"}


def test_combine_merges_a_disease_typed_differently_by_two_layers_into_one_node_type():
    pharos = (pd.DataFrame({"id": ["NCBIGene:1", "MONDO:1"], "category": ["biolink:Gene", "biolink:Disease"],
                            "layer": "pharos"}),
              pd.DataFrame({"id": ["e1"], "subject": ["NCBIGene:1"], "predicate": ["p"], "object": ["MONDO:1"],
                            "layer": "pharos"}))
    ot = (pd.DataFrame({"id": ["HP:5", "MONDO:1"], "category": ["biolink:Disease", "biolink:Disease"],
                        "layer": "opentargets"}),
          pd.DataFrame({"id": ["o1"], "subject": ["MONDO:1"], "predicate": ["biolink:has_phenotype"],
                        "object": ["HP:5"], "layer": "opentargets"}))
    rep = {}
    nodes, edges = mg.combine([pharos, ot], rep)
    assert rep["retyped"] == 1 and "layer" not in nodes.columns
    assert edges.set_index("id").loc["o1", "object_category"] == "biolink:PhenotypicFeature"
    assert nodes.set_index("id").loc["HP:5", "category"] == "biolink:PhenotypicFeature"


OWL = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
         xmlns:owl="http://www.w3.org/2002/07/owl#" xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">
  <owl:Ontology rdf:about="http://purl.obolibrary.org/obo/mondo.owl">
    <owl:versionIRI rdf:resource="http://purl.obolibrary.org/obo/mondo/releases/2026-10-06/mondo.owl"/>
  </owl:Ontology>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/MONDO_0000001">
    <rdfs:label>disease one</rdfs:label>
    <oboInOwl:hasDbXref>EFO:0000001</oboInOwl:hasDbXref>
    <oboInOwl:hasDbXref>Orphanet:11</oboInOwl:hasDbXref>
    <oboInOwl:hasDbXref>DOID:1</oboInOwl:hasDbXref>
  </owl:Class>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/MONDO_0000002">
    <rdfs:label>disease two</rdfs:label>
    <oboInOwl:hasDbXref>EFO:0000001</oboInOwl:hasDbXref>
  </owl:Class>
</rdf:RDF>
"""


def test_mondo_ref_writes_efo_and_orphanet_tables_and_flags_ambiguous_xrefs(tmp_path):
    owl = tmp_path / "mondo.owl"
    owl.write_text(OWL)
    version = mondo_ref.extract(owl, tmp_path / "ref")
    assert version.endswith("2026-10-06/mondo.owl")
    efo = pd.read_csv(tmp_path / "ref" / "mondo_efo_exact.tsv", sep="\t", comment="#", dtype=str, keep_default_na=False)
    assert sorted(efo["mondo_id"]) == ["MONDO:0000001", "MONDO:0000002"] and set(efo["ambiguous"]) == {"Y"}
    orph = pd.read_csv(tmp_path / "ref" / "mondo_orphanet_exact.tsv", sep="\t", comment="#", dtype=str,
                       keep_default_na=False)
    assert orph.to_dict("records") == [{"orphanet_id": "Orphanet:11", "mondo_id": "MONDO:0000001", "ambiguous": ""}]
    doid = pd.read_csv(tmp_path / "ref" / "mondo_doid_exact.tsv", sep="\t", comment="#", dtype=str, keep_default_na=False)
    assert len(doid) == 1


def test_source_cards_name_the_mondo_lookup_tables_that_exist():
    from kgbuilder import engine
    for card_name in ("opentargets_layer.yaml", "chembl37.yaml", "pharos.yaml"):
        card = engine.load_card(engine.ROOT / "sources" / card_name)
        for name, lk in (card.get("lookups") or {}).items():
            if "ref_file" in lk:
                assert (engine.ROOT / lk["ref_file"]).exists(), (card_name, name)


def test_drop_small_edge_types_and_positive_exclusions():
    pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from test_linkpred import toy_graph
    from linkpred.run import drop_small_edge_types, positive_exclusions, find_task_edge_type
    hd = toy_graph()
    et = find_task_edge_type(hd, "B")
    hd[et].edge_knowledge_level = ["knowledge_assertion", "statistical_association", "text_co_occurrence"] * 100
    assert positive_exclusions(hd, et, "all") == ()
    assert positive_exclusions(hd, et, "curated") == ("text_co_occurrence",)
    assert positive_exclusions(hd, et, "assertions") == ("statistical_association", "text_co_occurrence")
    with pytest.raises(ValueError):
        positive_exclusions(hd, et, "weird")
    gone = drop_small_edge_types(hd, 250, keep=(et,))                 # has_gene_product has 40 edges, PPI 500
    assert gone == [("Gene", "has_gene_product", "Protein")] and et in hd.edge_types
    assert ("Protein", "physically_interacts_with", "Protein") in hd.edge_types
