"""Tests for pipelines/linkpred/ on tiny fixtures; torch required."""
import sys
sys.path.insert(0, "pipelines")

import numpy as np
import pytest
torch = pytest.importorskip("torch")

from linkpred.splits import kfold_masks, cold_start_masks, sparsity_masks, leakage_filter


def test_kfold_covers_all_edges():
    n = 100
    tested = set()
    for train, val, test in kfold_masks(n, k=5, seed=0):
        assert train.sum() + val.sum() + test.sum() == n
        assert not (train & test).any()
        assert not (val & test).any()
        tested.update(torch.where(test)[0].tolist())
    assert len(tested) == n


def test_cold_start_hides_all_edges_of_selected_nodes():
    ei = torch.tensor([[0, 0, 1, 1, 2, 2, 3, 3],
                        [0, 1, 0, 1, 0, 1, 0, 1]])
    train, val, test = cold_start_masks(ei, None, frac=0.5, seed=0, side="subject")
    hidden_src = set(ei[0, test].tolist()) | set(ei[0, val].tolist())
    for s in hidden_src:
        mask = ei[0] == s
        assert (test[mask] | val[mask]).all(), f"Not all edges of node {s} hidden"


def test_sparsity_nested():
    n = 200
    masks = list(sparsity_masks(n, remove_fracs=(0.1, 0.3, 0.5), seed=0))
    prev_test = None
    for frac, train, test in masks:
        assert abs(test.sum().item() - int(n * frac)) <= 1
        if prev_test is not None:
            assert (prev_test & test).sum() == prev_test.sum(), "smaller removal should be subset of larger"
        prev_test = test


def test_leakage_filter():
    ei = torch.tensor([[0, 1, 2], [10, 11, 12]])
    train = torch.tensor([True, True, True])
    enrichment = {(1, 11)}
    sub_map = {0: 0, 1: 1, 2: 2}
    obj_map = {10: 10, 11: 11, 12: 12}
    filtered = leakage_filter(train, ei, sub_map, obj_map, enrichment)
    assert filtered[0] and not filtered[1] and filtered[2]


def toy_graph(n_gene=40, n_dis=30, n_prot=40, seed=0):
    """Small Pharos-shaped HeteroData: Gene-Disease (task B), Gene->Protein, Protein-Protein."""
    from torch_geometric.data import HeteroData
    g = torch.Generator().manual_seed(seed)
    hd = HeteroData()
    for nt, n in (("Gene", n_gene), ("Disease", n_dis), ("Protein", n_prot)):
        hd[nt].num_nodes = n
        hd[nt].node_id = [f"{nt}:{i}" for i in range(n)]
    gd = torch.stack([torch.randint(0, n_gene, (300,), generator=g), torch.randint(0, n_dis, (300,), generator=g)])
    hd["Gene", "gene_associated_with_condition", "Disease"].edge_index = gd
    hd["Gene", "gene_associated_with_condition", "Disease"].edge_layer = ["pharos"] * 300
    hd["Gene", "has_gene_product", "Protein"].edge_index = torch.stack([torch.arange(n_gene), torch.arange(n_gene)])
    hd["Protein", "physically_interacts_with", "Protein"].edge_index = torch.randint(0, n_prot, (2, 500), generator=g)
    return hd


def test_run_experiment_end_to_end_on_toy_graph():
    pytest.importorskip("torch_geometric")
    from linkpred.run import run_experiment
    from linkpred.evaluate import summarise_run
    out = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=3, hidden=16, out=8, data=toy_graph(), log=lambda m: None)
    assert len(out) == 1 and 0.0 <= out[0]["auroc"] <= 1.0 and 0.0 <= out[0]["auprc"] <= 1.0
    assert "auroc" in summarise_run(out)
    cold = run_experiment("toy", "B", "cold_disease", n_seeds=1, epochs=2, hidden=16, out=8, data=toy_graph(),
                          log=lambda m: None)
    assert 0.0 <= cold[0]["auroc"] <= 1.0


def test_cap_edges_keeps_per_edge_attributes_aligned():
    pytest.importorskip("torch_geometric")
    from linkpred.run import cap_edges
    hd = toy_graph()
    et = ("Gene", "gene_associated_with_condition", "Disease")
    hd[et].edge_layer = [f"L{i}" for i in range(300)]
    before = {(int(s), int(o), l) for s, o, l in zip(*hd[et].edge_index, hd[et].edge_layer)}
    cap_edges(hd, 50)
    assert hd[et].edge_index.size(1) == 50 and len(hd[et].edge_layer) == 50
    after = {(int(s), int(o), l) for s, o, l in zip(*hd[et].edge_index, hd[et].edge_layer)}
    assert after <= before
    assert hd["Gene", "has_gene_product", "Protein"].edge_index.size(1) == 40   # under the cap: untouched


def test_cap_edges_keeps_the_same_edge_ids_in_a_pruned_copy():
    pytest.importorskip("torch_geometric")
    from linkpred.run import cap_edges
    et = ("Gene", "gene_associated_with_condition", "Disease")
    g0 = toy_graph()
    g0[et].edge_id = [f"e{i}" for i in range(300)]
    g1 = toy_graph()
    keep = torch.tensor([i for i in range(300) if i % 5 != 0])                 # "pruned": every fifth edge gone
    g1[et].edge_index = g0[et].edge_index[:, keep]
    g1[et].edge_id = [g0[et].edge_id[i] for i in keep.tolist()]
    g1[et].edge_layer = ["pharos"] * len(keep)                               # per-edge attributes match the 240 edges
    cap_edges(g0, 100)
    cap_edges(g1, 100)
    kept0, kept1 = set(g0[et].edge_id), set(g1[et].edge_id)
    assert len(kept0) == 100 and len(kept1) == 100 and len(g1[et].edge_layer) == 100
    assert {e for e in kept0 if int(e[1:]) % 5 != 0} <= kept1                 # shared edges kept in both


def test_auroc_and_average_precision_match_definitions():
    from linkpred.evaluate import auroc, average_precision
    y = np.array([1, 0, 1, 0, 1])
    s = np.array([0.9, 0.8, 0.7, 0.3, 0.3])
    # pairs (pos, neg): 0.9>0.8, 0.9>0.3, 0.7<0.8, 0.7>0.3, 0.3<0.8, 0.3=0.3 (half) -> 3.5 / 6
    assert abs(auroc(y, s) - 3.5 / 6) < 1e-9
    # ranked: 1,0,1,{0,1 tie}: precision at each recall step 1/3 -> 1.0, 2/3 -> 2/3, 3/3 -> 3/5
    assert abs(average_precision(y, s) - (1 / 3 * 1 + 1 / 3 * 2 / 3 + 1 / 3 * 3 / 5)) < 1e-9
    assert auroc([1, 0], [1.0, 0.0]) == 1.0


# --- shared test pairs across graphs (run.py) ---

def test_hash_unit_is_deterministic_and_balanced():
    from linkpred.splits import hash_unit, kfold_of_pairs, hidden_nodes
    keys = [f"NCBIGene:{i}\tMONDO:{i % 97}" for i in range(5000)]
    u1, u2 = hash_unit(keys, seed=0), hash_unit(keys, seed=0)
    assert (u1 == u2).all() and (0 <= u1).all() and (u1 < 1).all()
    assert not (hash_unit(keys, seed=1) == u1).all()
    folds = kfold_of_pairs(keys, k=5, seed=0)
    counts = np.bincount(folds, minlength=5)
    assert counts.min() > 800 and counts.max() < 1200          # roughly 1000 each
    assert hidden_nodes(["a", "b", "c"], frac=1.0) == {"a", "b", "c"}
    assert len(hidden_nodes(["a", "b", "c"], frac=1e-9)) == 1 and hidden_nodes([], frac=0.1) == set()   # never empty


def enriched_toy(hd, n_restated=40, n_new=60, seed=1):
    """G2-shaped copy of a toy G0: Open Targets edges in the task edge type (some restating Pharos pairs, some
    new) and a TIGA edge type that restates Pharos pairs. Returns the new HeteroData."""
    import copy
    g = torch.Generator().manual_seed(seed)
    et = ("Gene", "gene_associated_with_condition", "Disease")
    g2 = copy.deepcopy(hd)
    l0 = hd[et].edge_index
    restated = l0[:, :n_restated]
    new = torch.stack([torch.randint(0, hd["Gene"].num_nodes, (n_new,), generator=g),
                       torch.randint(0, hd["Disease"].num_nodes, (n_new,), generator=g)])
    g2[et].edge_index = torch.cat([l0, restated, new], dim=1)
    g2[et].edge_layer = list(hd[et].edge_layer) + ["opentargets_layer"] * (n_restated + n_new)
    g2["Gene", "genetically_associated_with", "Disease"].edge_index = l0[:, 100:150].clone()
    return g2


def test_same_test_pairs_in_baseline_and_enriched_graph_and_leaks_removed():
    pytest.importorskip("torch_geometric")
    from linkpred.run import reference_pairs, held_out_sets, split_graph, edge_pair_keys, find_task_edge_type
    g0 = toy_graph()
    g2 = enriched_toy(g0)
    ref = reference_pairs(g0, "B")
    assert ref == reference_pairs(g2, "B")                      # L0 pairs only: the OT edges are not scored
    et = find_task_edge_type(g0, "B")
    label, test_keys, hidden = next(held_out_sets(ref, "kfold", 0, g0["Gene"].node_id, g0["Disease"].node_id))
    results = {}
    for name, g in (("g0", g0), ("g2", g2)):
        src_index = {n: i for i, n in enumerate(g["Gene"].node_id)}
        dst_index = {n: i for i, n in enumerate(g["Disease"].node_id)}
        results[name] = split_graph(g, et, test_keys, hidden, src_index, dst_index, edge_pair_keys(g, et))
    t0, t2 = results["g0"][0], results["g2"][0]
    assert t0.shape == t2.shape and torch.equal(t0, t2) and t0.size(1) == len(test_keys)
    # G2: no training edge of the task type restates a test pair, whatever its layer
    train_keys = {k for k, m in zip(edge_pair_keys(g2, et), results["g2"][1].tolist()) if m}
    assert not (train_keys & test_keys)
    # G2: the TIGA edges restating test pairs are dropped from the graph the model sees
    tiga = ("Gene", "genetically_associated_with", "Disease")
    keep = results["g2"][2][tiga]
    tiga_keys = edge_pair_keys(g2, tiga)
    assert all((k in test_keys) == (not m) for k, m in zip(tiga_keys, keep.tolist()))
    assert results["g2"][3]["n_leak_edges_removed"] == sum(k in test_keys for k in tiga_keys) > 0
    # OT edges for pairs outside the test fold stay in training
    assert results["g2"][3]["n_train_task"] > results["g0"][3]["n_train_task"]


def test_pruned_graph_is_tested_on_the_reference_pairs():
    pytest.importorskip("torch_geometric")
    import copy
    from linkpred.run import reference_pairs, held_out_sets, split_graph, edge_pair_keys, find_task_edge_type
    g0 = toy_graph()
    g1 = copy.deepcopy(g0)
    et = find_task_edge_type(g0, "B")
    g1[et].edge_index = g0[et].edge_index[:, 50:]               # pruning removed 50 Pharos edges
    g1[et].edge_layer = g0[et].edge_layer[50:]
    ref = reference_pairs(g0, "B")
    assert len(reference_pairs(g1, "B")) < len(ref)
    label, test_keys, hidden = next(held_out_sets(ref, "kfold", 0, g0["Gene"].node_id, g0["Disease"].node_id))
    idx = {n: i for i, n in enumerate(g1["Gene"].node_id)}, {n: i for i, n in enumerate(g1["Disease"].node_id)}
    test_ei, train_mask, _, info = split_graph(g1, et, test_keys, hidden, idx[0], idx[1], edge_pair_keys(g1, et))
    assert info["n_test"] == len(test_keys) and info["n_test_missing_node"] == 0   # scored on every G0 pair


def test_cold_start_hides_every_edge_of_hidden_diseases():
    pytest.importorskip("torch_geometric")
    from linkpred.run import reference_pairs, held_out_sets, split_graph, edge_pair_keys, find_task_edge_type
    g2 = enriched_toy(toy_graph())
    et = find_task_edge_type(g2, "B")
    ref = reference_pairs(g2, "B")
    label, test_keys, hidden = next(held_out_sets(ref, "cold_disease", 0, g2["Gene"].node_id, g2["Disease"].node_id))
    side, hidden_ids = hidden
    assert side == 1 and hidden_ids and all(k.split("\t")[1] in hidden_ids for k in test_keys)
    idx = {n: i for i, n in enumerate(g2["Gene"].node_id)}, {n: i for i, n in enumerate(g2["Disease"].node_id)}
    test_ei, train_mask, edge_masks, info = split_graph(g2, et, test_keys, hidden, idx[0], idx[1],
                                                        edge_pair_keys(g2, et))
    dis = g2["Disease"].node_id
    trained_diseases = {dis[o] for o, m in zip(g2[et].edge_index[1].tolist(), train_mask.tolist()) if m}
    assert not (trained_diseases & hidden_ids)                  # OT edges to a hidden disease are hidden too
    tiga = ("Gene", "genetically_associated_with", "Disease")
    seen = {dis[o] for o, m in zip(g2[tiga].edge_index[1].tolist(), edge_masks[tiga].tolist()) if m}
    assert not (seen & hidden_ids)
    assert set(test_ei[1].tolist()) <= {idx[1][d] for d in hidden_ids}


def test_sample_negatives_avoid_known_pairs_and_are_the_same_in_every_graph():
    from linkpred.run import sample_negatives
    from linkpred.splits import pair_key
    known = {pair_key("G0", "D0"), pair_key("G0", "D1"), pair_key("G0", "D2")}   # only D3 is free for G0
    test_keys = [pair_key("G0", f"D{i}") for i in range(3)]
    candidates = ["D0", "D1", "D2", "D3"]
    neg = sample_negatives(test_keys, candidates, known, {f"D{i}": i for i in range(4)}, seed=0)
    assert (neg == 3).all() and neg.shape == (3,)
    # another graph with the same candidates but a different node order gives the same negative ids
    other_index = {"D3": 0, "D2": 1, "D1": 2, "D0": 3}
    neg2 = sample_negatives(test_keys, candidates, known, other_index, seed=0)
    assert [other_index["D3"]] * 3 == neg2.tolist()


def test_text_mining_pairs_are_not_test_items_but_stay_in_the_graph():
    pytest.importorskip("torch_geometric")
    from linkpred.run import reference_pairs, eligible_mask, find_task_edge_type, digest
    g0 = toy_graph()
    et = find_task_edge_type(g0, "B")
    g0[et].edge_knowledge_level = ["text_co_occurrence"] * 100 + ["knowledge_assertion"] * 200
    ok = eligible_mask(g0, et)
    assert ok.sum() == 200 and not ok[:100].any()
    ref = reference_pairs(g0, "B")
    text_only = set(edge_pair_keys_of(g0, et, range(100))) - set(edge_pair_keys_of(g0, et, range(100, 300)))
    assert not (set(ref) & text_only) and g0[et].edge_index.size(1) == 300    # excluded from tests, kept as context
    assert digest(ref) == digest(list(reversed(ref))) and digest(ref) != digest(ref[1:])


def edge_pair_keys_of(hd, et, positions):
    from linkpred.run import edge_pair_keys
    keys = edge_pair_keys(hd, et)
    return [keys[i] for i in positions]


def test_run_experiment_enriched_graph_uses_reference_pairs():
    pytest.importorskip("torch_geometric")
    from linkpred.run import run_experiment
    g0 = toy_graph()
    out = run_experiment("toy2", "B", "kfold", n_seeds=1, epochs=2, hidden=16, out=8, data=enriched_toy(g0),
                         reference=g0, log=lambda m: None)
    assert out[0]["test_from"] == "given" and out[0]["n_leak_edges_removed"] > 0 and 0.0 <= out[0]["auroc"] <= 1.0
    assert len(out[0]["test_set_digest"]) == 16
    assert 0 < out[0]["n_test"] <= 60                           # at most 300 L0 pairs over 5 folds
