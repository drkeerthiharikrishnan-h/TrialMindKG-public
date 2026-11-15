"""Torch-free tests of the shared-test-set helpers in pipelines/linkpred/run.py (digest, negatives)."""
import sys
sys.path.insert(0, "pipelines")

import numpy as np

from linkpred.run import cap_keep_indices, cap_size, choose_negative, collect_results, digest, load_reference, EXCLUDED_LEVELS
from linkpred.splits import pair_key


def test_digest_depends_on_pairs_not_order():
    a = [pair_key("NCBIGene:1", "MONDO:0000001"), pair_key("NCBIGene:2", "MONDO:0000002")]
    assert digest(a) == digest(list(reversed(a))) and len(digest(a)) == 16
    assert digest(a) != digest(a[:1])
    assert digest(a) != digest(a, excluded_levels=())          # the exclusion rule is part of the test set
    assert EXCLUDED_LEVELS == ("text_co_occurrence",)


def test_choose_negative_is_deterministic_and_skips_known_pairs():
    cands = [f"MONDO:{i:07d}" for i in range(50)]
    k = pair_key("NCBIGene:7", "MONDO:0000003")
    first = choose_negative(k, cands, set(), seed=0)
    assert first == choose_negative(k, cands, set(), seed=0) and first in cands
    assert first != choose_negative(k, cands, set(), seed=1) or True   # another seed may differ
    known = {pair_key("NCBIGene:7", c) for c in cands if c != "MONDO:0000049"}
    assert choose_negative(k, cands, known, seed=0, rounds=500) == "MONDO:0000049"


def test_cap_keep_indices_keeps_the_same_edges_in_a_subset_graph():
    ids = [f"e{i}" for i in range(300)]
    k0 = cap_keep_indices(ids, 300, 100, seed=0)
    assert len(k0) == 100 and list(k0) == sorted(k0) and len(set(k0)) == 100
    kept0 = {ids[i] for i in k0}
    # a graph holding a subset of the same edges (pruned copy, other order) keeps every shared kept edge
    rng = np.random.RandomState(1)
    sub = [ids[i] for i in rng.permutation(300)[:240]]
    kept1 = {sub[i] for i in cap_keep_indices(sub, 240, 100, seed=0)}
    assert kept0 & set(sub) <= kept1
    # another seed = another choice; under the cap = everything
    assert kept0 != {ids[i] for i in cap_keep_indices(ids, 300, 100, seed=1)}
    assert list(cap_keep_indices(ids, 300, 300, seed=0)) == list(range(300))


def test_cap_keep_indices_without_ids_is_seeded_by_edge_type_name():
    a = cap_keep_indices(None, 300, 50, seed=0, name="Gene|x|Disease")
    assert list(a) == list(cap_keep_indices(None, 300, 50, seed=0, name="Gene|x|Disease")) and len(set(a)) == 50
    assert list(a) != list(cap_keep_indices(None, 300, 50, seed=0, name="Protein|y|Protein"))


def test_cap_size_keeps_proportions_with_a_fraction_and_bounds_with_a_cap():
    assert cap_size(13_415_737) == 13_415_737 and cap_size(13_415_737, max_edges=100_000) == 100_000
    assert cap_size(13_415_737, fraction=0.01) == 134_158 and cap_size(50_000, fraction=0.01) == 500
    assert cap_size(13_415_737, max_edges=100_000, fraction=0.01) == 100_000        # the smaller of the two
    assert cap_size(50_000, max_edges=100_000, fraction=0.01) == 500
    assert cap_size(7, fraction=0.01) == 1 and cap_size(0, fraction=0.5) == 0       # never below one edge of a non-empty type


def test_load_reference_uses_g0_pairs_even_with_an_edge_cap(tmp_path, monkeypatch):
    import linkpred.run as run
    (tmp_path / "outputs" / "g0").mkdir(parents=True)
    (tmp_path / "outputs" / "g0" / "heterodata.pt").write_bytes(b"")
    monkeypatch.setattr(run, "load_graph", lambda name: name)
    monkeypatch.setattr(run, "reference_pairs", lambda data, task, test_levels=(): [f"{data}:{task}"])
    assert load_reference("B", "G3", max_edges=100000, root=tmp_path) == (["G0:B"], "G0")   # capped: still G0's pairs
    assert load_reference("B", "G3", max_edges=None, root=tmp_path) == (["G0:B"], "G0")
    assert load_reference("B", "G0", root=tmp_path) == (None, "self")                        # no graph handed in
    # G0 itself: its pairs come from the UNCAPPED graph handed in, same set + name as the other graphs get
    assert load_reference("B", "G0", max_edges=100000, root=tmp_path, data="G0") == (["G0:B"], "G0")
    assert load_reference("B", "G3", root=tmp_path / "nowhere") == (None, "self")            # no G0 file


def test_collect_results_reads_every_result_file_on_disk(tmp_path):
    import json
    (tmp_path / "G0_B_kfold.json").write_text(json.dumps({"summary": {"auroc": {"mean": 0.9, "ci95": 0.01}}}))
    (tmp_path / "G3_B_cold_disease.json").write_text(json.dumps({"summary": {"auroc": {"mean": 0.8, "ci95": 0.02}}}))
    (tmp_path / "broken_B_kfold.json").write_text("not json")
    (tmp_path / "report.md").write_text("# old report")
    got = collect_results(tmp_path)
    assert got == {"G0": {"B": {"kfold": {"auroc": {"mean": 0.9, "ci95": 0.01}}}},
                   "G3": {"B": {"cold_disease": {"auroc": {"mean": 0.8, "ci95": 0.02}}}}}
    assert collect_results(tmp_path / "empty") == {}
