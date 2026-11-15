"""pipelines/linkpred/controls.py (coverage-matched meta-path AUROC) and the compare.py additions (meta-path by
default, report.md without stale smoke files). numpy / json only: runs everywhere."""
import json
import sys
sys.path.insert(0, "pipelines")

import numpy as np
import pytest

from linkpred import compare as cmp
from linkpred import controls as ct
from linkpred.metapaths import metapath_aurocs
from test_linkpred_metapaths import GD, _planted


def _scores(keys, labels, scores, has):
    return {"key": list(keys), "label": np.array(labels, float), "score": np.array(scores, float),
            "has_path": np.array(has, bool)}


# ---- coverage_matched on hand-made numbers ----
def test_coverage_matched_restricts_to_pairs_with_a_path_in_both_graphs():
    keys = ["a|1", "b|2", "c|3", "d|4", "e|5", "f|6"]
    y = [1, 1, 1, 0, 0, 0]
    # graph A (enriched) covers everything; graph B covers only a, b, d, e (c and f have no path)
    a = _scores(keys, y, [0.9, 0.8, 0.7, 0.2, 0.3, 0.1], [1] * 6)
    b = _scores(keys, y, [0.6, 0.4, 0.5, 0.5, 0.5, 0.5], [1, 1, 0, 1, 1, 0])
    m = ct.coverage_matched(a, b)
    assert (m["n_common"], m["n_matched"], m["n_pos"], m["n_neg"]) == (6, 4, 2, 2)
    assert m["share_matched"] == pytest.approx(4 / 6)
    assert m["auroc_a"] == 1.0                                   # on a, b | d, e: 0.9, 0.8 vs 0.2, 0.3
    assert m["auroc_b"] == pytest.approx(0.5)                    # 0.6, 0.4 vs 0.5, 0.5
    assert m["delta"] == pytest.approx(0.5)


def test_coverage_matched_aligns_by_key_not_by_position_and_ignores_pairs_only_one_graph_scored():
    a = _scores(["x|1", "y|2", "z|3", "w|4"], [1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], [1, 1, 1, 1])
    b = _scores(["y|2", "x|1", "q|9", "w|4", "z|3"], [0, 1, 1, 0, 1], [0.3, 0.7, 0.5, 0.4, 0.6], [1, 1, 1, 1, 1])
    m = ct.coverage_matched(a, b)
    assert m["n_common"] == 4 and m["n_matched"] == 4 and m["delta"] == pytest.approx(0.0)   # both rank perfectly


def test_coverage_matched_raises_on_different_labels_and_returns_none_without_both_classes():
    a = _scores(["x|1", "y|2"], [1, 0], [1, 0], [1, 1])
    with pytest.raises(ValueError):
        ct.coverage_matched(a, _scores(["x|1", "y|2"], [0, 0], [1, 0], [1, 1]))
    none = ct.coverage_matched(a, _scores(["x|1", "y|2"], [1, 0], [1, 0], [1, 0]))    # y has no path in B: no negative
    assert none["n_matched"] == 1 and none["auroc_a"] is None and none["delta"] is None


# ---- the interval and the sentence ----
def _folds(deltas, share=0.9):
    return [{"n_common": 100, "n_matched": int(share * 100), "share_matched": share, "n_pos": 45, "n_neg": 45,
             "auroc_a": 0.7 + d, "auroc_b": 0.7, "delta": d} for d in deltas]


def test_summarise_matched_interval_and_wording_follow_the_numbers():
    s = ct.summarise_matched(_folds([0.05, 0.06, 0.04, 0.05, 0.05]))
    assert s["n"] == 5 and s["mean"] == pytest.approx(0.05) and s["wins"] == 5 and s["ci_low"] > 0
    assert "still scores higher" in ct.matched_sentence(s) and "5 of 5 folds" in ct.matched_sentence(s)
    assert "100%" in ct.matched_sentence(s, unmatched_delta=0.05)
    gone = ct.summarise_matched(_folds([0.01, -0.02, 0.0, 0.015, -0.01]))
    sentence = ct.matched_sentence(gone, "G2", "G0", 0.08)
    assert gone["ci_low"] < 0 < gone["ci_high"] and "no clear difference" in sentence and "not shown to be more than coverage" in sentence
    lower = ct.summarise_matched(_folds([-0.05, -0.06, -0.04, -0.05, -0.05]))
    assert "scores LOWER" in ct.matched_sentence(lower)
    few = ct.summarise_matched(_folds([0.05]))
    assert few["ci_low"] is None and "too few" in ct.matched_sentence(few)
    assert ct.summarise_matched([])["n"] == 0 and ct.summarise_matched([])["mean"] is None


def test_matched_markdown_lists_each_fold_and_the_sentence():
    text = ct.matched_markdown(_folds([0.05, 0.06, 0.04]), "G2", "G0", unmatched_delta=0.08)
    assert "| 0 | 100 | 90 | 90.0% | 45 / 45 |" in text and "G2 vs G0" in text and "of the unmatched difference" in text


# ---- metapath_pair_scores reproduces the frozen metapath_aurocs ----
def _setup():
    edges, counts = _planted()
    test = np.array([[0, 1, 2, 3, 4], [0, 1, 2, 3, 4]])
    keep = ~np.isin(np.arange(edges[GD].shape[1]), [0, 1, 2, 3, 4])
    train = {**edges, GD: edges[GD][:, keep]}
    neg = np.array([[0, 1, 2, 3, 4], [10, 11, 12, 13, 14]])
    return edges, counts, train, test, neg


def test_pair_scores_reproduce_metapath_aurocs_exactly():
    _, counts, train, test, neg = _setup()
    names = ([f"Gene:{i}" for i in range(60)], [f"Disease:{i}" for i in range(20)])
    ref = metapath_aurocs(train, counts, GD, test, neg, candidates=list(range(20)), seed=0, max_len=4)
    got = ct.metapath_pair_scores(train, counts, GD, test, neg, list(range(20)), *names, seed=0, max_len=4)
    assert got["auroc"] == pytest.approx(ref["auroc_metapath"]) and got["coverage"] == pytest.approx(ref["metapath_coverage"])
    assert got["key"][0] == "Gene:0|Disease:0" and got["key"][5] == "Gene:0|Disease:10"
    assert got["label"].tolist() == [1] * 5 + [0] * 5 and got["has_path"].shape == (10,)


def test_pair_scores_without_any_shape_are_chance_and_uncovered():
    got = ct.metapath_pair_scores({GD: np.array([[0], [0]])}, {"Gene": 2, "Disease": 2}, GD, np.array([[1], [1]]),
                                  np.array([[1], [0]]), [0, 1], ["g0", "g1"], ["d0", "d1"], max_len=1)
    assert got["auroc"] == 0.5 and got["coverage"] == 0.0 and not got["has_path"].any()


def test_a_thinner_graph_gives_a_matched_comparison_end_to_end():
    _, counts, train, test, neg = _setup()
    names = ([f"Gene:{i}" for i in range(60)], [f"Disease:{i}" for i in range(20)])
    full = ct.metapath_pair_scores(train, counts, GD, test, neg, list(range(20)), *names, seed=0, max_len=4)
    bare = ct.metapath_pair_scores({GD: train[GD]}, {"Gene": 60, "Disease": 20}, GD, test, neg, list(range(20)),
                                   *names, seed=0, max_len=4)
    m = ct.coverage_matched(full, bare)
    assert m["n_common"] == 10 and m["n_matched"] <= 10 and m["share_matched"] == m["n_matched"] / 10


# ---- compare.py: meta-path by default, tagged-only report ----
def _result(fold_vals, digest="abc", **extra):
    folds = [{"auroc": v, "auroc_metapath": v + 0.1, "metapath_coverage": 0.9, **extra} for v in fold_vals]
    return {"settings": {"test_set": {"digest": digest}}, "per_seed": [{"folds": folds}],
            "summary": {"auroc": sum(fold_vals) / len(fold_vals), "auroc_metapath": 0.8}}


def _write(d, name, obj):
    (d / name).write_text(json.dumps(obj))


def test_compare_metrics_reports_metapath_first_and_labels_it_exploratory(tmp_path):
    _write(tmp_path, "G0_B_kfold@frozen_a.json", _result([0.60, 0.61, 0.60, 0.59, 0.60]))
    _write(tmp_path, "G2_B_kfold@frozen_a.json", _result([0.62, 0.62, 0.63, 0.61, 0.62]))
    text, rows = cmp.compare_metrics(tmp_path, base="G0@frozen_a")
    assert list(rows) == ["auroc_metapath", "metapath_coverage", "auroc"]
    assert text.index("auroc_metapath") < text.index("(task B, split kfold, auroc)")
    assert "exploratory until the confound controls" in text and "the GNN: the primary frozen metric" in text
    assert rows["auroc_metapath"][0]["n"] == 5


def test_compare_metrics_says_so_when_a_file_has_no_per_fold_values(tmp_path):
    old = {"settings": {"test_set": {"digest": "abc"}}, "per_seed": [{"auroc": 0.6}], "summary": {}}
    _write(tmp_path, "G0_B_kfold.json", old)
    _write(tmp_path, "G2_B_kfold.json", old)
    text, _ = cmp.compare_metrics(tmp_path, base="G0")
    assert text.count("No result file with per-fold values") == 3


def test_cli_default_writes_all_metric_sections_and_the_filtered_report(tmp_path, capsys):
    _write(tmp_path, "G0_B_kfold@frozen_a.json", _result([0.60, 0.61, 0.60]))
    _write(tmp_path, "G2_B_kfold@frozen_a.json", _result([0.62, 0.62, 0.63]))
    cmp.main(["--base", "G0@frozen_a", "--out-dir", str(tmp_path)])
    assert "auroc_metapath" in (tmp_path / "compare.md").read_text() and (tmp_path / "report.md").exists()


def test_report_lists_only_tagged_frozen_and_diag_runs_and_names_the_rest(tmp_path):
    summary = {"auroc": 0.6, "auprc": 0.5, "auroc_metapath": 0.7}
    for name in ("G0_B_kfold@frozen_a", "G2_B_kfold@frozen_a", "G2_B_kfold@diag_shuffle"):
        _write(tmp_path, name + ".json", {"summary": summary, "per_seed": [], "settings": {}})
    for name in ("G0_B_kfold", "G0_B_kfold@dm", "G0_B_kfold@wired", "G1_B_kfold", "G3_B_kfold@dm"):
        _write(tmp_path, name + ".json", {"summary": {"auroc": 0.99}, "per_seed": [], "settings": {}})
    (tmp_path / "G4_B_kfold@frozen_x.json").write_text("not json")
    kept, excluded = cmp.split_results(tmp_path)
    assert sorted(kept) == ["G0", "G2"] and set(kept["G2"]["B"]) == {"kfold@frozen_a", "kfold@diag_shuffle"}
    assert excluded == ["G0_B_kfold.json", "G0_B_kfold@dm.json", "G0_B_kfold@wired.json", "G1_B_kfold.json",
                        "G3_B_kfold@dm.json", "G4_B_kfold@frozen_x.json"]
    path, _, _ = cmp.write_report(tmp_path)
    text = path.read_text()
    assert "kfold@frozen_a" in text and "kfold@diag_shuffle" in text and "0.9900" not in text
    assert "Not tabulated" in text and "- G0_B_kfold@dm.json" in text and "## G1" not in text
