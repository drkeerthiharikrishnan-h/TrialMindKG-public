"""Tests for pipelines/make_graphs.py and make_g0.py on a tiny fake repo (tmp_path); no network, no real data."""
import gzip
import pandas as pd
import pytest

import make_graphs as mg


def write_layer(root, card_stem, name, nodes, edges, verdicts=None):
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / f"{card_stem}.yaml").write_text(f"name: {name}\n")
    d = root / "data" / "kg" / name
    d.mkdir(parents=True)
    for fn, df in (("nodes", nodes), ("edges", edges), ("edge_verdicts", verdicts)):
        if df is not None:
            with gzip.open(d / f"{fn}.tsv.gz", "wt") as f:
                df.to_csv(f, sep="\t", index=False)


PHAROS_NODES = pd.DataFrame({"id": ["NCBIGene:1", "NCBIGene:2", "MONDO:1", "UniProtKB:P1"],
                             "category": ["biolink:Gene", "biolink:Gene", "biolink:Disease", "biolink:Protein"]})
PHAROS_EDGES = pd.DataFrame({
    "id": ["e1", "e2", "e3"],
    "subject": ["NCBIGene:1", "NCBIGene:2", "NCBIGene:1"],
    "predicate": ["biolink:gene_associated_with_condition", "biolink:gene_associated_with_condition",
                  "biolink:has_gene_product"],
    "object": ["MONDO:1", "MONDO:1", "UniProtKB:P1"]})
VERDICTS = pd.DataFrame({"id": ["e1", "e2", "e3"], "verdict": ["keep", "prune", "keep"]})


@pytest.fixture
def repo(tmp_path):
    write_layer(tmp_path, "pharos", "pharos", PHAROS_NODES, PHAROS_EDGES, VERDICTS)
    return tmp_path


def test_g0_is_pharos_unpruned_and_g1_drops_prune_verdicts(repo):
    n0, e0, r0 = mg.assemble("G0", repo)
    assert list(e0["id"]) == ["e1", "e2", "e3"] and r0["missing"] == [] and r0["pruned"] == 0
    assert set(e0["subject_category"]) == {"biolink:Gene"}
    _, e1, r1 = mg.assemble("G1", repo)
    assert list(e1["id"]) == ["e1", "e3"] and r1["pruned"] == 1


def test_graph_with_missing_layers_is_not_assembled_unless_allowed(repo):
    nodes, edges, rep = mg.assemble("G2", repo)
    assert nodes is None and "opentargets_layer" in rep["missing"]
    nodes, edges, rep = mg.assemble("G2", repo, allow_missing=True)
    assert len(edges) == 3 and rep["used"] == ["pharos"]
    assert "Layers missing (not built yet): dto, opentargets_layer" in mg.summary_markdown(rep)


def test_combine_dedups_nodes_and_edges_and_drops_edges_without_nodes():
    a = (PHAROS_NODES, PHAROS_EDGES.assign(layer="pharos"))
    b = (pd.DataFrame({"id": ["MONDO:1", "HP:1"], "category": ["biolink:PhenotypicFeature", "biolink:PhenotypicFeature"]}),
         pd.DataFrame({"id": ["e1", "x1", "x2"], "subject": ["NCBIGene:1", "MONDO:1", "NCBIGene:9"],
                       "predicate": ["p", "biolink:has_phenotype", "p"], "object": ["MONDO:1", "HP:1", "HP:1"],
                       "layer": "hpo"}))
    nodes, edges = mg.combine([a, b])
    assert nodes.set_index("id").loc["MONDO:1", "category"] == "biolink:Disease"   # first layer's class wins
    assert sorted(edges["id"]) == ["e1", "e2", "e3", "x1"]                       # e1 once; x2 has no node row
    assert edges.set_index("id").loc["e1", "layer"] == "pharos"


def test_layer_without_verdicts_is_kept_whole_and_reported(repo):
    write_layer(repo, "hpo_hierarchy", "hpo_hierarchy",
                pd.DataFrame({"id": ["HP:1", "HP:2"], "category": ["biolink:PhenotypicFeature"] * 2}),
                pd.DataFrame({"id": ["h1"], "subject": ["HP:1"], "predicate": ["biolink:subclass_of"], "object": ["HP:2"]}))
    mg_recipes = dict(mg.RECIPES)
    try:
        mg.RECIPES["GX"] = {"layers": ["pharos", "hpo_hierarchy"], "prune": True}
        _, edges, rep = mg.assemble("GX", repo)
    finally:
        mg.RECIPES.clear(); mg.RECIPES.update(mg_recipes)
    assert rep["not_pruned"] == ["hpo_hierarchy"] and "h1" in set(edges["id"]) and "e2" not in set(edges["id"])


def test_recipes_follow_the_plan():
    assert mg.RECIPES["G0"]["layers"] == ["pharos"] and not mg.RECIPES["G0"]["prune"]
    assert mg.RECIPES["G3"]["layers"] == mg.RECIPES["G2"]["layers"] and mg.RECIPES["G3"]["prune"]
    assert mg.RECIPES["G4"]["layers"][-1] == mg.TRIAL_LAYER


def test_make_g0_writes_heterodata_that_loads_in_pyg(repo):
    pytest.importorskip("torch_geometric")
    from kgbuilder.pyg_export import load_pyg
    rep = mg.make("G0", repo)
    assert rep["written"]
    hd = load_pyg(repo / "outputs" / "g0" / "heterodata.pt")
    et = ("Gene", "gene_associated_with_condition", "Disease")
    assert hd[et].edge_index.size(1) == 2 and hd[et].edge_layer == ["pharos", "pharos"]
    assert hd[et].edge_knowledge_level == ["not_provided", "not_provided"]   # no knowledge_level column in fixture
    assert hd["Gene"].node_id == ["NCBIGene:1", "NCBIGene:2"]
    assert "Total: 4 nodes, 3 edges" in (repo / "outputs" / "g0" / "summary.md").read_text()


def test_node_features_depend_only_on_node_id_and_seed():
    torch = pytest.importorskip("torch")
    from linkpred.run import node_features
    a = node_features(["NCBIGene:1", "NCBIGene:2"], dim=8, seed=0)
    b = node_features(["NCBIGene:9", "NCBIGene:2", "NCBIGene:1"], dim=8, seed=0)   # other graph, other order
    assert torch.equal(a[0], b[2]) and torch.equal(a[1], b[1])
    assert not torch.equal(a[0], node_features(["NCBIGene:1"], dim=8, seed=1)[0])


def test_find_task_edge_type_matches_short_names():
    pytest.importorskip("torch_geometric")
    from torch_geometric.data import HeteroData
    import torch
    from linkpred.run import find_task_edge_type
    hd = HeteroData()
    hd["Gene", "gene_associated_with_condition", "Disease"].edge_index = torch.zeros((2, 1), dtype=torch.long)
    assert find_task_edge_type(hd, "B") == ("Gene", "gene_associated_with_condition", "Disease")
