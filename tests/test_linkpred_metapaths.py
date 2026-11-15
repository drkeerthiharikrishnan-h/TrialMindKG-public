"""Wave C / H of docs/ENRICHMENT_DIAGNOSIS_PLAN.md: meta-path counts and the meta-path-only model (metapaths.py),
degree bins, degree-matched negatives and per-stratum AUROC (strata.py), and their wiring in run.py.
The numpy parts run everywhere; the run_experiment test needs torch + torch_geometric (skipped in the cloud)."""
import sys
sys.path.insert(0, "pipelines")

import numpy as np
import pytest

from linkpred.metapaths import (typed_relations, enumerate_metapaths, metapath_counts, shape_name, fit_logistic,
                                predict_logistic, holdout_edges, metapath_aurocs, rel_key)
from linkpred.strata import bin_label, degree_bins, matched_candidates, stratified_aurocs
from linkpred.evaluate import summarise_run, report_markdown, auroc

GD = ("Gene", "gene_associated_with_condition", "Disease")
GP = ("Gene", "has_gene_product", "Protein")
PP = ("Protein", "interacts_with", "Protein")


def test_typed_relations_have_both_directions():
    rel = typed_relations({GP: np.array([[0, 1, 1], [0, 0, 0]])}, {"Gene": 2, "Protein": 1})
    assert set(rel) == {GP, rel_key(GP, reverse=True)} and rel_key(GP, True) == ("Protein", "has_gene_product^-1", "Gene")
    assert rel[GP].shape == (2, 1) and rel[rel_key(GP, True)].shape == (1, 2)
    assert rel[GP].nnz == 2 and set(rel[GP].data) == {1.0}          # the duplicate (1, 0) counts once


def test_enumerate_keeps_the_other_gene_of_a_disease_shape_and_skips_one_step():
    rel = typed_relations({GD: np.array([[0], [0]]), GP: np.array([[0], [0]])}, {"Gene": 1, "Disease": 1, "Protein": 1})
    shapes = enumerate_metapaths(rel, "Gene", "Disease", max_len=3)
    names = [shape_name(s) for s in shapes]
    assert all(len(s) >= 2 for s in shapes) and all(s[-1][2] == "Disease" for s in shapes)
    assert ("Gene -gene_associated_with_condition-> Disease <-gene_associated_with_condition- Gene "
            "-gene_associated_with_condition-> Disease") in names
    assert ("Gene -has_gene_product-> Protein <-has_gene_product- Gene -gene_associated_with_condition-> Disease"
            in names)
    assert [len(s) for s in shapes] == sorted(len(s) for s in shapes)
    assert len(enumerate_metapaths(rel, "Gene", "Disease", max_len=3, max_paths=1)) == 1


def test_metapath_counts_on_a_hand_checked_graph():
    # genes 0, 1 make proteins 0, 1; proteins interact; gene 1 has disease 0
    edges = {GD: np.array([[1], [0]]), GP: np.array([[0, 1], [0, 1]]), PP: np.array([[0], [1]])}
    rel = typed_relations(edges, {"Gene": 2, "Disease": 1, "Protein": 2})
    shape = (GP, PP, rel_key(GP, True))           # Gene -> Protein -> Protein <- Gene: ends at Gene, not used here
    assert metapath_counts(rel, [shape], np.array([0]), np.array([1]))[0, 0] == 1
    s3 = (GP, rel_key(GP, True), GD)               # gene -> its protein <- gene (itself) -> disease
    c = metapath_counts(rel, [s3], np.array([0, 1]), np.array([0, 0]), batch=1)
    assert c[0, 0] == 0 and c[1, 0] == 1           # gene 1 reaches disease 0 only through its own edge
    s4 = (GP, PP, rel_key(GP, True), GD)
    assert metapath_counts(rel, [s4], np.array([0]), np.array([0]))[0, 0] == 1   # 4 steps: partner gene has it


def test_logistic_regression_separates_and_handles_constant_columns():
    X = np.c_[np.r_[np.zeros(20), np.ones(20)], np.ones(40)]
    y = np.r_[np.zeros(20), np.ones(20)]
    m = fit_logistic(X, y)
    s = predict_logistic(m, X)
    assert auroc(y, s) == 1.0 and np.isfinite(s).all()


def test_holdout_edges_is_deterministic_and_never_empty():
    ei = np.array([[0, 1, 2], [0, 0, 1]])
    assert holdout_edges(ei, 0.1, 0).tolist() == holdout_edges(ei, 0.1, 0).tolist()
    assert holdout_edges(ei, 0.0, 3).sum() == 1
    assert holdout_edges(np.array([[0] * 200, list(range(200))]), 0.5, 1).mean() == pytest.approx(0.5, abs=0.15)


def _planted(n_gene=60, n_dis=20, seed=0):
    """Gene g makes protein g; proteins of genes with the same (g % n_dis) interact; gene g has disease g % n_dis
    and one random disease. A held-out pair (g, g % n_dis) is then reachable through a partner gene's protein."""
    rng = np.random.default_rng(seed)
    g = np.arange(n_gene)
    gd = np.c_[np.c_[g, g % n_dis].T, np.c_[g, rng.integers(0, n_dis, n_gene)].T]
    pp = np.array([[a, b] for a in g for b in g if a < b and a % n_dis == b % n_dis]).T
    return {GD: gd, GP: np.c_[g, g].T, PP: pp}, {"Gene": n_gene, "Disease": n_dis, "Protein": n_gene}


def test_metapath_model_finds_planted_signal_and_reports_shapes():
    edges, counts = _planted()
    test = np.array([[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]])          # pairs (g, g % 20) held out of the graph
    keep = ~np.isin(np.arange(edges[GD].shape[1]), [0, 1, 2, 3, 4])
    train = {**edges, GD: edges[GD][:, keep]}
    neg = np.array([[0, 1, 2, 3, 4], [10, 11, 12, 13, 14]])
    m = metapath_aurocs(train, counts, GD, test, neg, candidates=list(range(20)), seed=0, max_len=4)
    assert m["auroc_metapath"] > 0.9 and m["metapath_coverage"] == 1.0
    assert m["metapath_n_shapes"] > 0 and m["metapath_fit_pairs"] >= 1 and len(m["metapath_shapes"]) <= 10
    assert all({"shape", "auroc", "coverage"} == set(r) for r in m["metapath_shapes"])
    # without the protein layer the planted signal is unreachable from the gene side except via its random disease
    bare = metapath_aurocs({GD: train[GD]}, {"Gene": 60, "Disease": 20}, GD, test, neg, list(range(20)), seed=0,
                           max_len=4)
    assert bare["auroc_metapath"] < m["auroc_metapath"]


def test_metapath_model_without_any_shape_returns_chance():
    m = metapath_aurocs({GD: np.array([[0], [0]])}, {"Gene": 2, "Disease": 2}, GD, np.array([[1], [1]]),
                        np.array([[1], [0]]), candidates=[0, 1], max_len=1)
    assert m["auroc_metapath"] == 0.5 and m["metapath_coverage"] == 0.0 and m["metapath_shapes"] == []


def test_degree_bins_and_matched_candidates():
    assert [bin_label(d) for d in (1, 5, 6, 20, 21, 100, 101, 500, 501)] == [
        "1-5", "1-5", "6-20", "6-20", "21-100", "21-100", "101-500", "101-500", ">500"]
    keys = [f"G{i}\tD0" for i in range(7)] + ["G0\tD1", "G1\tD2", "G2\tD3"]
    bins = degree_bins(keys, side=1)
    assert bins == {"D0": "6-20", "D1": "1-5", "D2": "1-5", "D3": "1-5"}
    assert degree_bins(keys, side=0)["G0"] == "1-5"
    pick = matched_candidates(["D0", "D1", "D2", "D3"], bins)
    assert pick("D1") == ["D1", "D2", "D3"]
    assert pick("D0") == ["D0", "D1", "D2", "D3"]                 # its bin holds only itself: fall back to all


def test_degree_matched_negatives_stay_in_the_true_diseases_bin():
    from linkpred.run import negative_ids
    keys = [f"G{i}\tD0" for i in range(30)] + [f"G{i}\tD{j}" for i in range(2) for j in range(1, 6)]
    bins = degree_bins(keys)
    cands = sorted({k.split("\t")[1] for k in keys} | {"D9"})
    bins["D9"] = ">100"; bins["D8"] = ">100"; cands.append("D8")
    test = [f"G5\tD1", "G6\tD2"]
    negs = negative_ids(test, cands, set(keys), seed=0, pick=matched_candidates(cands, bins))
    assert all(bins[n] == "1-5" for n in negs)
    assert negs == negative_ids(test, cands, set(keys), seed=0, pick=matched_candidates(cands, bins))


def test_stratified_aurocs_score_each_stratum_on_its_own_pairs():
    pos, neg = [0.9, 0.8, 0.1, 0.2, 0.7, 0.6], [0.1, 0.2, 0.9, 0.8, 0.5, 0.5]
    out = stratified_aurocs(pos, neg, ["a", "a", "b", "b", "c", "c"], prefix="stratum_disease", min_pairs=2)
    assert out["stratum_disease_a"] == 1.0 and out["stratum_disease_b"] == 0.0 and out["stratum_disease_c"] == 1.0
    assert out["stratum_disease_a_n"] == 2
    few = stratified_aurocs(pos, neg, ["a"] * 6, min_pairs=10)
    assert few == {"auroc_a_n": 6}


def test_fold_and_seed_averages_tolerate_missing_strata_and_report_shows_them():
    from linkpred.run import average_folds
    folds = [{"auroc": 0.8, "stratum_disease_1-5": 0.6, "stratum_disease_1-5_n": 4, "metapath_shapes": [{"x": 1}]},
             {"auroc": 0.6, "stratum_disease_1-5_n": 1}]
    avg = average_folds(folds)
    assert avg["auroc"] == pytest.approx(0.7) and avg["stratum_disease_1-5"] == 0.6
    assert avg["metapath_shapes"] == [{"x": 1}]


def test_summarise_and_report_with_strata_and_metapaths():
    seeds = [{"auroc": 0.8, "auroc_metapath": 0.7, "metapath_coverage": 0.5, "stratum_disease_6-20": 0.9,
              "stratum_disease_6-20_n": 10, "stratum_disease_>100": 0.7, "stratum_disease_>100_n": 3,
              "metapath_shapes": [], "test_from": "G0"},
             {"auroc": 0.6, "auroc_metapath": 0.7, "metapath_coverage": 0.5, "stratum_disease_6-20_n": 4}]
    s = summarise_run(seeds)
    assert s["auroc"]["mean"] == 0.7 and s["stratum_disease_6-20"]["mean"] == 0.9 and "metapath_shapes" not in s
    rep = report_markdown({"G0": {"B": {"kfold": s}}})
    assert "meta-path model" in rep and "0.7000" in rep
    assert rep.index("disease_6-20") < rep.index("disease_>100")
    assert "0.900 (n 7)" in rep


def test_run_experiment_with_metapaths_strata_and_degree_matched_negatives():
    pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from test_linkpred import toy_graph
    from linkpred.run import run_experiment
    out = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=2, hidden=16, out=8, data=toy_graph(),
                         log=lambda m: None, metapaths=True, metapath_len=2, neg_match="degree", strata=True)[0]
    assert 0.0 <= out["auroc_metapath"] <= 1.0 and 0.0 <= out["metapath_coverage"] <= 1.0
    assert any(k.startswith("stratum_disease_") for k in out) and any(k.startswith("stratum_gene_") for k in out)
    with pytest.raises(ValueError):
        run_experiment("toy", "B", "kfold", n_seeds=1, epochs=1, data=toy_graph(), log=lambda m: None,
                       neg_match="popular")


def test_match_bins_are_powers_of_two():
    from linkpred.strata import match_bins
    keys = [f"G{i}\tD{d}" for d, n in (("a", 1), ("b", 3), ("c", 4), ("d", 700)) for i in range(n)]
    assert match_bins(keys) == {"Da": "log2_0", "Db": "log2_1", "Dc": "log2_2", "Dd": "log2_9"}


def test_project_bridge_restates_protein_steps_at_gene_level():
    from linkpred.metapaths import project_bridge, BRIDGE
    edges = {GD: np.array([[1], [0]]), GP: np.array([[0, 1], [0, 1]]), PP: np.array([[0], [1]]),
             ("SmallMolecule", "binds", "Protein"): np.array([[0], [0]])}
    rel = typed_relations(edges, {"Gene": 2, "Disease": 1, "Protein": 2, "SmallMolecule": 1})
    out = project_bridge(rel, BRIDGE)
    assert not any("Protein" in (k[0], k[2]) for k in out)          # protein steps and the bridge are gone
    gg = out[("Gene", "interacts_with@product", "Gene")]
    assert gg[0, 1] == 1 and gg.nnz == 1                              # gene 0's protein interacts with gene 1's
    assert out[("SmallMolecule", "binds@product", "Gene")][0, 0] == 1
    assert out[GD].nnz == 1
    shapes = [shape_name(s) for s in enumerate_metapaths(out, "Gene", "Disease", max_len=2)]
    assert "Gene -interacts_with@product-> Gene -gene_associated_with_condition-> Disease" in shapes
    assert project_bridge({GD: rel[GD]}) == {GD: rel[GD]}            # no bridge: unchanged


def test_planted_partner_signal_is_found_in_two_steps_through_the_bridge():
    edges, counts = _planted()
    test = np.array([[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]])
    keep = ~np.isin(np.arange(edges[GD].shape[1]), [0, 1, 2, 3, 4])
    train = {**edges, GD: edges[GD][:, keep]}
    neg = np.array([[0, 1, 2, 3, 4], [10, 11, 12, 13, 14]])
    m = metapath_aurocs(train, counts, GD, test, neg, candidates=list(range(20)), seed=0, max_len=2)
    assert m["auroc_metapath"] > 0.9
    assert m["metapath_shapes"][0]["shape"].startswith("Gene -interacts_with@product-> Gene")
    flat = metapath_aurocs(train, counts, GD, test, neg, candidates=list(range(20)), seed=0, max_len=2, bridge=None)
    assert flat["auroc_metapath"] < m["auroc_metapath"]


def test_cap_keeps_the_gene_protein_bridge_whole():
    pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from test_linkpred import toy_graph
    from linkpred.run import cap_edges
    hd = cap_edges(toy_graph(), 0, fraction=0.1, keep_whole=("has_gene_product",))
    assert hd[("Gene", "has_gene_product", "Protein")].edge_index.size(1) == 40
    assert hd[("Protein", "physically_interacts_with", "Protein")].edge_index.size(1) == 50
    cut = cap_edges(toy_graph(), 0, fraction=0.1)
    assert cut[("Gene", "has_gene_product", "Protein")].edge_index.size(1) == 4
