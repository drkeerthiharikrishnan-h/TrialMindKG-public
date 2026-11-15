"""Wave D (linkpred/ablate.py, run.apply_ablation) and wave G (linkpred/compare.py) of
docs/ENRICHMENT_DIAGNOSIS_PLAN.md. numpy / json parts run everywhere; the graph parts need torch (Mac)."""
import json
import sys
sys.path.insert(0, "pipelines")

import numpy as np
import pytest

from linkpred.ablate import layer_mask, keep_mask, shuffle_objects, random_drop_mask, describe
from linkpred.compare import fold_values, paired_delta, verdict, compare_all, load_result, t_quantile
from linkpred.run import result_name, average_folds


LAYERS = ["pharos", "pharos", "chembl37", "opentargets_layer", "chembl37", "pharos"]


def test_layer_and_keep_masks():
    assert layer_mask(LAYERS, ["chembl37"]).tolist() == [False, False, True, False, True, False]
    assert keep_mask(LAYERS, keep_layers=["pharos", "chembl37"]).tolist() == [True, True, True, False, True, True]
    assert keep_mask(LAYERS, drop_layers=["pharos"]).tolist() == [False, False, True, True, True, False]
    assert keep_mask(LAYERS).all()


def test_shuffle_keeps_degrees_and_leaves_other_edges_alone():
    ei = np.array([[0, 1, 2, 3, 4, 5, 6, 7], [10, 11, 12, 13, 14, 15, 16, 17]])
    mask = np.array([True] * 6 + [False] * 2)
    out = shuffle_objects(ei, mask, seed=1, name="x")
    assert out[0].tolist() == ei[0].tolist()                       # subjects (out-degree) untouched
    assert sorted(out[1, :6].tolist()) == sorted(ei[1, :6].tolist())   # same objects (in-degree), new pairs
    assert out[1, 6:].tolist() == [16, 17] and out[1, :6].tolist() != ei[1, :6].tolist()
    assert out.tolist() == shuffle_objects(ei, mask, seed=1, name="x").tolist()   # deterministic
    assert ei[1].tolist() == list(range(10, 18))                   # input not changed


def test_random_drop_removes_the_share_of_pharos_edges_only():
    layers = ["pharos"] * 100 + ["chembl37"] * 50
    stay = random_drop_mask(layers, 0.2, seed=0, name="x")
    assert (~stay[:100]).sum() == 20 and stay[100:].all()
    assert random_drop_mask(layers, 0.0).all()
    assert stay.tolist() == random_drop_mask(layers, 0.2, seed=0, name="x").tolist()


def test_describe_labels():
    assert describe() == ""
    assert describe(keep_layers=("pharos", "chembl37"), shuffle_layers=("chembl37",)) == \
        "keep pharos+chembl37, shuffle chembl37"
    assert describe(random_drop=0.05) == "random drop 0.05 of pharos"


def test_result_names_with_and_without_tag():
    assert result_name("G2", "B", "kfold") == "G2_B_kfold.json"
    assert result_name("G2", "B", "kfold", "no_ot") == "G2_B_kfold@no_ot.json"
    with pytest.raises(ValueError):
        result_name("G2", "B", "kfold", "a/b")


def test_average_folds_keeps_nothing_it_should_not():
    avg = average_folds([{"auroc": 0.5, "n_test": 10}, {"auroc": 0.7, "n_test": 12}])
    assert avg == {"auroc": pytest.approx(0.6), "n_test": 11}


def _result(fold_aurocs, digest="abc"):
    """A result file shaped like run.py's: per_seed[*].folds[*].auroc."""
    return {"settings": {"test_set": {"digest": digest}},
            "per_seed": [{"auroc": sum(f) / len(f), "folds": [{"auroc": v} for v in f]} for f in fold_aurocs],
            "summary": {}}


def test_paired_delta_and_verdict_wording_follow_the_numbers():
    base = _result([[0.80, 0.82, 0.81, 0.79, 0.80]])
    up = _result([[0.83, 0.85, 0.83, 0.82, 0.84]])
    d = paired_delta(up, base)
    assert d["n"] == 5 and d["wins"] == 5 and d["mean"] == pytest.approx(0.03) and d["ci_low"] > 0
    assert verdict(d, "G2", "G0").startswith("G2 scores higher than G0")
    mixed = _result([[0.81, 0.81, 0.80, 0.80, 0.80]])
    dm = paired_delta(mixed, base)
    assert dm["ci_low"] < 0 < dm["ci_high"] and verdict(dm, "G2", "G0").startswith("No clear difference")
    down = paired_delta(base, up)
    assert verdict(down, "G0", "G2").startswith("G0 scores lower than G2")
    other = paired_delta(_result([[0.9] * 5], digest="zzz"), base)
    assert "not comparable" in verdict(other, "G2", "G0")
    assert paired_delta({"per_seed": [{"auroc": 0.8}]}, base)["n"] == 0      # an old file without folds
    assert fold_values(_result([[0.1, 0.2], [0.3, 0.4]]))[(1, 0)] == 0.3


def test_t_quantile_matches_known_values():
    assert t_quantile(4) == pytest.approx(2.776, abs=0.01)
    assert t_quantile(1000) == pytest.approx(1.962, abs=0.01)


def test_compare_all_reads_every_variant_of_the_split(tmp_path):
    (tmp_path / "G0_B_kfold.json").write_text(json.dumps(_result([[0.8, 0.8, 0.81, 0.79, 0.8]])))
    (tmp_path / "G2_B_kfold.json").write_text(json.dumps(_result([[0.78, 0.79, 0.79, 0.77, 0.78]])))
    (tmp_path / "G2_B_kfold@shuffle_ot.json").write_text(json.dumps(_result([[0.75, 0.76, 0.75, 0.74, 0.75]])))
    (tmp_path / "G2_B_cold_disease.json").write_text(json.dumps(_result([[0.6]])))
    text, rows = compare_all(tmp_path, base="G0", all_tags=True)
    assert [r["variant"] for r in rows] == ["G2", "G2@shuffle_ot"]
    assert [r["variant"] for r in compare_all(tmp_path, base="G0")[1]] == ["G2"]        # same tag only
    assert "G2@shuffle_ot scores lower than G0" in text and "| G2 | 5 |" in text
    text2, rows2 = compare_all(tmp_path, base="G2@shuffle_ot", all_tags=True)
    assert [r["variant"] for r in rows2] == ["G0", "G2"] and "G2 scores higher than G2@shuffle_ot" in text2
    assert load_result(tmp_path / "G2_B_kfold@shuffle_ot.json")[0] == "G2@shuffle_ot"
    with pytest.raises(FileNotFoundError):
        compare_all(tmp_path, base="G9")


def test_compare_all_with_named_variants_pairs_a_differently_tagged_base(tmp_path):
    # CS-3: compare.md pairs G2/G3@frozen_a with the cap-matched G0@frozen_a_g0_1m (different tag)
    (tmp_path / "G0_B_kfold@frozen_a_g0_1m.json").write_text(json.dumps(_result([[0.8, 0.8, 0.81, 0.79, 0.8]])))
    (tmp_path / "G0_B_kfold@frozen_a.json").write_text(json.dumps(_result([[0.7, 0.7, 0.7, 0.7, 0.7]])))
    (tmp_path / "G2_B_kfold@frozen_a.json").write_text(json.dumps(_result([[0.82, 0.83, 0.82, 0.81, 0.82]])))
    (tmp_path / "G2_B_kfold@diag_x.json").write_text(json.dumps(_result([[0.9, 0.9, 0.9, 0.9, 0.9]])))
    text, rows = compare_all(tmp_path, base="G0@frozen_a_g0_1m", variants=["G2@frozen_a"])
    assert [r["variant"] for r in rows] == ["G2@frozen_a"]
    assert "G2@frozen_a scores higher than G0@frozen_a_g0_1m" in text
    with pytest.raises(FileNotFoundError):
        compare_all(tmp_path, base="G0@frozen_a_g0_1m", variants=["G3@frozen_a"])


def test_report_markdown_has_a_gnnpath_column():
    from linkpred.evaluate import report_markdown
    rep = report_markdown({"G2": {"B": {"kfold@diag_gnnpath_1m": {"auroc_gnnpath": {"mean": 0.793, "ci95": 0.0}}}}})
    assert "GNN + paths (auroc_gnnpath)" in rep and "| 0.7930 +/- 0.0000 |" in rep


def _layered_toy():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from test_linkpred import toy_graph
    hd = toy_graph()
    for et in hd.edge_types:
        n = hd[et].edge_index.size(1)
        hd[et].edge_layer = ["pharos" if (i % 3) else "chembl37" for i in range(n)] \
            if et[1] != "gene_associated_with_condition" else ["pharos"] * n
        hd[et].edge_id = [f"{et[1]}:{i}" for i in range(n)]
    return hd


def test_apply_ablation_drops_shuffles_and_keeps_attributes_aligned():
    from linkpred.run import apply_ablation
    hd = _layered_toy()
    pp = ("Protein", "physically_interacts_with", "Protein")
    n0 = hd[pp].edge_index.size(1)
    info = apply_ablation(hd, drop_layers=("chembl37",))
    n1 = hd[pp].edge_index.size(1)
    assert info["removed"] > 0 and n1 < n0 and len(hd[pp].edge_id) == n1 and set(hd[pp].edge_layer) == {"pharos"}
    hd2 = _layered_toy()
    before = hd2[pp].edge_index.clone()
    info2 = apply_ablation(hd2, shuffle_layers=("chembl37",))
    assert info2["shuffled"] > 0 and info2["removed"] == 0 and hd2[pp].edge_index.size(1) == n0
    assert not (hd2[pp].edge_index == before).all()
    hd3 = _layered_toy()
    task = ("Gene", "gene_associated_with_condition", "Disease")
    info3 = apply_ablation(hd3, keep_layers=("chembl37",), keep_types=(task,))
    assert task in hd3.edge_types and hd3[task].edge_index.size(1) == 0
    from test_linkpred import toy_graph
    with pytest.raises(ValueError):
        apply_ablation(toy_graph(), drop_layers=("chembl37",))


def test_run_experiment_with_an_ablation_writes_folds():
    from linkpred.run import run_experiment
    out = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=2, hidden=16, out=8, data=_layered_toy(),
                         log=lambda m: None, shuffle_layers=("chembl37",))[0]
    assert len(out["folds"]) == 5 and all("auroc" in f for f in out["folds"])
