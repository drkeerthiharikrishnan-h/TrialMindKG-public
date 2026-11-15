"""CC-6 usability pieces on fixtures (no network): the three landing chips, the model and answer labels (offline stand-in,
stored answer, fallback), the Generalization payload read from repo files, the local/Neptune backend switch (stubbed endpoint:
same rows and same evidence ids as the local run), the card timing field, and the page markup the new screens need."""
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "pipelines")
import app as appmod  # noqa: E402
import neptune_backend as nb  # noqa: E402
import personas  # noqa: E402
import querier  # noqa: E402
import usability as us  # noqa: E402
from nim import NIM  # noqa: E402
import pharos_app  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from snapshot import Snapshot  # noqa: E402

from fixtures.pharos_fixture import build, GENE, PROT  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


QUESTION = pharos_app.DEMO_QUESTIONS[0]["question"]


@pytest.fixture
def hosted_stub(client, tmp_path):
    """The app's NIM replaced by a 'hosted' one whose endpoint answers the first demo question with its pinned query."""
    snap = Snapshot(tmp_path / "llm", mode="live", sleep=0)
    cy = pharos_app.DEMO_QUESTIONS[0]["cypher"]
    snap._http = lambda m, u, p, b, headers=None, store_client_errors=True: {
        "choices": [{"message": {"content": json.dumps({"cypher": cy, "explanation": "stub"})}}], "usage": {}}
    n = NIM(backend="nim", snap=snap)
    n.key = "nvapi-stub"
    appmod._state["nim"] = n
    return n


@pytest.fixture
def client(repo, monkeypatch):
    g = PharosGraph(root=repo)
    appmod._pharos.update(g=g, index=None)
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    monkeypatch.delenv("NEPTUNE_ENDPOINT", raising=False)
    monkeypatch.delenv("TMKG_GRAPH_BACKEND", raising=False)
    return TestClient(appmod.app)


# ---- landing: one search box + 3 example chips
def test_exactly_three_chips_one_per_kind_taken_from_the_role_pages():
    chips = us.landing_chips(personas.load_personas())
    assert [c["kind"] for c in chips] == ["card", "pharos", "trials"] and len({c["text"] for c in chips}) == 3
    every_question = {(q["tab"], q["text"]) for p in personas.load_personas() for q in p["questions"]}
    assert all((c["kind"], c["text"]) in every_question for c in chips)         # nothing invented for the landing page
    assert us.landing_chips([]) == [] and len(us.landing_chips([{"id": "x", "questions": [{"tab": "card", "text": "A"}]}])) == 1


def test_landing_endpoint_and_page_markup(client):
    j = client.get("/api/landing").json()
    assert len(j["chips"]) == 3 and j["model"]["offline"] is True and j["backend_default"] == "local"
    page = client.get("/").text
    for needle in ('id="hf"', 'id="hq"', 'id="hchips"', 'id="tabGeneralize"', 'id="generalize"', '<!-- PK15 Neptune -->',
                   'id="modelbadge"', "Ask the graph", "Or start as a role"):
        assert needle in page, needle
    assert page.index('id="hf"') < page.index('id="personaCards"')              # the search box comes before the roles
    assert '<div class="personas" id="personaCards"></div>' in page             # roles are drawn by script, below the search


def test_every_chip_leads_to_an_answer_on_the_fixture(client, hosted_stub):
    """The card chip's text is searched and opened; the pharos chip is asked; both work with no role chosen on the server."""
    hits = client.get("/api/pharos/search", params={"q": "PTGS2"}).json()
    assert hits and client.get(f"/api/pharos/card/{hits[0]['id']}").status_code == 200
    r = client.post("/api/pharos/ask", json={"question": QUESTION})
    assert r.status_code == 200 and r.json()["rows"] and r.json()["evidence"]


# ---- the card in under 2 seconds, and says how long it took
def test_card_reports_its_time_and_is_fast(client):
    c = client.get(f"/api/pharos/card/{GENE}").json()
    assert 0 <= c["elapsed_ms"] < 2000


def test_card_from_the_index_is_fast_and_marked(repo, client):
    import pharos_index
    stats = pharos_index.build_index(repo, log=lambda *_: None)
    appmod._pharos["index"] = pharos_index.CardIndex(stats["path"])
    try:
        c = client.get(f"/api/pharos/card/{GENE}").json()
        assert c["served_from"] == "card index" and c["elapsed_ms"] < 2000
    finally:
        appmod._pharos["index"] = None


# ---- labels: a stand-in answer is never mistaken for the hosted model
def test_model_label_for_both_backends():
    off = us.model_label(NIM(backend="offline"))
    assert off["offline"] and "Offline stand-in" in off["label"] and "NVIDIA_API_KEY" in off["note"]
    on = us.model_label(NIM(backend="nim"))
    assert not on["offline"] and "Nemotron" in on["label"]


@pytest.mark.parametrize("res,kind,offline", [
    ({"backend": "nim"}, "hosted", False),
    ({"backend": "offline-stand-in"}, "offline", True),
    ({"backend": "offline"}, "offline", True),
    ({"backend": "nim", "cached": True}, "cached", False),
    ({"backend": "offline-stand-in", "fallback_from": "nim"}, "fallback", True),
    ({}, "hosted", False)])
def test_answer_label_kinds(res, kind, offline):
    lab = us.answer_label(res)
    assert lab["kind"] == kind and lab["offline"] is offline and bool(lab["text"]) == (kind != "hosted")
    if kind == "fallback":
        assert "hosted model could not be reached" in lab["text"]


def test_answers_carry_the_label_and_the_page_shows_it(client, hosted_stub):
    r = client.post("/api/pharos/ask", json={"question": QUESTION}).json()
    assert r["label"]["kind"] == "hosted" and r["graph_backend"] == "local" and r["rows"]
    appmod._state["nim"] = NIM(backend="offline")
    off = client.post("/api/pharos/ask", json={"question": "Which diseases is PTGS2 associated with?"}).json()
    assert off["label"]["kind"] == "offline" and off["label"]["offline"]            # the stand-in is named, even with no rows
    page = client.get("/").text
    assert "answerLabel(r.label)" in page and "Not the hosted model" in page and "Stored answer" in page


# ---- Generalization tab
def test_generalization_is_read_from_the_repository(client):
    g = client.get("/api/generalization").json()
    man = json.loads((ROOT / "outputs" / "run_manifest.json").read_text())["counts"]
    t = g["trial_layer"]
    assert (t["nodes"], t["edges"]) == (man["nodes"], man["edges"]) and t["programs"] == man["by_category"]["tmkg:ClinicalTrialProgram"]
    assert t["classes"] == len(man["by_category"]) and t["predicates"] == len(man["by_predicate"])
    assert {c["name"] for c in g["cards"]} >= {"pharos", "chembl37"} and all(c["card"].startswith("sources/") for c in g["cards"])
    steps = g["byo"]["steps"]
    assert len(steps) == 5 and "SYNTHETIC" in g["byo"]["label"] and steps[0]["title"].startswith("Put your table")
    assert [s_["command"].split()[1] for s_ in steps if s_["command"]] == ["pipelines/build_kg.py", "pipelines/prune_kg.py"]
    assert g["adc"]["available"] and "SYNTHETIC" in g["adc"]["label"] and g["adc"]["synthetic_lines"] >= 1
    assert "TUB-040" in g["adc"]["title"] and g["adc"]["sections"]


def test_the_byo_example_is_built_when_asked_with_the_tutorials_numbers():
    pytest.importorskip("kgbuilder")
    from kgbuilder.biolink import DEFAULT
    if not Path(DEFAULT).exists():
        pytest.skip("BioLink model not downloaded")
    b = us.generalization(ROOT)["byo"]["built"]
    assert (b["nodes"], b["edges"], b["problems"]) == (9, 6, [])            # the numbers docs/BRING_YOUR_OWN_SOURCE.md quotes
    assert b["classes"] == ["biolink:Protein", "biolink:SmallMolecule"]


def test_generalization_degrades_gracefully_without_files(tmp_path):
    g = us.generalization(tmp_path)
    assert g["trial_layer"]["nodes"] is None and g["cards"] == [] and g["adc"] == {"available": False}
    assert g["byo"]["built"] is None and g["byo"]["reason"] and g["byo"]["steps"] == []


def test_the_page_draws_the_three_demonstrations_and_names_synthetic_data(client):
    page = client.get("/").text
    for needle in ("showGeneralization", "A second graph: failed clinical-trial programs", "Bring your own source",
                   "A sponsor's question: planning a pivotal trial", "SYNTHETIC"):
        assert needle in page, needle


# ---- backend switch: local | neptune (PK15: Neptune is available only when the host's status file says so; asking it never fails)
def neptune_up(monkeypatch, tmp_path, state="available"):
    """Configures Amazon Neptune for the app: NEPTUNE_ENDPOINT set and a fresh status.json with the host's state."""
    import datetime
    d = tmp_path / "neptune_status"
    d.mkdir(exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (d / "status.json").write_text(json.dumps({"state": state, "checked_at": now}))
    monkeypatch.setenv("NEPTUNE_ENDPOINT", "c.example")
    monkeypatch.setenv("NEPTUNE_STATUS_DIR", str(d))
    return d


def test_backend_options_follow_the_environment(tmp_path, monkeypatch):
    assert [o["available"] for o in nb.backend_options({})] == [True, False]
    assert nb.backend_options({})[1]["state"] == "not_configured" and "not set up" in nb.backend_options({})[1]["reason"]
    d = neptune_up(monkeypatch, tmp_path)
    env = {"NEPTUNE_ENDPOINT": "x.example", "NEPTUNE_STATUS_DIR": str(d)}
    assert [o["available"] for o in nb.backend_options(env)] == [True, True]
    neptune_up(monkeypatch, tmp_path, "stopped")
    off = nb.backend_options(env)[1]
    assert off["available"] is False and off["state"] == "asleep" and "asleep to save cost" in off["reason"]
    assert [o["available"] for o in nb.backend_options({"NEPTUNE_ENDPOINT": "x.example", "NEPTUNE_STATUS_DIR": str(tmp_path / "none")})] == [True, False]


def test_neptune_not_set_up_answers_locally_with_a_note(client):
    assert client.get("/api/backends").json()["default"] == "local"
    r = client.post("/api/pharos/ask", json={"question": "Which diseases is PTGS2 associated with?", "backend": "neptune"})
    assert r.status_code == 200, r.text                      # PK15: no 409 any more
    j = r.json()
    assert j["graph_backend"] == "local" and j["neptune"]["answered"] is False and j["neptune"]["state"] == "not_configured"
    assert client.post("/api/pharos/ask", json={"question": "x", "backend": "oracle"}).status_code == 400


def neptune_snapshot(tmp_path, graph, calls):
    """A Snapshot whose HTTP layer answers an openCypher request with the rows querier.run_local gives on the fixture graph,
    in Neptune's response shape {'results': [{column: value}]} - what a loaded cluster returns for the same query."""
    snap = Snapshot(tmp_path, mode="live", sleep=0)

    def fake_http(method, url, params, body, headers=None, store_client_errors=True, budget=None):
        calls.append((method, url, body))
        if "BOOM" in body["query"]:
            raise RuntimeError("POST failed after retries: HTTP 500")
        return {"results": querier.run_local(body["query"], graph)["rows"]}
    snap._http = fake_http
    return snap


def test_neptune_runner_returns_run_locals_rows_and_evidence_ids(repo, tmp_path):
    g = PharosGraph(root=repo)
    cy = ("MATCH (g:Gene {name:'PTGS2'})-[r:gene_associated_with_condition]->(d:Disease) "
          "RETURN d.name AS disease, r.source AS source, r.id AS evidence ORDER BY disease LIMIT 50")
    calls = []
    got = nb.NeptuneRunner("c.example", snap=neptune_snapshot(tmp_path, g, calls)).run(cy)
    want = querier.run_local(cy, g)
    assert got["columns"] == want["columns"] and got["rows"] == want["rows"] and got["evidence"] == want["evidence"] and want["evidence"]
    assert "backend" not in got and calls[0][1] == "https://c.example:8182/openCypher" and calls[0][2] == {"query": cy}
    again = nb.NeptuneRunner("c.example", snap=neptune_snapshot(tmp_path, g, calls)).run(cy)       # frozen: no second call
    assert again == got and len(calls) == 1


def test_neptune_errors_are_reported_not_hidden(tmp_path):
    with pytest.raises(nb.NeptuneError, match="NEPTUNE_ENDPOINT"):
        nb.NeptuneRunner("", snap=Snapshot(tmp_path, sleep=0)).run("MATCH (n) RETURN n.id LIMIT 1")
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    snap._http = lambda *a, **k: {"message": "bad query"}
    with pytest.raises(nb.NeptuneError, match="without results"):
        nb.NeptuneRunner("c.example", snap=snap).run("MATCH (g:Gene)-[r:has_gene_product]->(p) RETURN p.id AS p, r.id AS evidence LIMIT 1")


def test_a_question_asked_on_neptune_cites_the_same_edges_as_the_local_answer(client, hosted_stub, repo, tmp_path, monkeypatch):
    neptune_up(monkeypatch, tmp_path)
    calls = []
    snap = neptune_snapshot(tmp_path, appmod._pharos["g"], calls)
    monkeypatch.setattr(nb, "Snapshot", lambda *a, **k: snap)
    q = {"question": QUESTION}
    local = client.post("/api/pharos/ask", json={**q, "backend": "local"}).json()
    remote = client.post("/api/pharos/ask", json={**q, "backend": "neptune"})
    assert remote.status_code == 200, remote.text
    remote = remote.json()
    assert remote["graph_backend"] == "neptune" and calls and local["graph_backend"] == "local"
    assert remote["evidence"] == local["evidence"] and remote["rows"] == local["rows"] and local["evidence"]
    m = remote["neptune"]["match"]
    assert m["links_identical"] == m["links_local"] == m["links_neptune"] == len(set(local["evidence"]))
    assert client.get("/api/backends").json()["options"][1]["available"] is True
    assert not list(tmp_path.glob("neptune/*.json.gz"))         # the live path stores nothing


def test_a_neptune_failure_gives_the_local_answer_and_a_note(client, hosted_stub, tmp_path, monkeypatch):
    neptune_up(monkeypatch, tmp_path)
    monkeypatch.setattr(nb.NeptuneRunner, "run", lambda self, cy: (_ for _ in ()).throw(nb.NeptuneError("Neptune query failed: down")))
    local = client.post("/api/pharos/ask", json={"question": QUESTION}).json()
    r = client.post("/api/pharos/ask", json={"question": QUESTION, "backend": "neptune"})
    assert r.status_code == 200, r.text                      # PK15: no 502 any more
    j = r.json()
    assert j["graph_backend"] == "local" and j["rows"] == local["rows"] and j["evidence"] == local["evidence"]
    assert j["neptune"]["answered"] is False and "down" not in j["neptune"]["note"] and "this server's graph" in j["neptune"]["note"]


def test_default_backend_can_be_neptune_only_when_available(client, tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_GRAPH_BACKEND", "neptune")
    assert client.get("/api/backends").json()["default"] == "local"                    # asked for, not available
    monkeypatch.setenv("NEPTUNE_ENDPOINT", "c.example")
    assert client.get("/api/backends").json()["default"] == "local"                    # configured, but no status file
    neptune_up(monkeypatch, tmp_path)
    assert client.get("/api/backends").json()["default"] == "neptune"


# ---- the page's script parses (a syntax error would blank the whole app)
def test_page_script_is_syntactically_valid(client):
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    page = client.get("/").text
    js = re.search(r"<script>(.*)</script>", page, re.S).group(1)
    r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:500]


def test_a_shared_card_link_opens_for_a_first_time_visitor(client):
    """7 Nov: route() returned early while no role was chosen, so #pharos/node/<id> (the card's own "Link" button) showed a
    first-time visitor the start page - no card, no graph. Now the first role is chosen quietly, as the search box does.
    Checked in a browser-like run (jsdom against the hosted API): before 0 picture elements, after 94 (EGFR) / 101 (T2D)."""
    for path in ("/", "/classic"):                 # 11 Nov: the front page and the first page (kept at /classic) both do this
        page = client.get(path).text
        route = page[page.index("function route() {"):page.index('window.addEventListener("hashchange", route);')]
        assert "if (routing || !P) return;" not in route                           # the old early exit is gone
        assert "if (!P) { if (PERSONAS.length && location.hash.length > 1) choose(PERSONAS[0].id, true); return; }" in route
        load = page[page.index("async function loadPersonas() {"):page.index("let LANDING =")]
        assert "else if (PERSONAS.length && (location.hash.length > 1 || new URLSearchParams(location.search).get(\"node\"))) " \
               "choose(PERSONAS[0].id, true);" in load, path
        choose = page[page.index("function choose(id, quiet"):page.index("function runPill(")]
        assert "else if (location.hash.length > 1 && quiet) route();" in choose, path   # choose() routes the link once P is set


def test_model_label_carries_the_start_up_check_and_answer_label_names_the_backup():
    """CC-36: the landing note says what the start-up check found; warn drives the page banner; an answer by the backup
    model or the stand-in after a failed hosted call is labelled."""
    import nim as nimmod
    from types import SimpleNamespace
    hosted = SimpleNamespace(backend="nim")
    failed = nimmod.preflight_summary({"backend": "nim", "rows": [dict(model="m1", status="failed"), dict(model="m2", status="ok")]})
    lab = us.model_label(hosted, failed)
    assert lab["warn"] is True and failed["sentence"] in lab["note"] and lab["preflight"]["failed"] == ["m1"]
    assert us.model_label(hosted)["warn"] is False and us.model_label(hosted)["preflight"] is None
    off = us.model_label(SimpleNamespace(backend="offline"), failed)
    assert off["warn"] is False and failed["sentence"] not in off["note"]
    sm = us.answer_label(dict(backend="nim", model="m2", model_fallback_from="m1"))
    assert sm["kind"] == "second_model" and "m1" in sm["text"] and "m2" in sm["text"]
    run = dict(backend="nim", model_calls=[dict(role="specialist", backend="nim"), dict(role="verifier", backend=nimmod.OFFLINE)])
    assert us.answer_label(run)["kind"] == "partly_offline"            # FIX1 F4: 1 of 2 steps by fixed rules
    assert us.answer_label(dict(backend="nim", model_calls=[dict(backend=nimmod.OFFLINE)] * 2))["kind"] == "fallback"
    assert us.answer_label(dict(backend="nim", model_calls=[dict(backend="nim")]))["kind"] == "hosted"


def test_start_up_check_runs_once_and_reaches_the_landing_note(monkeypatch):
    """CC-36: start_preflight runs nim.preflight once per process; /api/landing shows its summary (and warn when a model
    failed). Not started (the tests' default): no check in the label."""
    monkeypatch.setattr(appmod, "_preflight", {"started": False, "rep": None})
    appmod._state.update(nim=NIM(backend="offline"))
    c = TestClient(appmod.app)
    assert c.get("/api/landing").json()["model"]["preflight"] is None
    monkeypatch.setenv("TMKG_NIM_PREFLIGHT", "off")
    assert appmod.start_preflight(background=False) is False
    monkeypatch.delenv("TMKG_NIM_PREFLIGHT")
    assert appmod.start_preflight(background=False) is True and appmod.start_preflight(background=False) is False
    assert c.get("/api/landing").json()["model"]["preflight"]["state"] == "offline"     # offline: nothing hosted to check
    hosted = NIM(backend="nim")
    monkeypatch.setattr(appmod, "_preflight", {"started": True, "rep": {"backend": "nim", "rows": [
        dict(model="nvidia/a", status="failed"), dict(model="nvidia/b", status="ok")]}})
    appmod._state.update(nim=hosted)
    try:
        m = c.get("/api/landing").json()["model"]
        assert m["warn"] is True and "1 of 2 hosted models did not answer." in m["note"] and "nvidia/a" not in m["note"]
    finally:
        appmod._state.update(nim=NIM(backend="offline"))
