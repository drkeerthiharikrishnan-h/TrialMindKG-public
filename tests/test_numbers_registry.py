"""CS-3 (8 Nov review m2, m3): registry constants of pipelines/numbers_page.py. No data files: checks the specs only."""
import sys

sys.path.insert(0, "pipelines")
import numbers_page as npg  # noqa: E402


def _spec(eid):
    return next(e for e in npg.EXPERIMENTS if e["id"] == eid)


def test_rotate_dim512_tag_not_registered():
    # unequal kge_epochs_done across graphs (docs/EXPERIMENTS_LINKPRED.md section 2 note)
    assert not any("diag_kge_rotate_gpu" in name for name in npg.experiment_files())
    assert {t for _, t in _spec("E006")["variants"]} == {"diag_kge_rotate_d256"}


def test_d_uses_the_preregistered_g0_base():
    # loop/ledger.tsv row D: G0_B_kfold@diag_gnnpath_1m_gpu.json for GNN + paths
    assert {b for _, b in _spec("D")["pairs"]} == {("G0", "diag_gnnpath_1m_gpu")}
    assert {v for v, _ in _spec("D")["pairs"]} == {("G2", "diag_gnnpath_1m_release"), ("G3", "diag_gnnpath_1m_release")}


def test_d_metapath_row_registered():
    # loop/ledger.tsv row "D meta-path": base G0_B_kfold@frozen_a_g0_1m
    spec = _spec("D-metapath")
    assert {b for _, b in spec["pairs"]} == {("G0", "frozen_a_g0_1m")}
    assert {v for v, _ in spec["pairs"]} == {("G2", "diag_release_metapath"), ("G3", "diag_release_metapath")}
    assert spec["metrics"][0][0] == "auroc_metapath"
    assert "G2_B_kfold@diag_release_metapath.json" in npg.experiment_files()
