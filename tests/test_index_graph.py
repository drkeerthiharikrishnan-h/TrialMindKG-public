"""CC-19: explain / node / predict / demo / ask / subgraph answered from the card index, never from the live 26-million-edge graph.
Fixture graph (tests/fixtures/pharos_fixture.py); every index answer is compared with the live PharosGraph's."""
import json
import math
import sys

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "pipelines")
import app as appmod  # noqa: E402
import index_graph  # noqa: E402
import pharos_app  # noqa: E402
import subgraph_export  # noqa: E402
from nim import NIM  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402

from fixtures.pharos_fixture import build, write, GENE, PROT, DIS1, DIS2, CMP, BP  # noqa: E402


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def built(repo):
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    return CardIndex(stats["path"]), PharosGraph(root=repo)


# ------------------------------------------------------------------------------------------------ equal to the live graph
def test_explain_edge_equals_the_live_explanation_for_every_edge(built):
    idx, g = built
    ids = list(g.edges)
    assert len(ids) == 10
    for eid in ids:
        assert idx.explain_edge(eid) == g.explain_edge(eid), eid
    assert idx.explain_edge("e-nope") is None and g.explain_edge("e-nope") is None


def test_explanation_has_the_corroboration_and_the_timeline_of_the_whole_pair(built):
    idx, _ = built
    x = idx.explain_edge(f"e{1:016x}")                         # PTGS2 - Alzheimer, DisGeNET: the same pair is in 4 rows
    assert len(x["corroboration"]) == 3 and {c["layer"] for c in x["corroboration"]} == {"pharos", "opentargets_layer"}
    assert len(x["timeline"]["events"]) >= 2 and x["verdict"]["verdict"] == "keep"
    pruned = idx.explain_edge(f"e{3:016x}")
    assert pruned["verdict"]["verdict"] == "prune" and "text mining" in pruned["verdict"]["reasons"]


def test_node_view_equals_the_live_node_view(built):
    idx, g = built
    for nid in g.nodes:
        assert idx.node_view(nid) == g.node_view(nid), nid
    assert idx.node_view("nope") is None


@pytest.mark.parametrize("n", [1, 5, 20])
@pytest.mark.parametrize("task", [None, "A", "B", "Z"])
def test_predicted_links_equal_the_live_ones_with_and_without_a_task(built, n, task):
    idx, g = built
    for nid in list(g.nodes) + ["nope"]:
        assert idx.predicted_links(nid, n=n, task=task) == g.predicted_links(nid, n=n, task=task), (nid, n, task)


def test_a_task_filter_is_exact_even_when_another_task_fills_the_top(tmp_path):
    root = build(tmp_path / "r")
    rows = [dict(graph="G3", task="B", subject=GENE, object=f"MONDO:{i:07d}", score=str(0.99 - i / 1000), rank=str(i)) for i in range(1, 26)]
    rows += [dict(graph="G3", task="A", subject=GENE, object=f"CHEMBL.COMPOUND:CHEMBL{i}", score=str(0.2 - i / 1000), rank=str(i)) for i in range(1, 6)]
    rows.append(dict(graph="G2", task="A", subject=GENE, object="MONDO:0000001", score="0.01", rank="9"))      # same pair as a task-B row
    write(root / "outputs" / "linkpred", "predictions", pd.DataFrame(rows))
    stats = build_index(root, n_pred=3, chunk=4, log=lambda *_: None)
    idx, g = CardIndex(stats["path"]), PharosGraph(root=root)
    for task in (None, "A", "B"):
        for n in (1, 3):
            a, b = idx.predicted_links(GENE, n=n, task=task), g.predicted_links(GENE, n=n, task=task)
            assert a == b and len(a["predictions"]) == n, (task, n)
    assert [p["task"] for p in idx.predicted_links(GENE, n=3, task="A")["predictions"]] == ["A"] * 3     # task A, though B owns the top
    assert idx.card(GENE, n_pred=3)["predicted"] == g.card(GENE, n_pred=3)["predicted"]
    assert idx.predicted_links(GENE, n=3, task="A")["predictions"][0]["score"] == 0.199


def test_tested_questions_equal_the_live_answers(built):
    idx, g = built
    assert idx.demo() == g.demo()
    assert idx.demo(ids={"pharos_demo_3"}) == g.demo(ids={"pharos_demo_3"})
    assert [d["passed"] for d in idx.demo()] == [True] * 5


def test_tested_questions_are_ordered_by_evidence_with_the_same_ids(built):
    """CC-31a, owner decision 1 (8 Nov): the five demo queries order evidence first; the ids stay. Demo 1 sorts on the
    knowledge level in the Cypher itself, and every stored answer is ordered by querier.evidence_key with one row per answer."""
    idx, g = built
    assert [d["id"] for d in pharos_app.DEMO_QUESTIONS] == [f"pharos_demo_{i}" for i in range(1, 6)]
    q = {d["id"]: d["cypher"] for d in pharos_app.DEMO_QUESTIONS}
    assert "ORDER BY knowledge_level, disease" in q["pharos_demo_1"]
    assert "ORDER BY alzheimer_level, diabetes_level, gene" in q["pharos_demo_3"]
    d1 = idx.demo(ids={"pharos_demo_1"})[0]
    # PTGS2 - Alzheimer is stated by DisGeNET, text mining and Open Targets (3 sources): merged into one row, first
    assert [r["disease"] for r in d1["rows"]] == ["Alzheimer disease", "type 2 diabetes mellitus"]
    assert d1["rows"][0]["evidence"] == [f"e{1:016x}", f"e{3:016x}", f"e{0x65:016x}"]
    assert d1["ordering"]["by"] == "evidence" and d1["ordering"]["collapsed"] == 2 and d1["ordering"]["n_more"] == 0
    assert all(d["ordering"]["by"] == "evidence" for d in idx.demo())


def test_refresh_demo_rewrites_only_the_stored_answers(repo, tmp_path):
    import sqlite3
    import pharos_index
    src = build_index(repo, out=tmp_path / "ci.sqlite", chunk=3, log=lambda *_: None)["path"]
    con = sqlite3.connect(src)
    con.execute("UPDATE demo SET result = ? WHERE id = 'pharos_demo_1'", (json.dumps(dict(rows=[], columns=[], evidence=[],
                                                                                       passed=False, note="old")),))
    con.commit()
    n_edges = con.execute("SELECT count(*) FROM edges").fetchone()[0]
    con.close()
    assert CardIndex(src).demo(ids={"pharos_demo_1"})[0]["note"] == "old"
    assert pharos_index.refresh_demo(src) == {f"pharos_demo_{i}": True for i in range(1, 6)}
    fresh = CardIndex(src)
    assert fresh.demo(ids={"pharos_demo_1"})[0]["ordering"]["by"] == "evidence"
    assert fresh.con.execute("SELECT count(*) FROM edges").fetchone()[0] == n_edges


def hosted(cypher, tmp_path, tag):
    """A 'hosted' NIM whose endpoint answers every request with this openCypher (the offline stand-in writes no Pharos queries)."""
    from snapshot import Snapshot
    snap = Snapshot(tmp_path / f"llm_{tag}", mode="live", sleep=0)
    snap._http = lambda m, u, p, b, headers=None, store_client_errors=True, **kw: {
        "choices": [{"message": {"content": json.dumps({"cypher": cypher, "explanation": "stub"})}}], "usage": {}}
    n = NIM(backend="nim", snap=snap)
    n.key = "nvapi-stub"
    return n


FREE = ("MATCH (g:Gene {name:'SOD1'})-[r:genetically_associated_with]->(t) RETURN t.name AS trait, r.id AS evidence "
        "ORDER BY trait LIMIT 5")


def test_ask_from_the_index_equals_ask_on_the_live_graph(built, tmp_path):
    idx, g = built
    cases = [(d["question"], d["cypher"]) for d in pharos_app.DEMO_QUESTIONS]
    cases += [("What does SOD1 have to do with GWAS traits?", FREE),
              ("Which compounds are linked to ibuprofen's protein?",
               "MATCH (c:SmallMolecule {id:'CHEMBL.COMPOUND:CHEMBL521'})-[r:directly_physically_interacts_with]->(p:Protein) "
               "RETURN c.name AS compound, p.name AS protein, r.id AS evidence ORDER BY protein LIMIT 5")]
    for i, (q, cy) in enumerate(cases):
        nim = hosted(cy, tmp_path, i)
        live, fast = pharos_app.ask(g, nim, q), pharos_app.ask(idx.graph(), nim, q)
        assert live["rows"] and live["evidence"], q                                   # the comparison is not vacuous
        assert {k: fast[k] for k in ("cypher", "columns", "rows", "evidence")} == {k: live[k] for k in ("cypher", "columns", "rows", "evidence")}, q
        assert fast.get("entities") == live.get("entities") and fast.get("backend") == live.get("backend"), q


def test_ask_validation_uses_the_index_schema(built, tmp_path):
    idx, g = built
    bad = "MATCH (g:Gene)-[r:made_up_type]->(d:Disease) RETURN d.id AS d, r.id AS evidence LIMIT 5"
    res = pharos_app.ask(idx.graph(), hosted(bad, tmp_path, "bad"), "anything about PTGS2")
    live = pharos_app.ask(g, hosted(bad, tmp_path, "bad2"), "anything about PTGS2")
    assert res["rows"] == live["rows"] == [] and res.get("problems") == live.get("problems")


def test_entities_and_the_schema_card_match_the_live_graph(built):
    idx, g = built
    ig = idx.graph()
    for q in ("Which genes are associated with both Alzheimer disease and type 2 diabetes mellitus?", "What does ibuprofen bind?",
              "PTGS2 and SOD1 and MONDO:0004975 and CHEMBL521", "nothing here"):
        assert ig.find_entities(q) == g.find_entities(q), q
    live, fast = g.schema_card(), ig.schema_card()
    assert fast["labels"] == live["labels"] and fast["patterns"] == live["patterns"] and fast["rel_props"] == live["rel_props"]


def test_subgraph_collect_equals_the_live_one_and_unknown_ids_are_skipped(built):
    idx, g = built
    ids = [f"e{i:016x}" for i in (1, 3, 5, 7)] + ["e-nope", f"e{1:016x}"]
    assert idx.subgraph(ids) == subgraph_export.collect(g, ids)
    nodes, edges = idx.subgraph(ids)
    assert len(edges) == 4 and {e["id"] for e in edges} == {f"e{i:016x}" for i in (1, 3, 5, 7)}
    assert subgraph_export.zip_bytes(nodes, edges, "t")                                            # the zip builds from index data


def test_the_edges_table_holds_the_card_rows_the_pairs_and_the_tested_questions(tmp_path):
    root = build(tmp_path / "r")
    stats = build_index(root, per_kind=1, chunk=3, log=lambda *_: None)                           # a card keeps one row per kind
    idx, g = CardIndex(stats["path"]), PharosGraph(root=root)
    n_rows = idx.con.execute("SELECT count(DISTINCT id) FROM rows").fetchone()[0]
    stored = {r[0] for r in idx.con.execute("SELECT id FROM edges")}
    assert {r[0] for r in idx.con.execute("SELECT id FROM rows")} <= stored and int(idx.status()["edges_stored"]) == len(stored)
    for eid in stored:                                                                              # every stored edge explains like the live one
        assert idx.explain_edge(eid) == g.explain_edge(eid), eid
    assert n_rows <= len(stored) <= len(g.edges)
    pair = [e for e in g.edges if g.edges[e]["subject"] == GENE and g.edges[e]["object"] == DIS1]
    assert set(pair) <= stored                                                                      # the same-pair siblings came with them


# ------------------------------------------------------------------------------------------------ the app
@pytest.fixture
def served(repo, tmp_path, monkeypatch):
    """The app with the card index and the live-graph loader patched to FAIL: any endpoint that falls back to the 26M-edge graph errors."""
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    monkeypatch.delenv("TMKG_PHAROS_INDEX", raising=False)
    monkeypatch.delenv("NEPTUNE_ENDPOINT", raising=False)
    appmod._pharos.clear()
    appmod._state.update(kg=None, nim=NIM(backend="offline"))

    def boom(*a, **k):
        raise AssertionError("the live graph must not be loaded when a card index exists")
    monkeypatch.setattr(appmod, "pharos", boom)
    monkeypatch.setattr(pharos_app, "PharosGraph", boom)
    yield TestClient(appmod.app, raise_server_exceptions=True), CardIndex(stats["path"])
    appmod._pharos.clear()


def test_every_page_endpoint_answers_with_the_live_graph_disabled(served):
    c, idx = served
    eid = f"e{1:016x}"
    assert c.get("/api/pharos/status").status_code == 200
    assert c.get("/api/pharos/search", params={"q": "ptgs2"}).json()
    assert c.get(f"/api/pharos/card/{GENE}").status_code == 200
    n = c.get(f"/api/pharos/node/{GENE}")
    assert n.status_code == 200 and n.json()["label"] == "PTGS2"
    x = c.get(f"/api/pharos/explain/{eid}")
    assert x.status_code == 200 and x.json()["verdict"]["verdict"] == "keep" and x.json()["corroboration"]
    assert c.get("/api/pharos/predict", params={"node": GENE, "n": 5}).json()["predictions"]
    assert c.get("/api/pharos/predict", params={"node": GENE, "task": "B"}).status_code == 200
    d = c.get("/api/pharos/demo").json()
    assert [q["passed"] for q in d] == [True] * 5 and c.get("/api/pharos/demo/pharos_demo_1").json()["rows"]
    assert c.get("/api/pharos/demo_questions").status_code == 200
    appmod._state["nim"] = hosted(pharos_app.DEMO_QUESTIONS[0]["cypher"], idx.path.parent, "app")
    r = c.post("/api/pharos/ask", json={"question": pharos_app.DEMO_QUESTIONS[0]["question"], "backend": "local"})
    assert r.status_code == 200 and r.json()["rows"] and r.json()["from_index"] is True
    z = c.post("/api/pharos/subgraph.zip", json={"edges": r.json()["evidence"], "title": "t"})
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip" and len(z.content) > 100


def test_page_answers_equal_the_index_answers(served, built):
    c, idx = served
    fast = c.get(f"/api/pharos/explain/{f'e{2:016x}'}").json()
    assert fast == json.loads(json.dumps(built[1].explain_edge(f"e{2:016x}")))
    assert c.get("/api/pharos/demo").json() == json.loads(json.dumps(built[1].demo()))


def test_an_unknown_edge_or_node_is_a_quick_404_with_a_plain_message(served):
    c, _ = served
    r = c.get("/api/pharos/explain/e-not-an-edge")
    assert r.status_code == 404 and "card index" in r.json()["detail"] and "e-not-an-edge" in r.json()["detail"]
    assert c.get("/api/pharos/node/nope").status_code == 404 and c.get("/api/pharos/predict", params={"node": "nope"}).status_code == 404
    assert c.post("/api/pharos/subgraph.zip", json={"edges": ["e-nope"]}).status_code == 404
    assert c.post("/api/pharos/subgraph.zip", json={"edges": []}).status_code == 400


def test_nan_and_inf_become_null_not_a_500(served, monkeypatch):
    assert appmod.nan_safe({"a": float("nan"), "b": [1.5, float("inf"), {"c": float("-inf")}], "d": "x", "e": (float("nan"),)}) == \
        {"a": None, "b": [1.5, None, {"c": None}], "d": "x", "e": [None]}
    c, idx = served
    monkeypatch.setattr(index_graph.IndexGraph, "explain_edge", lambda self, eid: {"id": eid, "verdict": {"confidence": float("nan")}, "score": float("inf")})
    r = c.get("/api/pharos/explain/e1")
    assert r.status_code == 200 and r.json() == {"id": "e1", "verdict": {"confidence": None}, "score": None}


def test_a_nan_confidence_in_the_data_does_not_break_the_node_view(tmp_path, monkeypatch):
    root = build(tmp_path / "r")
    v = pd.read_csv(root / "data" / "kg" / "pharos" / "edge_verdicts.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    v.loc[0, "confidence"] = "nan"
    write(root / "data" / "kg" / "pharos", "edge_verdicts", v)
    stats = build_index(root, chunk=3, log=lambda *_: None)
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(root))
    appmod._pharos.clear()
    monkeypatch.setattr(appmod, "pharos", lambda: (_ for _ in ()).throw(AssertionError("live graph")))
    c = TestClient(appmod.app)
    r = c.get(f"/api/pharos/node/{GENE}")
    assert r.status_code == 200
    assert any(e["confidence"] is None for rows in r.json()["edges_by_predicate"].values() for e in rows)
    appmod._pharos.clear()


def test_without_an_index_the_live_graph_still_answers_and_off_turns_the_index_off(repo, monkeypatch):
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    monkeypatch.delenv("NEPTUNE_ENDPOINT", raising=False)
    monkeypatch.setenv("TMKG_PHAROS_INDEX", "off")
    appmod._pharos.clear()
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    c = TestClient(appmod.app)
    assert c.get(f"/api/pharos/explain/{f'e{1:016x}'}").status_code == 200 and "g" in appmod._pharos          # the live graph was loaded
    assert c.get("/api/pharos/status").status_code == 200
    appmod._pharos.clear()


def test_the_candidates_hook_replaces_the_node_scan(built):
    idx, g = built
    ig = idx.graph()
    assert ig.candidates("Gene", {"name": "PTGS2"}, lambda x: True) == [GENE]
    assert ig.candidates(None, {"symbol": "PTGS2"}, lambda x: True) == [PROT]
    assert ig.candidates("Disease", {}, lambda x: x == DIS1) == [DIS1]
    import querier
    cy = "MATCH (g:Gene {name:'PTGS2'})-[r:gene_associated_with_condition]->(d:Disease) RETURN d.name AS disease, r.id AS evidence ORDER BY disease LIMIT 50"
    assert querier.run_local(cy, ig) == querier.run_local(cy, g)
