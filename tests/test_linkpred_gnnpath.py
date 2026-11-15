"""CC-14: pipelines/linkpred/gnnpath.py (GNN + path features, exploratory E008) on fixture graphs only, CPU."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "pipelines")

import numpy as np
import pytest
torch = pytest.importorskip("torch")
pytest.importorskip("torch_geometric")

from test_linkpred import toy_graph                 # noqa: E402
from test_linkpred_kge import _community_graph      # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GD = ("Gene", "gene_associated_with_condition", "Disease")


def _run(data, **kw):
    from linkpred import gnnpath
    from linkpred.run import run_experiment
    with gnnpath.patched(**kw):
        return run_experiment("toy", "B", "kfold", n_seeds=1, epochs=3, hidden=16, out=8, data=data,
                              log=lambda m: None)


def test_tag_guard_refuses_a_frozen_looking_tag():
    from linkpred import gnnpath
    with pytest.raises(SystemExit):
        gnnpath.main(["--tag", "frozen_x"])
    with pytest.raises(SystemExit):
        gnnpath.main(["--graphs", "G0"])


def test_patched_swaps_and_restores_the_model_step():
    from linkpred import gnnpath, run
    original = run._train_and_eval
    with gnnpath.patched(fit_frac=0.2):
        assert run._train_and_eval is gnnpath.gnnpath_train_eval and gnnpath._SETTINGS["fit_frac"] == 0.2
    assert run._train_and_eval is original and gnnpath._SETTINGS == gnnpath.GNNPATH_DEFAULTS
    with pytest.raises(TypeError):
        with gnnpath.patched(bogus=1):
            pass


def test_fit_pairs_leave_every_gene_disease_edge_type_both_orientations():
    from linkpred.gnnpath import split_fit_pairs
    hd = toy_graph()
    rev = ("Disease", "rev_assoc", "Gene")
    other = ("Gene", "other_assoc", "Disease")
    hd[rev].edge_index = hd[GD].edge_index.flip(0)                      # same pairs, other orientation
    hd[other].edge_index = hd[GD].edge_index[:, :150]                   # a second gene-disease type
    n = hd[GD].edge_index.size(1)
    train_mask = torch.ones(n, dtype=torch.bool)
    train_mask[:20] = False                                             # pretend 20 edges are test edges
    existing = {other: torch.arange(150) % 7 != 0}                      # an existing keep mask must survive
    tm2, masks2, fit = split_fit_pairs(hd, GD, train_mask, existing, 0.3, seed=0)
    assert fit and not (tm2 & ~train_mask).any()                        # only ever removes
    ei = hd[GD].edge_index
    kept_task = {(int(a), int(b)) for a, b in ei[:, tm2].T}
    assert not (kept_task & fit)
    for et in (rev, other):
        e = hd[et].edge_index[:, masks2[et]]
        pairs = {(int(b), int(a)) for a, b in e.T} if et == rev else {(int(a), int(b)) for a, b in e.T}
        assert not (pairs & fit), et
    assert not masks2[other][torch.arange(150) % 7 == 0].any()          # AND with the old mask, never replaced
    assert fit <= {(int(a), int(b)) for a, b in ei[:, train_mask].T}    # fit pairs come from TRAINING edges only


def test_output_keys_and_ranges():
    out = _run(toy_graph())
    f = out[0]["folds"][0]
    for k in ("auroc", "auroc_gnnpath", "auroc_gnnhead", "auroc_pathhead"):
        assert 0.0 <= f[k] <= 1.0, k
    assert f["gnnpath_fit_pairs"] > 0 and f["gnnpath_n_path_features"] >= 0 and f["n_test"] > 0


def test_same_test_pairs_as_the_rgcn_run():
    from linkpred.run import run_experiment
    base = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=1, hidden=16, out=8, data=toy_graph(),
                          log=lambda m: None)
    alt = _run(toy_graph())
    assert base[0]["test_set_digest"] == alt[0]["test_set_digest"]
    for a, b in zip(base[0]["folds"], alt[0]["folds"]):
        assert a["n_test"] == b["n_test"] and a["n_leak_edges_removed"] == b["n_leak_edges_removed"]


def test_determinism_same_seed_same_auroc_gnnpath():
    a = _run(toy_graph())[0]["folds"][0]["auroc_gnnpath"]
    b = _run(toy_graph())[0]["folds"][0]["auroc_gnnpath"]
    assert a == b


def test_planted_signal_is_found_by_the_path_head():
    """Two communities (genes 0-19 <-> diseases 0-14, genes 20-39 <-> 15-29): a held-out pair shares gene-disease-gene-
    disease paths with its community, a random negative mostly does not."""
    f = _run(_community_graph(), fit_frac=0.3)[0]["folds"][0]
    assert f["auroc_pathhead"] > 0.8 and f["auroc_gnnpath"] > 0.8, f


def test_heads_never_use_test_rows_for_fitting():
    """Changing the test features leaves the fitted weights (and so the fit-set scores) unchanged."""
    from linkpred.gnnpath import fit_heads
    rng = np.random.default_rng(0)
    Xf, y = rng.normal(size=(60, 4)), np.r_[np.ones(30), np.zeros(30)]
    Xf[:30] += 1.0
    a = fit_heads({"h": Xf}, y, {"h": Xf[:5]})["h"]
    b = fit_heads({"h": Xf}, y, {"h": np.r_[Xf[:5], 100 * rng.normal(size=(50, 4))]})["h"][:5]
    assert np.allclose(a, b)


def test_strata_keys_are_prefixed_gnnpath():
    from linkpred import gnnpath
    from linkpred.run import run_experiment
    with gnnpath.patched():
        out = run_experiment("toy", "B", "kfold", n_seeds=1, epochs=2, hidden=16, out=8, data=_community_graph(),
                             strata=True, log=lambda m: None)
    keys = [k for k in out[0]["folds"][0] if k.startswith("stratum_gnnpath_")]
    assert keys or out[0]["folds"][0]["n_test"] < 5      # strata need >= 5 pairs per bin; on tiny folds none appear


def test_settings_are_recorded_in_the_result_json(tmp_path, monkeypatch):
    import json
    from linkpred import gnnpath
    out_dir = tmp_path / "outputs" / "linkpred"
    out_dir.mkdir(parents=True)
    (out_dir / "x@diag_gnnpath_t.json").write_text(json.dumps({"settings": {}}))
    monkeypatch.setattr(gnnpath, "ROOT", tmp_path)
    from linkpred import run
    monkeypatch.setattr(run, "main", lambda: None)
    gnnpath.main(["--gnnpath-fit-frac", "0.2", "--tag", "diag_gnnpath_t"])
    d = json.loads((out_dir / "x@diag_gnnpath_t.json").read_text())
    assert d["settings"]["gnnpath"]["fit_frac"] == 0.2 and d["settings"]["gnnpath"]["head"] == "logistic"


def test_frozen_files_are_unchanged():
    """run.py, splits.py, metapaths.py, evaluate.py must equal the versions on main (skipped when main is absent)."""
    if subprocess.run(["git", "rev-parse", "--verify", "-q", "main"], cwd=ROOT, capture_output=True).returncode:
        pytest.skip("no local main ref to compare with")
    for name in ("run.py", "splits.py", "metapaths.py", "evaluate.py"):
        rel = f"pipelines/linkpred/{name}"
        assert subprocess.run(["git", "diff", "--quiet", "main", "--", rel], cwd=ROOT).returncode == 0, \
            f"{rel} differs from main: frozen file edited"
