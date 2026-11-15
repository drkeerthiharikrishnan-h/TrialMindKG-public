"""Tests for pipelines/numbers_page.py: the Numbers page reads only frozen result files and the calibration file,
computes its sentences from them, and serves only allow-listed files. Fixture files only (no data, no torch)."""
import json
import sys

import pytest

sys.path.insert(0, "pipelines")
import numbers_page as npg  # noqa: E402

FROZEN_SETTINGS = {"epochs": 300, "hidden": 64, "seeds": 1, "max_edges_per_type": 2_000_000, "sample_fraction": None,
                   "wiring": {"test_levels": ["knowledge_assertion"], "drop_levels": ["text_co_occurrence"],
                              "positives": "assertions", "neg_match": "degree", "neg_pool": "training",
                              "reverse_edges": True, "learn_embeddings": True, "num_bases": 30},
                   "test_set": {"digest": "abc123"}}


def result(aurocs, metapath=None, settings=FROZEN_SETTINGS, digest="abc123"):
    """A result file in run.py's shape: one seed, one fold per value."""
    folds = [dict(auroc=a, **({"auroc_metapath": metapath[i]} if metapath else {})) for i, a in enumerate(aurocs)]
    mean = sum(aurocs) / len(aurocs)
    summary = {"auroc": {"mean": mean, "ci95": 0.0}, "auroc_degree_dst": {"mean": 0.508, "ci95": 0.0},
               "metapath_coverage": {"mean": 0.42, "ci95": 0.0},
               "stratum_disease_1-5": {"mean": 0.6, "ci95": 0.0}, "stratum_disease_1-5_n": {"mean": 900, "ci95": 0.0}}
    if metapath:
        summary["auroc_metapath"] = {"mean": sum(metapath) / len(metapath), "ci95": 0.0}
    s = json.loads(json.dumps(settings))
    s["test_set"] = {"digest": digest}
    return {"settings": s, "per_seed": [{"folds": folds}], "summary": summary}


@pytest.fixture
def repo(tmp_path):
    lp = tmp_path / "outputs" / "linkpred"
    lp.mkdir(parents=True)
    w = lambda name, obj: (lp / name).write_text(json.dumps(obj))
    w("G0_B_kfold@frozen_a.json", result([0.60, 0.61, 0.59, 0.60, 0.62], metapath=[0.60, 0.60, 0.60, 0.60, 0.60]))
    w("G2_B_kfold@frozen_a.json", result([0.65, 0.66, 0.64, 0.66, 0.67], metapath=[0.60, 0.61, 0.59, 0.60, 0.61]))
    bad = dict(FROZEN_SETTINGS, epochs=50)
    w("G3_B_kfold@frozen_a.json", result([0.60, 0.62, 0.58, 0.61, 0.60], settings=bad))
    w("G0_B_kfold.json", result([0.99] * 5))                     # a smoke run: must never appear
    w("G2_B_kfold@smoke.json", result([0.99] * 5))
    (lp / "G9_B_kfold@frozen_x.json").write_text("not json")    # unreadable: reported, not hidden
    cal = {"by_verdict": [{"verdict": "keep", "n": 300, "in_ot": 0.55, "ot_strong": 0.15},
                          {"verdict": "prune", "n": 5, "in_ot": 0.16, "ot_strong": 0.004}],
           "by_decile": [{"decile": d, "conf_min": d / 10, "conf_max": (d + 1) / 10, "n": 30,
                          "in_ot": [0.33, 0.43, 0.43, 0.48, 0.5, 0.55, 0.59, 0.64, 0.73, 0.82][d - 1],
                          "ot_strong": [0.05, 0.15, 0.07, 0.06, 0.06, 0.08, 0.1, 0.15, 0.27, 0.5][d - 1]}
                         for d in range(10, 0, -1)],                 # written in reverse: must come back sorted
           "by_source": [], "tinx_by_verdict": [], "n_non_text": 300, "n_text": 1000, "ot_threshold": 0.2,
           "n_ot_pairs": 7000}
    (tmp_path / "outputs" / "pharos").mkdir(parents=True)
    (tmp_path / "outputs" / "pharos" / "prune_calibration.json").write_text(json.dumps(cal))
    return tmp_path


def test_frozen_runs_reads_only_frozen_files_and_flags_deviations(repo):
    runs = npg.frozen_runs(repo / "outputs" / "linkpred")
    assert [r["label"] for r in runs] == ["G0@frozen_a", "G2@frozen_a", "G3@frozen_a", "G9@frozen_x"]
    g0, g2, g3, g9 = runs
    assert g0["auroc"]["mean"] == pytest.approx(0.604) and g0["degree_baseline"]["mean"] == 0.508
    assert g0["folds"] == 5 and g0["deviations"] == [] and g0["test_digest"] == "abc123"
    assert g0["strata"] == [dict(name="disease_1-5", auroc={"mean": 0.6, "ci95": 0.0}, n=900)]
    assert g3["deviations"] == ["epochs 50 (protocol 300)"]
    assert "error" in g9 and "auroc" not in g9


def test_protocol_deviations_allows_only_the_documented_fallback():
    assert npg.protocol_deviations(dict(FROZEN_SETTINGS, max_edges_per_type=1_000_000)) == []
    d = npg.protocol_deviations(dict(FROZEN_SETTINGS, max_edges_per_type=100_000, sample_fraction=0.01))
    assert len(d) == 2 and d[0].startswith("max_edges_per_type 100000")
    w = dict(FROZEN_SETTINGS["wiring"], neg_match="none")
    assert npg.protocol_deviations(dict(FROZEN_SETTINGS, wiring=w)) == ["neg_match none (protocol degree)"]
    assert len(npg.protocol_deviations({})) >= 10                        # nothing recorded = nothing assumed


def test_frozen_comparisons_are_paired_against_g0_of_the_same_tag(repo):
    comps = npg.frozen_comparisons(repo / "outputs" / "linkpred")
    assert [(c["tag"], c["base"]) for c in comps] == [("frozen_a", "G0@frozen_a")]     # frozen_x has no G0
    rows = {r["variant"]: r for r in comps[0]["rows"]}
    assert set(rows) == {"G2@frozen_a", "G3@frozen_a"}                               # not the smoke files
    g2 = rows["G2@frozen_a"]
    assert g2["n"] == 5 and g2["wins"] == 5 and g2["mean"] == pytest.approx(0.052) and g2["ci_low"] > 0
    assert g2["verdict"].startswith("G2@frozen_a scores higher than G0@frozen_a")
    assert rows["G3@frozen_a"]["verdict"].startswith("No clear difference")
    mp = comps[0]["metapath_rows"]
    assert [r["variant"] for r in mp] == ["G2@frozen_a"] and "No clear difference" in mp[0]["verdict"]


def test_calibration_sentences_follow_the_numbers(repo):
    cal = npg.calibration(repo / "outputs" / "pharos" / "prune_calibration.json")
    assert [d["decile"] for d in cal["by_decile"]] == list(range(1, 11))
    s = " ".join(cal["sentences"])
    assert "Least-confident tenth of the 300" in s and "5.0% also in Open Targets at score >= 0.2" in s
    assert "most-confident tenth: 50.0% (82.0%)" in s
    assert "rises in 8 of 9 steps" in s and "(at score >= 0.2: 6 of 9)" in s
    assert ("Pruned gene-disease edges that are not text-mined are less often found in Open Targets than kept ones: "
            "1 of the 5 pruned (16.0%) also appear there, against 55.0% of the 300 kept.") in s
    assert "covers few of them" not in s                                       # no text-mined count in this fixture
    path = repo / "outputs" / "pharos" / "prune_calibration.json"
    d = json.loads(path.read_text())
    try:
        path.write_text(json.dumps(dict(d, tinx_by_verdict=[{"verdict": "prune", "n": 40}])))
        assert "A further 40 pruned gene-disease edges are text-mined, so this check covers few of them." in " ".join(
            npg.calibration(path)["sentences"])
        path.write_text(json.dumps(dict(d, tinx_by_verdict=[{"verdict": "prune", "n": 2}])))
        assert "A further 2 pruned gene-disease edges are text-mined and are not in this check." in " ".join(
            npg.calibration(path)["sentences"])
    finally:
        path.write_text(json.dumps(d))
    assert npg.calibration(repo / "missing.json") is None


def test_numbers_and_source_file_allow_list(repo):
    n = npg.numbers(repo)
    assert n["benchmark"]["tags"] == ["frozen_a", "frozen_x"] and "curated Pharos pairs" in n["benchmark"]["protocol"]
    assert "G0_B_kfold.json" not in n["sources"] and n["sources"]["G2_B_kfold@frozen_a.json"].endswith("G2_B_kfold@frozen_a.json")
    assert "prune_calibration.json" in n["sources"] and "compare.md" not in n["sources"]   # not written in the fixture
    assert npg.source_file(repo, "G2_B_kfold@frozen_a.json").name == "G2_B_kfold@frozen_a.json"
    assert npg.source_file(repo, "G0_B_kfold.json") is None                   # smoke file: not served
    assert npg.source_file(repo, "../../CLAUDE.md") is None and npg.source_file(repo, "G2_B_kfold@frozen_../x.json") is None
    assert npg.source_file(repo, "prune_calibration.json").is_file()
    empty = npg.numbers(repo / "nowhere")
    assert empty["benchmark"]["runs"] == [] and empty["calibration"] is None and empty["sources"] == {}


def test_missing_result_files_are_read_from_the_fallback_root(repo, tmp_path_factory):
    """CC-32: the hosted data root (TMKG_PHAROS_ROOT) may hold no outputs/linkpred; the image copy of the repo is then
    read instead. A file present in both is read from the data root."""
    data = tmp_path_factory.mktemp("data")                        # the hosted data root: no result files
    n = npg.numbers(data, fallback_root=repo)
    assert n["benchmark"]["tags"] == ["frozen_a", "frozen_x"] and n["calibration"] is not None
    assert "G2_B_kfold@frozen_a.json" in n["fallback_files"] and "prune_calibration.json" in n["fallback_files"]
    assert npg.source_file(data, "G2_B_kfold@frozen_a.json") is None
    assert npg.source_file(data, "G2_B_kfold@frozen_a.json", fallback_root=repo).parent == repo / "outputs" / "linkpred"
    assert npg.source_file(data, "../../CLAUDE.md", fallback_root=repo) is None
    # the data root wins where it has the file
    lp = data / "outputs" / "linkpred"
    lp.mkdir(parents=True)
    (lp / "G0_B_kfold@frozen_a.json").write_text(json.dumps(result([0.70] * 5)))
    n2 = npg.numbers(data, fallback_root=repo)
    g0 = [r for r in n2["benchmark"]["runs"] if r["label"] == "G0@frozen_a"][0]
    assert g0["auroc"]["mean"] == pytest.approx(0.70) and "G0_B_kfold@frozen_a.json" not in n2["fallback_files"]
    assert "G2_B_kfold@frozen_a.json" in n2["fallback_files"]
    assert npg.numbers(repo, fallback_root=repo)["fallback_files"] == []     # same folder: no fallback


def test_epochs_caveat_is_computed_from_kge_epochs_done():
    def kge(epochs):
        return {"summary": {"auroc": {"mean": 0.6, "ci95": 0.0}, "kge_epochs_done": {"mean": epochs, "ci95": 0.0}}}
    s = npg.epochs_caveat("G2@t", kge(118.6), "G0@t", kge(143.8))
    assert "118.6" in s and "143.8" in s and "G2@t" in s and "G0@t" in s and s.startswith("Caveat")
    assert npg.epochs_caveat("G2@t", kge(100), "G0@t", kge(100)) is None
    assert npg.epochs_caveat("G2@t", result([0.6] * 5), "G0@t", kge(100)) is None     # R-GCN runs: no such field


def test_experiment_sentences_carry_the_epochs_caveat(tmp_path):
    lp = tmp_path / "outputs" / "linkpred"
    lp.mkdir(parents=True)
    for g, a, ep in (("G0", 0.60, 140.0), ("G2", 0.61, 120.0), ("G3", 0.62, 140.0)):
        r = result([a] * 5)
        r["summary"]["kge_epochs_done"] = {"mean": ep, "ci95": 0.0}
        (lp / f"{g}_B_kfold@diag_kge_rotate_d256.json").write_text(json.dumps(r))
    e006 = [e for e in npg.experiments(lp) if e["id"] == "E006"][0]
    caveats = [s for s in e006["sentences"] if s.startswith("Caveat")]
    assert len(caveats) == 1 and "G2@diag_kge_rotate_d256" in caveats[0] and "120" in caveats[0] and "140" in caveats[0]
