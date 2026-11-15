"""CC-38: optional demo_overlay.json and value_sets.json in the data root (TMKG_PHAROS_ROOT) refresh the tested questions'
answers and the validator's value sets on the hosted app without re-uploading the card index
(pharos_index.write_overlay writes them; index_graph.load_overlay reads them, re-read when they change)."""
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "pipelines")
import app as appmod  # noqa: E402
import index_graph  # noqa: E402
import pharos_app  # noqa: E402
from pharos_index import CardIndex, build_index, write_overlay  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("ov"))
    return root, build_index(root, chunk=3, log=lambda *_: None)["path"]


def test_write_overlay_writes_both_files_with_the_current_cypher(built, tmp_path):
    root, path = built
    sizes = write_overlay(path, tmp_path, root, chunk=3)
    assert set(sizes) == {"demo_overlay.json", "value_sets.json"} and all(n > 0 for n in sizes.values())
    d = json.loads((tmp_path / "demo_overlay.json").read_text())
    assert {k: v["cypher"] for k, v in d["results"].items()} == {q["id"]: q["cypher"] for q in pharos_app.DEMO_QUESTIONS}
    assert json.loads((tmp_path / "value_sets.json").read_text())["value_sets"] == CardIndex(path).graph().schema_card()["rel_enums"]


def test_overlay_answers_win_only_for_the_current_cypher(built, tmp_path):
    _, path = built
    ci = CardIndex(path)
    q1, q2 = pharos_app.DEMO_QUESTIONS[0], pharos_app.DEMO_QUESTIONS[1]
    fake = dict(rows=[{"disease": "from the overlay"}], columns=["disease"], evidence=[], passed=True, note="")
    (tmp_path / "demo_overlay.json").write_text(json.dumps({"made_at": "t", "results": {
        q1["id"]: {"cypher": q1["cypher"], "result": fake},
        q2["id"]: {"cypher": "MATCH (old query)", "result": fake}}}))
    g = ci.graph(tmp_path)
    got = {d["id"]: d for d in g.demo()}
    assert got[q1["id"]]["rows"] == fake["rows"]                                   # refreshed
    assert got[q2["id"]]["rows"] == ci.graph().demo(ids={q2["id"]})[0]["rows"]     # stale cypher: the stored answer stays
    assert ci.graph() is not g and ci.graph(tmp_path) is g


def test_value_sets_overlay_replaces_the_index_meta_and_is_reread(built, tmp_path):
    _, path = built
    g = CardIndex(path).graph(tmp_path)
    assert g.schema_card()["rel_enums"] == CardIndex(path).graph().schema_card()["rel_enums"]     # no file: the meta
    f = tmp_path / "value_sets.json"
    f.write_text(json.dumps({"made_at": "t", "value_sets": {"source": ["CTD"]}}))
    assert g.schema_card()["rel_enums"] == {"source": ["CTD"]}
    time.sleep(0.01)
    f.write_text(json.dumps({"made_at": "t2", "value_sets": {"source": ["CTD", "DisGeNET"]}}))
    assert g.schema_card()["rel_enums"] == {"source": ["CTD", "DisGeNET"]}
    f.write_text("not json")
    assert index_graph.load_overlay(tmp_path)["value_sets"] is None


def test_the_app_reads_the_overlay_from_the_data_root(built, tmp_path, monkeypatch):
    root, path = built
    q1 = pharos_app.DEMO_QUESTIONS[0]
    fake = dict(rows=[{"disease": "overlay row"}], columns=["disease"], evidence=[], passed=True, note="")
    (root / "demo_overlay.json").write_text(json.dumps({"made_at": "t", "results": {q1["id"]: {"cypher": q1["cypher"], "result": fake}}}))
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(root))
    monkeypatch.setenv("TMKG_PHAROS_INDEX", str(path))
    appmod._pharos.clear()
    try:
        r = TestClient(appmod.app).get(f"/api/pharos/demo/{q1['id']}").json()
        assert r["rows"] == fake["rows"]
    finally:
        (root / "demo_overlay.json").unlink()
        appmod._pharos.clear()
