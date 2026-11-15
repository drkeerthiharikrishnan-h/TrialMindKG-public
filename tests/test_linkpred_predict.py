"""Tests for pipelines/linkpred/predict.py: it accepts the frozen run's graph-preparation flags with run.py's names,
choices and defaults, records them, and prepares the graph with run.py's functions in run.py's order.
The parser tests need no torch; the graph test runs where torch_geometric is installed (the Mac)."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pipelines")
from linkpred import predict  # noqa: E402
from linkpred import run  # noqa: E402

FROZEN_A = ("--graph G3 --tasks B --top 50 --epochs 300 --hidden 64 --drop-levels text_co_occurrence "
            "--test-levels knowledge_assertion --positives assertions --max-edges 2000000 --reverse-edges "
            "--learn-embeddings --num-bases 30 --neg-pool training").split()
PREP_FLAGS = ["--epochs", "--hidden", "--reverse-edges", "--learn-embeddings", "--num-bases", "--neg-pool",
              "--positives", "--drop-levels", "--test-levels", "--max-edges", "--sample-fraction", "--keep-whole"]


def run_py_flags():
    """{flag: (default, choices)} from run.py's argparse calls (read from the source; run.py builds its parser
    inside main)."""
    src = Path(run.__file__).read_text()
    out = {}
    for m in re.finditer(r'p\.add_argument\("(--[a-z-]+)"([^)]*)', src):
        d = re.search(r"default=([^,\n]+)", m.group(2))
        c = re.search(r"choices=(\[[^\]]*\])", m.group(2))
        out[m.group(1)] = (d.group(1).strip() if d else None, c.group(1) if c else None)
    return out


def test_frozen_flags_parse_and_are_recorded():
    a = predict.parse_args(FROZEN_A)
    assert a.epochs == 300 and a.hidden == 64 and a.max_edges == 2_000_000 and a.neg_pool == "training"
    assert a.positives == "assertions" and a.reverse_edges and a.learn_embeddings and a.num_bases == 30
    st = predict.settings(a)
    assert st["drop_levels"] == ["text_co_occurrence"] and st["test_levels"] == ["knowledge_assertion"]
    assert st["tasks"] == ["B"] and st["keep_whole"] is None
    assert predict.settings(predict.parse_args(["--keep-whole", ""]))["keep_whole"] == []


def test_preparation_flags_match_run_py():
    flags = run_py_flags()
    for f in PREP_FLAGS:
        assert f in flags, f"{f} is not a run.py flag"
    defaults = vars(predict.parse_args([]))
    assert defaults["positives"] == "all" == flags["--positives"][0].strip('"')
    assert flags["--positives"][1] == '["all", "curated", "assertions"]'
    assert defaults["neg_pool"] == "all" and defaults["drop_levels"] == "" and defaults["max_edges"] == 0
    with pytest.raises(SystemExit):
        predict.parse_args(["--neg-pool", "reference"])     # needs a test set: not offered here


def test_context_relation_name():
    assert run.CONTEXT_SUFFIX == "_context" and run.TEXT_MINED_SUFFIX == run.CONTEXT_SUFFIX


def test_prepare_graph_follows_run_py_order():
    pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from test_linkpred import toy_graph
    hd = toy_graph()
    et = ("Gene", "gene_associated_with_condition", "Disease")
    n = hd[et].edge_index.size(1)
    levels = ["text_co_occurrence", "statistical_association", "knowledge_assertion"]
    hd[et].edge_knowledge_level = [levels[i % 3] for i in range(n)]
    hd[et].edge_id = [f"e{i}" for i in range(n)]
    data, info = predict.prepare_graph(hd, "B", drop_levels=["text_co_occurrence"], positives="assertions",
                                       max_edges=60, log=lambda *_: None)
    ctx = (et[0], et[1] + "_context", et[2])
    assert info["levels_moved"] == ["statistical_association"] and info["moved_to_context"] == n // 3
    assert set(data[et].edge_knowledge_level) == {"knowledge_assertion"} and data[et].edge_index.size(1) == 60
    assert set(data[ctx].edge_knowledge_level) == {"statistical_association"}
    assert data["Gene", "has_gene_product", "Protein"].edge_index.size(1) == 40          # bridge never capped
    assert data["Protein", "physically_interacts_with", "Protein"].edge_index.size(1) == 60


# ---------------------------------------------------------------- meta-path predictions (T-006 / CC-3)
import gzip  # noqa: E402
import json  # noqa: E402

import numpy as np  # noqa: E402

GD = ("Gene", "gene_associated_with_condition", "Disease")
GP = ("Gene", "has_gene_product", "Protein")
PP = ("Protein", "interacts_with", "Protein")


def planted(n_gene=60, n_dis=20, seed=0):
    """Gene g makes protein g; proteins of genes with the same g % n_dis interact; gene g has disease g % n_dis and one
    random disease. Pair (g, g % n_dis) is reachable through a partner gene's protein once it is removed."""
    rng = np.random.default_rng(seed)
    g = np.arange(n_gene)
    gd = np.c_[np.c_[g, g % n_dis].T, np.c_[g, rng.integers(0, n_dis, n_gene)].T]
    pp = np.array([[a, b] for a in g for b in g if a < b and a % n_dis == b % n_dis]).T
    edges = {GD: gd, GP: np.c_[g, g].T, PP: pp}
    counts = {"Gene": n_gene, "Disease": n_dis, "Protein": n_gene}
    ids = {"Gene": [f"NCBIGene:{i}" for i in range(n_gene)], "Disease": [f"MONDO:{i:07d}" for i in range(n_dis)],
           "Protein": [f"UniProtKB:P{i:05d}" for i in range(n_gene)]}
    eids = {et: [f"{et[1]}:{i}" for i in range(ei.shape[1])] for et, ei in edges.items()}
    return edges, counts, ids, eids


def hold_out(edges, eids, pairs):
    """The graph without the task edges (gene, disease) in pairs, and its edge ids kept aligned."""
    gd = edges[GD]
    keep = np.array([(int(a), int(b)) not in pairs for a, b in gd.T])
    return {**edges, GD: gd[:, keep]}, {**eids, GD: [e for e, k in zip(eids[GD], keep) if k]}


@pytest.fixture(scope="module")
def mp_rows():
    edges, counts, ids, eids = planted()
    train, teids = hold_out(edges, eids, {(0, 0), (1, 1), (2, 2)})
    rows = predict.metapath_predictions(train, counts, GD, ids, teids, top=3, graph="GT", task="B", max_len=4, batch=16)
    return rows, train, counts, ids, teids


def test_metapath_rows_have_the_columns_and_known_pairs_are_never_predicted(mp_rows):
    rows, train, counts, ids, eids = mp_rows
    assert rows and list(rows[0]) == predict.METAPATH_COLUMNS
    known = {(ids["Gene"][a], ids["Disease"][b]) for a, b in train[GD].T}
    assert all((r["subject"], r["object"]) not in known for r in rows)
    assert {r["by"] for r in rows} == {"subject", "object"} and {r["model"] for r in rows} == {"metapath"}
    per_subject = {}
    for r in rows:
        if r["by"] == "subject":
            per_subject.setdefault(r["subject"], []).append(r["rank"])
    assert all(sorted(v) == list(range(1, len(v) + 1)) and len(v) <= 3 for v in per_subject.values())


def test_removed_true_links_are_recovered_with_a_named_path(mp_rows):
    rows, *_ = mp_rows
    got = {(r["subject"], r["object"]): r for r in rows if r["by"] == "subject" and r["rank"] <= 3}
    for g in (0, 1, 2):                    # the held-out pair (g, g % 20) is among the best three for its gene
        r = got[(f"NCBIGene:{g}", f"MONDO:{g:07d}")]
        paths = json.loads(r["paths"])
        assert paths and r["best_shape"] and r["n_shapes"] >= 1
        assert paths[0]["shape"] == r["best_shape"] or len(paths) > 1


def test_every_path_is_a_chain_of_real_graph_edges(mp_rows):
    rows, train, counts, ids, eids = mp_rows
    by_id = {}
    for et, ei in train.items():
        for (a, b), eid in zip(ei.T, eids[et]):
            by_id[eid] = (ids[et[0]][a], et[1], ids[et[2]][b])
    checked = 0
    for r in rows[:300]:
        for p in json.loads(r["paths"]):
            assert p["nodes"][0] == r["subject"] and p["nodes"][-1] == r["object"] and p["via"] == p["nodes"][1]
            assert len(p["edges"]) >= 2 and p["count"] >= 1
            at = p["nodes"][0]
            for e in p["edges"]:
                assert by_id[e["id"]] == (e["subject"], e["predicate"], e["object"])      # a real row of the graph
                assert at in (e["subject"], e["object"])                                  # consecutive edges touch
                at = e["object"] if at == e["subject"] else e["subject"]
            assert at == r["object"]
            checked += 1
    assert checked > 50


def test_the_bridge_is_expanded_into_gene_protein_edges(mp_rows):
    rows, *_ = mp_rows
    steps = [e for r in rows for p in json.loads(r["paths"]) for e in p["edges"]]
    assert any(e["predicate"] == "has_gene_product" for e in steps)        # a projected "@product" step shows its bridge edge
    assert any(e["predicate"] == "interacts_with" for e in steps)


def test_export_is_deterministic_and_writes_a_readable_gzip(mp_rows, tmp_path):
    rows, train, counts, ids, eids = mp_rows
    again = predict.metapath_predictions(train, counts, GD, ids, eids, top=3, graph="GT", task="B", max_len=4, batch=64)
    assert again == rows                                                    # batch size does not change the result
    p1 = predict.write_metapath_predictions(rows, tmp_path / "predictions_metapath.tsv.gz", {"rows": len(rows)})
    b1 = p1.read_bytes()
    assert predict.write_metapath_predictions(rows, tmp_path / "predictions_metapath.tsv.gz").read_bytes() == b1
    lines = gzip.decompress(b1).decode().splitlines()
    assert lines[0].split("\t") == predict.METAPATH_COLUMNS and len(lines) == len(rows) + 1
    assert json.loads((tmp_path / "predictions_metapath_settings.json").read_text()) == {"rows": len(rows)}


def test_no_shape_means_no_rows_and_unwritable_ids_are_skipped():
    one = {GD: np.array([[0], [0]])}
    assert predict.metapath_predictions(one, {"Gene": 2, "Disease": 2}, GD, {"Gene": ["g0", "g1"], "Disease": ["d0", "d1"]},
                                        {GD: ["e0"]}, top=2, max_len=1) == []


def test_metapath_flags_parse():
    a = predict.parse_args(["--model", "metapath", "--max-len", "4", "--max-shapes", "50", "--batch", "64"] + FROZEN_A)
    assert (a.model, a.max_len, a.max_shapes, a.batch) == ("metapath", 4, 50, 64)
    assert predict.parse_args([]).model == "gnn" and predict.parse_args(FROZEN_A).model == "gnn"


def test_paths_visit_no_gene_or_disease_twice(mp_rows):
    rows, *_ = mp_rows
    for r in rows:
        for p in json.loads(r["paths"]):
            ends = [n for n in p["nodes"] if not n.startswith("UniProtKB:")]     # a protein can be the hub of two steps
            assert len(set(ends)) == len(ends), p["nodes"]


# ---------------------------------------------------------------- CC-28: parallel explain, tops, curated objects
def test_two_workers_give_the_same_rows_as_one(mp_rows):
    rows, train, counts, ids, eids = mp_rows
    logs, stats = [], {}
    par = predict.metapath_predictions(train, counts, GD, ids, eids, top=3, graph="GT", task="B", max_len=4, batch=16,
                                       workers=2, chunk=7, log=logs.append, stats=stats)
    assert par == rows                                                      # identical rows, identical order
    assert stats["workers"] == 2 and stats["chunk"] == 7 and stats["ms_per_pair"] > 0
    assert stats["candidate_objects"] == counts["Disease"]
    prog = [m for m in logs if m.startswith("explained ")]
    assert len(prog) == -(-stats["pairs"] // 7) and prog[-1].startswith(f"explained {stats['pairs']:,} / {stats['pairs']:,}")


def test_progress_line_format():
    assert predict.progress_line(120_000, 1_300_000, 4920.0) == "explained 120,000 / 1,300,000 pairs, 41 ms/pair, ETA 13.4 h"   # > 2 h shown in hours
    assert re.fullmatch(r"explained 10 / 40 pairs, 500 ms/pair, ETA 0 min", predict.progress_line(10, 40, 5.0))
    assert predict.progress_line(1, 1_000_000, 1.0).endswith("ETA 277.8 h")
    assert predict.progress_line(5, 10, 1.0).endswith("ETA 0 min")


def test_top_subject_and_top_object_are_honoured():
    edges, counts, ids, eids = planted()
    rows = predict.metapath_predictions(edges, counts, GD, ids, eids, top=2, top_object=5, max_len=4, batch=16)
    per = {}
    for r in rows:
        per.setdefault((r["by"], r["subject"] if r["by"] == "subject" else r["object"]), []).append(r["rank"])
    assert max(len(v) for k, v in per.items() if k[0] == "subject") <= 2
    assert max(len(v) for k, v in per.items() if k[0] == "object") == 5
    a = predict.parse_args(["--model", "metapath"])
    assert predict.tops(a) == (10, 10) and a.objects == "curated" and a.workers >= 1
    assert predict.tops(predict.parse_args(["--top", "50"])) == (50, 50)
    assert predict.tops(predict.parse_args(["--top", "50", "--top-object", "3"])) == (50, 3)
    st = predict.settings(predict.parse_args(["--top-subject", "7"]))
    assert (st["top_subject"], st["top_object"], st["objects"]) == (7, 10, "curated")


def test_curated_objects_filter_and_count():
    ei = np.array([[0, 1, 2, 3], [0, 1, 1, 4]])
    levels = ["knowledge_assertion", "statistical_association", "knowledge_assertion", "text_co_occurrence"]
    assert predict.curated_objects(ei, levels).tolist() == [0, 1]              # 4 has only a text-mining edge
    assert predict.curated_objects(ei, None).tolist() == [0, 1, 4]              # no levels in the graph: every task object
    edges, counts, ids, eids = planted()
    stats = {}
    rows = predict.metapath_predictions(edges, counts, GD, ids, eids, top=3, max_len=4, batch=16, objects=[0, 1, 2],
                                        stats=stats)
    assert rows and {r["object"] for r in rows} <= {ids["Disease"][i] for i in (0, 1, 2)}
    assert stats["candidate_objects"] == 3


def test_spawn_fallback_gives_the_same_rows(mp_rows, monkeypatch):
    """Where fork is unavailable the workers get the graph through the pool initializer: same rows."""
    rows, train, counts, ids, eids = mp_rows
    stats = {}
    par = predict.metapath_predictions(train, counts, GD, ids, eids, top=3, graph="GT", task="B", max_len=4, batch=16,
                                       workers=2, chunk=50, log=lambda m: None, stats=stats, start_method="spawn")
    assert par == rows and stats["start_method"] == "spawn"
    monkeypatch.setattr(predict.multiprocessing, "get_all_start_methods", lambda: ["spawn"])
    stats = {}
    assert predict.metapath_predictions(train, counts, GD, ids, eids, top=3, graph="GT", task="B", max_len=4, batch=16,
                                        workers=2, chunk=50, log=lambda m: None, stats=stats) == rows
    assert stats["start_method"] == "spawn"                                 # auto falls back where fork is unavailable
    assert predict.parse_args([]).start_method == "auto"
