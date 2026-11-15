"""FIX1 F4 and F5 (gate 1: G1-U-01, G1-U-02, G1-SEC-06, G1-P1-04, G1-P1-02).

F4: when the hosted model stops answering, /api/status says typed questions are paused (from the outcome of the last answers,
no extra model call); an answer the model could not run leads to the card of a recognised gene or disease; the start-up check
stops at the first HTTP 429; a report where only some steps used fixed rules says "partly" with the counts.
F5: a typed trial-graph question never answers HTTP 500; it says the question was not run and leads to the trial's record."""
import json
import sys
import time
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import app as appmod  # noqa: E402
import nim as nimmod  # noqa: E402
import snapshot  # noqa: E402
import usability  # noqa: E402
from nim import NIM  # noqa: E402
from snapshot import Snapshot  # noqa: E402

SOLANEZUMAB = ("Why did solanezumab fail in EXPEDITION 3 for Alzheimer disease, and which targets are better supported for "
               "Alzheimer disease?")


def hosted(tmp_path, http):
    """A hosted NIM (fallback to fixed rules, as in the app) whose HTTP layer is `http`."""
    snap = Snapshot(tmp_path / "llm", mode="live", sleep=0)
    snap._http = http
    n = NIM(backend="nim", snap=snap, fallback="offline")
    n.key = "nvapi-stub"
    return n


def failing(*a, **k):
    raise requests.ConnectionError("the model service is not answering")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for var in ("TMKG_ASK_LIMIT_PER_HOUR", "TMKG_ASK_LIMIT_PER_DAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(appmod, "_model_health", usability.ModelHealth())
    saved = appmod._state.get("nim")
    yield
    appmod._state["nim"] = saved if saved is not None else NIM(backend="offline")


# ------------------------------------------------------------------------------------------------ F4 fix 1: status
def test_model_health_rule():
    h = usability.ModelHealth(n=3, window=600)
    assert not h.paused(0)
    for t in (0, 10, 20):
        h.record(False, t)
    assert h.paused(30)
    assert not h.paused(605)                       # the oldest of the three left the 10 minutes
    h.record(True, 40)
    assert not h.paused(41)                        # one answer since: not paused
    assert usability.label_outcome(dict(kind="model_unavailable")) is False
    assert usability.label_outcome(dict(kind="fallback")) is False
    assert usability.label_outcome(dict(kind="hosted")) is True and usability.label_outcome(dict(kind="partly_offline")) is True
    assert usability.label_outcome(dict(kind="cached")) is None and usability.label_outcome(dict(kind="offline")) is None


def test_model_label_paused_says_what_still_works():
    lab = usability.model_label(NIM(backend="offline"), paused=True)
    assert lab["paused"] is False and lab["warn"] is False                    # nothing hosted to pause
    class Hosted:
        backend = "nim"
    lab = usability.model_label(Hosted(), paused=True)
    assert lab["paused"] is True and lab["warn"] is True
    assert lab["note"] == ("Typed questions are paused: the model service is not answering. Stored and tested answers, "
                           "cards and downloads work.")


def test_three_failed_model_calls_make_status_warn(tmp_path):
    appmod._state.pop("kg", None) if appmod._state.get("kg") is None else None
    appmod.state()
    appmod._state["nim"] = hosted(tmp_path, failing)
    c = TestClient(appmod.app)
    before = c.get("/api/status").json()["model"]
    assert before["warn"] is False and before["paused"] is False
    for _ in range(3):
        r = c.post("/api/query", json={"question": "What was the primary outcome of TULIP-1?"})
        assert r.status_code == 200 and r.json().get("fallback_from") == "nim"     # fixed rules answered: a failure
    st = c.get("/api/status").json()["model"]
    assert st["warn"] is True and st["paused"] is True and st["note"].startswith("Typed questions are paused")
    assert c.get("/api/landing").json()["model"]["paused"] is True


def test_status_makes_no_model_call(tmp_path):
    calls = []
    appmod._state.pop("kg", None) if appmod._state.get("kg") is None else None
    appmod.state()
    appmod._state["nim"] = hosted(tmp_path, lambda *a, **k: calls.append(1) or failing())
    c = TestClient(appmod.app)
    for _ in range(5):
        c.get("/api/status")
    assert calls == []


# ------------------------------------------------------------------------------------------------ F4 fix 2: a way on
def test_model_unavailable_summary_names_the_card_of_a_recognised_gene():
    s = dict(headline="The graph returned no rows for this question.", confidence=dict(level="low", reason="x"), points=[],
             next_step="x", plan=dict(entities=[dict(id="NCBIGene:120892", label="LRRK2")]))
    out = appmod.model_unavailable_summary(s, dict(text="The hosted model did not answer."))
    assert out["next_step"].startswith("Open the LRRK2 card") and out["cards"] == [dict(id="NCBIGene:120892", label="LRRK2")]
    two = dict(s, plan=dict(entities=[dict(id="NCBIGene:4217", label="MAP3K5"), dict(id="KEGG:hsa1", label="a pathway"),
                                      dict(id="MONDO:0007915", label="NASH")]))
    out = appmod.model_unavailable_summary(two, dict(text="x"))
    assert out["next_step"].startswith("Open the MAP3K5 card or the NASH card")
    assert [c["id"] for c in out["cards"]] == ["NCBIGene:4217", "MONDO:0007915"]
    none = appmod.model_unavailable_summary(dict(s, plan=dict(entities=[])), dict(text="x"))
    assert none["next_step"] == "Try again in a minute, or use one of the tested questions." and none["cards"] == []


def test_card_next_steps_rules():
    assert usability.card_next_steps([]) == ("", [])
    sent, cards = usability.card_next_steps([dict(id="HP:0002511", label="Alzheimer disease")])
    assert sent == "" and cards == []                                    # a phenotype is not offered
    sent, cards = usability.card_next_steps([dict(id=f"NCBIGene:{i}", label=f"G{i}") for i in range(4)])
    assert len(cards) == 2 and sent == "Open the G0 card or the G1 card"


def test_the_model_unavailable_label_points_to_cards_when_something_was_recognised():
    base = dict(no_query=True, rows=[], fallback_from="nim")
    assert "Open the card" in usability.answer_label(dict(base, entities=["NCBIGene:120892"]))["text"]
    assert "Open the card" not in usability.answer_label(base)["text"]


def test_the_page_renders_and_wires_card_buttons():
    page = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")
    assert "${cardButtons(s.cards)}" in page and 'data-opencard' in page
    assert 'querySelectorAll("button[data-opencard]")' in page and "openProgramPanel(b.dataset.opencard)" in page
    assert "nimBanner(m.paused ? m.note : MODEL_DOWN)" in page and "function checkPaused()" in page


# ------------------------------------------------------------------------------------------------ F4 fix 3: preflight
def test_preflight_under_429_sends_once_and_says_rate_limited(tmp_path, monkeypatch):
    sends = []

    class R429:
        status_code, text = 429, "Too Many Requests"

    def send(*a, **k):
        sends.append(a[0] if a else k.get("url"))
        return R429()
    monkeypatch.setattr(snapshot.requests, "get", send)
    monkeypatch.setattr(snapshot.requests, "post", send)
    monkeypatch.setattr(snapshot.time, "sleep", lambda s: pytest.fail(f"slept {s} s behind a rate limit"))
    n = NIM(backend="nim")
    n.key = "nvapi-stub"
    t0 = time.time()
    rep = nimmod.preflight(n, snap=Snapshot(tmp_path / "pf", mode="refresh", sleep=0))
    assert len(sends) == 1 and time.time() - t0 < 5
    assert rep["rate_limited"] is True and rep["ok"] is False and rep["rows"]
    assert all("rate limited" in r["detail"] for r in rep["rows"])
    rep2 = nimmod.preflight(n, snap=Snapshot(tmp_path / "pf2", mode="refresh", sleep=0), list_models=False)
    assert len(sends) == 2 and rep2["rate_limited"] is True                # the first chat call: one send, then stop


def test_preflight_still_checks_every_model_when_healthy(tmp_path):
    def http(method, url, params, body, headers=None, store_client_errors=True, budget=None):
        assert budget is not None                                          # every call carries the short web budget
        if url.endswith("/models"):
            return {"data": []}
        if url.endswith("/chat/completions"):
            return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}}
        if url.endswith("/embeddings"):
            return {"data": [{"index": 0, "embedding": [0.1]}]}
        return {"rankings": [{"index": 0, "logit": 1.0}]}
    snap = Snapshot(tmp_path / "pf", mode="refresh", sleep=0)
    snap._http = http
    n = NIM(backend="nim")
    n.key = "nvapi-stub"
    rep = nimmod.preflight(n, snap=snap)
    assert rep["ok"] is True and rep["rate_limited"] is False


# ------------------------------------------------------------------------------------------------ F4 fix 4: partly offline
def test_a_report_with_some_offline_steps_is_partly_offline():
    calls = [dict(role="x", backend="nim")] * 38 + [dict(role="x", backend=nimmod.OFFLINE)] * 3
    lab = usability.answer_label(dict(backend="nim", model_calls=calls))
    assert lab["kind"] == "partly_offline" and lab["steps_offline"] == 3 and lab["steps"] == 41
    assert "3 of the 41 steps" in lab["text"] and lab["offline"] is False
    all_off = usability.answer_label(dict(backend="nim", model_calls=[dict(backend=nimmod.OFFLINE)] * 4))
    assert all_off["kind"] == "fallback"
    assert usability.answer_label(dict(backend="nim", model_calls=[dict(backend="nim")] * 4))["kind"] == "hosted"
    page = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")
    assert 'partly_offline: "Partly fixed rules"' in page


# ------------------------------------------------------------------------------------------------ F5: never a 500
def test_the_solanezumab_question_answers_offline():
    appmod._state.pop("kg", None) if appmod._state.get("kg") is None else None
    appmod.state()
    appmod._state["nim"] = NIM(backend="offline")
    r = TestClient(appmod.app).post("/api/query", json={"question": SOLANEZUMAB})
    assert r.status_code == 200 and r.json()["rows"]


@pytest.mark.parametrize("reply", [
    {"cypher": "MATCH (t)-[r]->(d:Disease {id:'MONDO:0004975'}) RETURN t AS target, r.id AS evidence LIMIT 50"},
    {"cypher": "MATCH (t)-[r]->(d:Disease {id:'MONDO:0004975'}) RETURN t.id AS target, max(r.score) AS s, r.id AS e LIMIT 5"},
    {"cypher": None}, {"query": "MATCH (n) RETURN n"}])
def test_the_solanezumab_question_with_a_plausible_but_invalid_hosted_query(tmp_path, reply):
    appmod._state.pop("kg", None) if appmod._state.get("kg") is None else None
    appmod.state()
    appmod._state["nim"] = hosted(tmp_path, lambda *a, **k: {"choices": [{"message": {"content": json.dumps(reply)}}], "usage": {}})
    r = TestClient(appmod.app).post("/api/query", json={"question": SOLANEZUMAB})
    assert r.status_code == 200 and isinstance(r.json()["rows"], list)


def test_a_server_failure_answers_not_run_with_the_trial_record(monkeypatch):
    appmod._state.pop("kg", None) if appmod._state.get("kg") is None else None
    appmod.state()

    class Broken:
        def __init__(self, *a, **k):
            pass

        def ask(self, question):
            raise RuntimeError("an unexpected failure inside the query step")
    monkeypatch.setattr(appmod.querier, "Querier", Broken)
    c = TestClient(appmod.app, raise_server_exceptions=False)
    r = c.post("/api/query", json={"question": SOLANEZUMAB})
    assert r.status_code == 200
    j = r.json()
    assert j["server_error"] is True and j["rows"] == [] and j["label"]["kind"] == "not_run"
    assert j["cards"] and j["cards"][0]["kind"] == "trial" and j["cards"][0]["label"] == "EXPEDITION 3"
    assert j["next_step"].startswith("Open the EXPEDITION 3 record")
    assert "Traceback" not in r.text and "RuntimeError" not in r.text
    s = c.post("/api/summarize", json=dict(kind="query", result=j)).json()
    assert s["headline"] == appmod.QUERY_FAILED_TEXT and s["cards"] == j["cards"] and "no rows" not in s["headline"]
    assert usability.answer_label(j)["kind"] == "not_run"


def test_busy_is_still_503(monkeypatch):
    appmod._state.pop("kg", None) if appmod._state.get("kg") is None else None
    appmod.state()

    class Busy:
        def __init__(self, *a, **k):
            pass

        def ask(self, question):
            raise appmod.querier.QueryBusy("busy")
    monkeypatch.setattr(appmod.querier, "Querier", Busy)
    assert TestClient(appmod.app).post("/api/query", json={"question": SOLANEZUMAB}).status_code == 503
