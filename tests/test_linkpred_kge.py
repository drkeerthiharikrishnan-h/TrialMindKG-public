import sys
sys.path.insert(0, "pipelines")

import pytest
torch = pytest.importorskip("torch")
pytest.importorskip("torch_geometric")

from test_linkpred import toy_graph   # noqa: E402  (tests/ is on sys.path under pytest's rootdir conftest)


def _community_graph(seed=0):
    """Genes 0-19 only link to diseases 0-14, genes 20-39 only to 15-29: a held-out pair is predictable from the
    other pairs of its community, a random negative mostly crosses communities."""
    from torch_geometric.data import HeteroData
    g = torch.Generator().manual_seed(seed)
    hd = HeteroData()
    for nt, n in (("Gene", 40), ("Disease", 30), ("Protein", 40)):
        hd[nt].num_nodes = n
        hd[nt].node_id = [f"{nt}:{i}" for i in range(n)]
    genes = torch.randint(0, 40, (400,), generator=g)
    dis = torch.where(genes < 20, torch.randint(0, 15, (400,), generator=g), torch.randint(15, 30, (400,), generator=g))
    et = ("Gene", "gene_associated_with_condition", "Disease")
    hd[et].edge_index = torch.stack([genes, dis])
    hd[et].edge_layer = ["pharos"] * 400
    hd["Gene", "has_gene_product", "Protein"].edge_index = torch.stack([torch.arange(40), torch.arange(40)])
    hd["Protein", "physically_interacts_with", "Protein"].edge_index = torch.randint(0, 40, (2, 200), generator=g)
    return hd


def test_build_triples_uses_one_global_index_space():
    from linkpred.kge import build_triples
    hd = toy_graph()
    ed = {et: hd[et].edge_index for et in hd.edge_types}
    head, rel, tail, offsets, rel_ids, n = build_triples(ed, hd)
    assert n == sum(int(hd[nt].num_nodes) for nt in hd.node_types)
    assert head.numel() == rel.numel() == tail.numel() == sum(ei.size(1) for ei in ed.values())
    gp = ("Gene", "has_gene_product", "Protein")
    sel = rel == rel_ids[gp]
    assert torch.equal(head[sel] - offsets["Gene"], hd[gp].edge_index[0])
    assert torch.equal(tail[sel] - offsets["Protein"], hd[gp].edge_index[1])
    assert int(head.max()) < n and int(tail.max()) < n


def test_patched_swaps_and_restores_the_model_step():
    from linkpred import run, kge
    original = run._train_and_eval
    with kge.patched(model_name="distmult", kge_epochs=1):
        assert run._train_and_eval is kge.kge_train_eval and kge._SETTINGS["model_name"] == "distmult"
    assert run._train_and_eval is original and kge._SETTINGS == kge.KGE_DEFAULTS
    with pytest.raises(TypeError):
        with kge.patched(bogus=1):
            pass
    with pytest.raises(ValueError):
        kge._kge_class("nope")


@pytest.mark.parametrize("name", ["transe", "distmult", "complex", "rotate"])
def test_every_kge_model_runs_through_the_frozen_pipeline(name):
    from linkpred import kge
    from linkpred.run import run_experiment
    with kge.patched(model_name=name, kge_epochs=2, batch_size=256):
        out = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=2, hidden=16, out=8, data=toy_graph(),
                             log=lambda m: None)
    f = out[0]["folds"][0]
    assert 0.0 <= out[0]["auroc"] <= 1.0 and f["kge_epochs_done"] == 2 and f["kge_dim"] == 16
    assert f["kge_n_triples"] > 0 and f["n_test"] > 0


def test_same_test_pairs_as_the_rgcn_run():
    """The KGE run must score exactly the pairs the R-GCN run scores (same digest, same pair and leak counts)."""
    from linkpred import kge
    from linkpred.run import run_experiment
    base = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=1, hidden=16, out=8, data=toy_graph(),
                          log=lambda m: None)
    with kge.patched(model_name="rotate", kge_epochs=1, batch_size=256):
        alt = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=1, hidden=16, out=8, data=toy_graph(),
                             log=lambda m: None)
    assert base[0]["test_set_digest"] == alt[0]["test_set_digest"]
    for a, b in zip(base[0]["folds"], alt[0]["folds"]):
        assert a["n_test"] == b["n_test"] and a["n_leak_edges_removed"] == b["n_leak_edges_removed"]


def test_kge_learns_community_structure():
    from linkpred import kge
    from linkpred.run import run_experiment
    with kge.patched(model_name="rotate", kge_epochs=60, batch_size=128, lr=0.05, margin=6.0):
        out = run_experiment("comm", "B", "kfold", n_seeds=1, epochs=1, hidden=32, out=8, data=_community_graph(),
                             log=lambda m: None)
    assert out[0]["auroc"] > 0.65


def test_main_refuses_an_unlabelled_tag():
    from linkpred import kge
    with pytest.raises(SystemExit):
        kge.main(["--kge", "rotate", "--graphs", "G0", "--tag", "frozen_x"])
