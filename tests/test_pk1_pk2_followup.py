"""PK1 / PK2 follow-ups from the S03 verification (outputs/review/S03_verify_2026-11-12.md, section 5).
No network, no model: the Pharos side runs on the fixture graph and its card index."""
import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import answer_summary as S  # noqa: E402
import app as appmod  # noqa: E402
import pharos_app as P  # noqa: E402
import routes_answers as R  # noqa: E402
import stored_answers as sa  # noqa: E402
from nim import NIM  # noqa: E402
from pharos_index import build_index  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402

INTERNAL = re.compile(r"\bG[0-4]\b|\bLIMIT\b|curated:|\.md\b|\.json\b|\.csv\b|\bPK\d|\bCC-\d|\b[OBE]G\d{3}\b|outputs/|pipelines/"
                      r"|_layer\b|chembl37|_hierarchy\b")


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def index_file(repo):
    return build_index(repo, chunk=3, log=lambda *_: None)["path"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for var in ("TMKG_ASK_LIMIT_PER_HOUR", "TMKG_ASK_LIMIT_PER_DAY", "TMKG_PHAROS_INDEX", "NEPTUNE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    saved = dict(appmod._pharos)
    appmod._pharos.clear()
    yield
    appmod._pharos.clear()
    appmod._pharos.update(saved)


# ---------------------------------------------------------------- PK1
def test_pk1_eight_first_calls_of_pharos_build_the_graph_once(repo, monkeypatch):
    built = []

    class SlowGraph:
        def __init__(self, root=None):
            built.append(root)
            time.sleep(0.05)
    monkeypatch.setattr(P, "PharosGraph", SlowGraph)
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    got = []
    threads = [threading.Thread(target=lambda: got.append(appmod.pharos())) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(built) == 1 and len(got) == 8 and len({id(g) for g in got}) == 1


def test_pk1_warm_does_not_load_the_live_pharos_graph(index_file, monkeypatch):
    monkeypatch.setenv("TMKG_PHAROS_INDEX", str(index_file))
    monkeypatch.setattr(appmod, "state", lambda: None)
    assert appmod.warm() == ["state", "card_index"] and "g" not in appmod._pharos
    assert "NOT loaded here" in appmod.warm.__doc__


def test_pk1_pypdf_is_past_the_advisory():
    for name in ("requirements.txt", "requirements.lock.txt"):
        v = re.search(r"^pypdf==([\d.]+)$", (ROOT / name).read_text(), re.M).group(1)
        assert tuple(int(x) for x in v.split(".")) >= (6, 21, 4), name


def test_pk1_openapi_json_is_off_but_the_description_still_builds():
    c = TestClient(appmod.app)
    assert c.get("/openapi.json").status_code == 404 and "/api/graph_numbers" in appmod.app.openapi()["paths"]


def test_pk1_a_query_answer_under_a_failed_model_is_not_summarised_as_no_rows(monkeypatch):
    monkeypatch.setattr(appmod, "state", lambda: (None, None))
    monkeypatch.setattr(appmod, "graph_summary_safe", lambda res, kg: dict(
        headline="The graph returned no rows.", confidence=dict(level="low", reason="no rows"), next_step="x",
        what_would_change=[], points=[]))
    failed = dict(question="q", rows=[], columns=[], no_query=True, fallback_from="nim", backend="offline")
    out = TestClient(appmod.app).post("/api/summarize", json=dict(kind="query", result=failed)).json()
    assert out["model_unavailable"] is True and "did not answer" in out["headline"] and "no rows" not in out["headline"]
    plain = dict(question="q", rows=[], columns=[], backend="offline")
    out = TestClient(appmod.app).post("/api/summarize", json=dict(kind="query", result=plain)).json()
    assert "model_unavailable" not in out and out["headline"] == "The graph returned no rows."


# ---------------------------------------------------------------- PK2 items 1 and 2
def test_pk2_graph_numbers_hold_no_file_names_and_no_layer_codes():
    n = R.graph_numbers()
    assert "files" not in n and n["added_layers"] and "Open Targets" in n["added_layers"] and "ChEMBL" in n["added_layers"]
    text = json.dumps(n)
    assert not INTERNAL.search(text), INTERNAL.search(text).group(0)


def test_pk2_graph_numbers_on_the_server_read_the_shipped_totals(tmp_path):
    """The image has outputs/ but not outputs/g2 or outputs/g3: the totals come from outputs/graph_totals.json."""
    (tmp_path / "outputs" / "pharos").mkdir(parents=True)
    (tmp_path / "outputs" / "pharos" / "prune_report.md").write_text((ROOT / "outputs/pharos/prune_report.md").read_text())
    (tmp_path / "outputs" / "graph_totals.json").write_text((ROOT / "outputs/graph_totals.json").read_text())
    here, there = R.graph_numbers(ROOT), R.graph_numbers(tmp_path)
    assert there["all_layers"] == here["all_layers"] and there["added_layers"] == here["added_layers"]
    assert there["sentence"] == here["sentence"] and there["added_layers"]


def test_pk2_the_totals_file_agrees_with_the_summaries(tmp_path):
    import headline
    t = json.loads((ROOT / "outputs/graph_totals.json").read_text())
    p = headline.prune_numbers(ROOT)
    assert (t["g2_edges"], t["g3_edges"], t["g3_removed"]) == (p["g2_edges"], p["g3_edges"], p["g3_removed"])
    for g in ("g2", "g3"):
        (tmp_path / "outputs" / g).mkdir(parents=True)
        (tmp_path / "outputs" / g / "summary.md").write_text((ROOT / "outputs" / g / "summary.md").read_text())
    (tmp_path / "outputs" / "pharos").mkdir()
    assert R.write_graph_totals(tmp_path) == t | dict(source=t["source"])


# ---------------------------------------------------------------- PK2 item 3
def test_pk2_a_claim_that_only_restates_the_failure_is_not_the_diagnosis():
    assert S.restates_failure("The curated record lists the semaglutide EVOKE/EVOKE+ program as failed.")
    assert not S.restates_failure("dal-GenE missed its primary endpoint: HR 0.88 (95% CI 0.75-1.03), p = 0.12.")
    sec = dict(role="failure_diagnostician", title="Why it failed")
    only = [("1.1", sec, dict(claim="The curated record lists the program as failed.", support_status="supported"))]
    assert S._diagnosis_claim(only) == (None, None, False)
    both = only + [("1.2", sec, dict(claim="The primary endpoint was missed, p=0.12.", support_status="supported"))]
    assert S._diagnosis_claim(both)[0] == "1.2"


def test_pk2_the_stored_semaglutide_answer_shows_the_stated_failure_alone():
    for meta in sa.index():
        stored = sa.load(sa.FOLDER, meta["id"])
        a = stored["answer"]
        if "sections" in a and a.get("summary"):
            if any(S.restates_failure(c.get("claim")) for _, _, c in S._claims(a)):
                head = S.trial_summary(a)["headline"]
                assert "Stated failure" in head and "lists the" not in head


# ---------------------------------------------------------------- PK2 item 4
def test_pk2_the_model_path_has_no_caveat_for_a_name_the_query_resolved(repo):
    graph = P.PharosGraph(root=repo)
    d = P.DEMO_QUESTIONS[2]                                   # a question whose names the query resolves
    question = d["question"].replace("both ", "")             # typed differently: not a tested question any more
    assert P.tested_question(question) is None
    reply = dict(cypher=d["cypher"], backend="offline")

    class FakeQuerier:
        def __init__(self, *a, **k):
            pass

        def ask(self, q, entities=None):
            return dict(question=q, cypher=d["cypher"], columns=[], rows=[], evidence=[], problems=[], backend="offline")
    import querier
    orig = querier.Querier
    querier.Querier = FakeQuerier
    try:
        got = P.ask(graph, NIM(backend="offline"), question)
    finally:
        querier.Querier = orig
    ids, _ = P.query_anchors(d["cypher"])
    labels = {str(graph.label(i)).lower() for i in ids}
    assert not any(n["phrase"].lower() in labels for n in got["entity_notes"])
    assert not any(f"'{lab}'" in s and "was read as" in s for s in got["entity_sentences"] for lab in labels)


def test_pk2_the_tested_answers_on_the_card_index_graph(repo, index_file, monkeypatch):
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    c = TestClient(appmod.app)
    for d in P.DEMO_QUESTIONS:
        r = c.get(f"/api/pharos/demo/{d['id']}")
        assert r.status_code == 200, d["id"]
        a = r.json()
        assert a["cypher"] == d["cypher"] and a["summary"]["headline"]
        text = S.summary_text(a["summary"]) if "points" in a["summary"] else ""
        assert not INTERNAL.search(text), d["id"]
        asked = c.post("/api/pharos/ask", json={"question": d["question"]}).json()
        assert asked["rows"] == a["rows"] and asked["entity_sentences"] == []


def test_pk2_ac06_the_served_tested_answers_say_how_many_links_were_pruned(repo, index_file, monkeypatch):
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    c = TestClient(appmod.app)
    for d in P.DEMO_QUESTIONS:
        s = c.get(f"/api/pharos/demo/{d['id']}").json()["summary"]
        pts = " ".join(p["text"] for p in s.get("points") or [])
        if s.get("counts", {}).get("rows"):
            assert re.search(r"pruned|pruning", pts), d["id"]


# ---------------------------------------------------------------- PK2 item 5
def test_pk2_a_live_trial_query_answer_shows_program_names_not_ids():
    facts = S.program_facts()
    pid = next(k for k, v in facts.items() if v.get("name"))
    cy = "MATCH (p:ClinicalTrialProgram) RETURN p.id AS id, p.name AS name"
    res = dict(question="q", cypher=cy, rows=[dict(id=pid, name="x")], evidence=["e1"], columns=["id", "name"])
    out = appmod.query_display(res)
    assert out["rows"][0]["id"] == facts[pid]["name"] and out["rows"][0]["name"] == "x"
    assert out["evidence"] == ["e1"] and out["cypher"] == cy and res["rows"][0]["id"] == pid     # input not changed
    assert appmod.query_display("not a dict") == "not a dict" and appmod.query_display(dict(rows=None)) == dict(rows=None)


def test_pk2_the_neptune_exception_is_recorded():
    assert "the model path is kept" in P.ask.__doc__ and "Neptune" in P.ask.__doc__
