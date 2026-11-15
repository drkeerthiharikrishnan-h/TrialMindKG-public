"""Wave A / B of docs/ENRICHMENT_DIAGNOSIS_PLAN.md: baselines, join audit and the wiring options.
The numpy parts run everywhere; the model parts need torch + torch_geometric (skipped in the cloud)."""
import sys
sys.path.insert(0, "pipelines")

import numpy as np
import pytest

from linkpred.baselines import degree_aurocs, build_adjacency, path_scores, heuristic_aurocs, block_offsets
from linkpred.join_audit import audit, markdown


def test_degree_baseline_scores_popularity_only():
    # disease 0 has 4 training genes, disease 1 has 2; positives point at 0, negatives at 1 -> perfect by dst degree
    train_src, train_dst = [0, 1, 2, 3, 4, 5], [0, 0, 0, 0, 1, 1]
    test_ei = np.array([[4, 5], [0, 0]])
    neg_ei = np.array([[4, 5], [1, 1]])
    m = degree_aurocs(train_src, train_dst, n_src=6, n_dst=2, test_ei=test_ei, neg_ei=neg_ei)
    assert m["auroc_degree_dst"] == 1.0
    assert m["auroc_degree_src"] == 0.5                       # the same genes in positives and negatives: no signal
    assert m["auroc_degree_prod"] == 1.0


def test_adjacency_is_symmetric_binary_over_all_node_types():
    edges = {("Gene", "a", "Disease"): np.array([[0, 0, 1], [0, 1, 0]]),
             ("Gene", "p", "Protein"): np.array([[0, 1], [0, 1]])}
    A, off = build_adjacency(edges, {"Gene": 2, "Disease": 2, "Protein": 2})
    assert off == {"Gene": 0, "Disease": 2, "Protein": 4} and block_offsets({"Gene": 2, "Disease": 2})[1] == 4
    assert A.shape == (6, 6) and (A != A.T).nnz == 0 and set(A.data) == {1.0}
    assert A[0, 2] == 1 and A[2, 0] == 1 and A[1, 5] == 1 and A[0, 1] == 0


def test_path_scores_count_two_and_three_hop_paths():
    # gene0 - disease0 - gene1 - disease1; gene0 also shares protein0 with gene1
    edges = {("Gene", "a", "Disease"): np.array([[0, 1, 1], [0, 0, 1]]),
             ("Gene", "p", "Protein"): np.array([[0, 1], [0, 0]])}
    A, off = build_adjacency(edges, {"Gene": 2, "Disease": 2, "Protein": 1})
    g0, g1, d0, d1 = 0, 1, 2, 3
    sc = path_scores(A, np.array([g0, g0]), np.array([d1, d0]), batch=1)
    assert sc["cn"][0] == 0 and sc["path3"][0] == 2            # g0-d0-g1-d1 and g0-p0-g1-d1
    # (g0, d0) IS an edge: 3-hop WALKS include the back-tracking ones (g0-d0-g0-d0, g0-p0-g0-d0) plus g0-d0-g1-d0
    # and g0-p0-g1-d0 -> 4. Held-out pairs are never edges of the training graph, so for them walks = paths.
    assert sc["cn"][1] == 0 and sc["path3"][1] == 4
    sc2 = path_scores(A, np.array([g1]), np.array([d1]))        # g1-d1 is an edge; common neighbours: none
    assert sc2["cn"][0] == 0
    m = heuristic_aurocs(edges, {"Gene": 2, "Disease": 2, "Protein": 1}, "Gene", "Disease",
                         np.array([[0], [1]]), np.array([[1], [1]]))
    assert set(m) == {"auroc_cn", "auroc_aa", "auroc_path3"} and all(0.0 <= v <= 1.0 for v in m.values())


def test_adamic_adar_prefers_rare_shared_neighbours():
    # pair (g0, d0) shares hub protein p0 (degree 4); pair (g1, d1) shares rare protein p1 (degree 2)
    edges = {("Gene", "p", "Protein"): np.array([[0, 1, 2, 3], [0, 1, 0, 0]]),
             ("Disease", "p", "Protein"): np.array([[0, 1], [0, 1]])}
    A, off = build_adjacency(edges, {"Gene": 4, "Disease": 2, "Protein": 2})
    sc = path_scores(A, np.array([0, 1]), np.array([off["Disease"], off["Disease"] + 1]))
    assert sc["cn"][0] == sc["cn"][1] == 1 and sc["aa"][1] > sc["aa"][0]


def test_join_audit_finds_layers_that_do_not_touch_pharos():
    node_ids = {"Gene": ["NCBIGene:1", "NCBIGene:2"], "Disease": ["MONDO:1", "EFO:2", "MONDO:3"]}
    edges = {("Gene", "assoc", "Disease"): ([0, 1, 0], [0, 1, 1], ["pharos", "opentargets", "opentargets"]),
             ("Disease", "subclass_of", "Disease"): ([1], [2], ["mondo"])}
    rep = audit(node_ids, edges)
    rows = {(r["edge_type"], r["layer"]): r for r in rep["edges"]}
    assert rows[("Gene|assoc|Disease", "pharos")]["share_both_ends_on_pharos"] == 1.0
    ot = rows[("Gene|assoc|Disease", "opentargets")]
    assert ot["n_edges"] == 2 and ot["share_one_end_on_pharos"] == 0.5 and ot["share_both_ends_on_pharos"] == 0.0
    assert rows[("Disease|subclass_of|Disease", "mondo")]["share_one_end_on_pharos"] == 0.0
    nodes = {r["node_type"]: r for r in rep["nodes"]}
    assert nodes["Disease"]["n_touched_by_pharos"] == 1 and nodes["Disease"]["prefixes"] == {"MONDO": 2, "EFO": 1}
    assert nodes["Disease"]["n_isolated"] == 0 and nodes["Gene"]["n_with_any_edge"] == 2
    md = markdown(rep, "toy")
    assert "| Gene|assoc|Disease | opentargets | 2 | 50.0% | 0.0% |" in md and "MONDO 2, EFO 1" in md


# ---- model parts (torch) ---------------------------------------------------------------------------------------

def _toy():
    from test_linkpred import toy_graph
    return toy_graph()


def test_reverse_edge_types_and_flipped_indices():
    torch = pytest.importorskip("torch")
    from linkpred.models import reverse_edge_types, with_reverse
    ets = [("Gene", "a", "Disease"), ("Protein", "ppi", "Protein")]
    assert reverse_edge_types(ets) == [("Disease", "rev_a", "Gene"), ("Protein", "rev_ppi", "Protein")]
    d = with_reverse({ets[0]: torch.tensor([[0, 1], [5, 6]])})
    assert torch.equal(d[("Disease", "rev_a", "Gene")], torch.tensor([[5, 6], [0, 1]])) and len(d) == 2


def test_negative_pool_restricts_training_negatives():
    torch = pytest.importorskip("torch")
    from linkpred.models import negative_sample
    ei = torch.zeros(2, 50, dtype=torch.long)
    pool = torch.tensor([3, 7])
    assert set(negative_sample(ei, 100, pool=pool).tolist()) <= {3, 7}
    assert negative_sample(ei, 100).numel() == 50


def test_split_text_mining_moves_edges_and_keeps_attributes_aligned():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from linkpred.run import split_text_mining, find_task_edge_type, eligible_mask
    hd = _toy()
    et = find_task_edge_type(hd, "B")
    n = hd[et].edge_index.size(1)
    hd[et].edge_knowledge_level = ["text_co_occurrence" if i % 3 == 0 else "knowledge_assertion" for i in range(n)]
    hd[et].edge_id = [f"e{i}" for i in range(n)]
    before = hd[et].edge_index.clone()
    moved = split_text_mining(hd, et)
    tm = (et[0], et[1] + "_context", et[2])
    assert moved == len([i for i in range(n) if i % 3 == 0]) and hd[tm].edge_index.size(1) == moved
    assert hd[et].edge_index.size(1) == n - moved and set(hd[et].edge_knowledge_level) == {"knowledge_assertion"}
    assert hd[tm].edge_id[0] == "e0" and hd[et].edge_id[0] == "e1" and len(hd[tm].edge_layer) == moved
    assert torch.equal(hd[tm].edge_index[:, 0], before[:, 0]) and torch.equal(hd[et].edge_index[:, 0], before[:, 1])
    assert eligible_mask(hd, et).all()                                 # what is left is all eligible
    assert split_text_mining(hd, et) == 0                              # nothing left to move


def test_training_negative_pool_modes():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from linkpred.run import training_negative_pool, find_task_edge_type
    hd = _toy()
    et = find_task_edge_type(hd, "B")
    mask = torch.ones(hd[et].edge_index.size(1), dtype=torch.bool)
    mask[:150] = False
    assert training_negative_pool(hd, et, mask, [1, 2], "all") is None
    pool = training_negative_pool(hd, et, mask, [1, 2], "training")
    assert set(pool.tolist()) == set(hd[et].edge_index[1][mask].tolist())
    assert training_negative_pool(hd, et, mask, [2, 1], "reference").tolist() == [1, 2]
    with pytest.raises(ValueError):
        training_negative_pool(hd, et, mask, [], "elsewhere")


def test_run_experiment_with_every_wiring_option_and_baselines():
    pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from linkpred.run import run_experiment
    hd = _toy()
    n = hd[("Gene", "gene_associated_with_condition", "Disease")].edge_index.size(1)
    hd[("Gene", "gene_associated_with_condition", "Disease")].edge_knowledge_level = \
        ["text_co_occurrence" if i % 4 == 0 else "knowledge_assertion" for i in range(n)]
    out = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=2, hidden=16, out=8, data=hd, log=lambda m: None,
                         reverse_edges=True, learn_embeddings=True, num_bases=2, neg_pool="training",
                         positives="curated", baselines=True)[0]
    for k in ("auroc", "auroc_untrained", "auroc_degree_dst", "auroc_degree_src", "auroc_degree_prod", "auroc_cn",
              "auroc_aa", "auroc_path3"):
        assert 0.0 <= out[k] <= 1.0, k
    with pytest.raises(TypeError):
        run_experiment("toy", "B", "kfold", n_seeds=1, epochs=1, data=_toy(), log=lambda m: None, bogus=1)


def test_learned_embeddings_change_during_training():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from linkpred.run import init_features, find_task_edge_type
    from linkpred.models import LinkPredictor, RGCNEncoder, train_epoch
    hd = init_features(_toy(), dim=16)
    et = find_task_edge_type(hd, "B")
    enc = RGCNEncoder(hd.metadata(), hidden=16, out=8, reverse=True,
                      learn_nodes={nt: hd[nt].x for nt in hd.node_types})
    assert len(enc.edge_types) == 2 * len(hd.edge_types)
    model = LinkPredictor(enc, len(hd.edge_types), 8)
    start = enc.node_emb["Gene"].detach().clone()
    opt = torch.optim.Adam(model.parameters(), lr=0.05)
    mask = torch.ones(hd[et].edge_index.size(1), dtype=torch.bool)
    for _ in range(3):
        train_epoch(model, hd, opt, et, mask)
    assert not torch.equal(start, enc.node_emb["Gene"].detach())
