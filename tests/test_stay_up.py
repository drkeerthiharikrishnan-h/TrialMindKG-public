"""The app stays up on the open internet: one test group per finding of the 11 Nov review (SEC-01 .. SEC-18).
No network: the hosted model is a stand-in transport that fails or answers on command; the Pharos side runs on the fixture graph."""
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))
import app as appmod  # noqa: E402
import index_graph  # noqa: E402
import nim as nimmod  # noqa: E402
import orchestrator  # noqa: E402
import pharos_app  # noqa: E402
import pharos_index  # noqa: E402
import querier  # noqa: E402
import snapshot as sn  # noqa: E402
import specialists  # noqa: E402
import usability  # noqa: E402
from kg_query import KGIndex  # noqa: E402
from nim import NIM  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402
from snapshot import HttpBudget, Snapshot  # noqa: E402

from fixtures.pharos_fixture import build, GENE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AWS = ROOT / "aws"
TRIAL_QUESTION = "Why did TULIP-1 miss its primary endpoint, and what changed later?"
OTHER_QUESTION = "What happened after the GENERATION HD1 failure?"


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def index_file(repo):
    return build_index(repo, chunk=3, log=lambda *_: None)["path"]


@pytest.fixture(scope="module")
def trial_kg():
    return KGIndex()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Every test starts without limits, slots, kept reports or a Pharos graph left over from another test."""
    for var in ("TMKG_ASK_LIMIT_PER_HOUR", "TMKG_ASK_LIMIT_PER_DAY", "TMKG_MODEL_CONCURRENCY", "TMKG_QUERY_CONCURRENCY",
                "TMKG_DOWNLOADS_BASE", "NEPTUNE_ENDPOINT", "TMKG_PHAROS_INDEX", "TMKG_WEB_NIM_DEADLINE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(appmod, "_ask_log", {})
    monkeypatch.setattr(appmod, "_day_log", type(appmod._day_log)())
    appmod._reports.clear()
    yield
    appmod._reports.clear()
    appmod._pharos.clear()
    appmod._state.update(nim=NIM(backend="offline"))


@pytest.fixture
def trial_client(trial_kg):
    appmod._state.update(kg=trial_kg, nim=NIM(backend="offline"))
    return TestClient(appmod.app)


@pytest.fixture
def pharos_client(repo, index_file, monkeypatch):
    """The app on the fixture card index (the hosted set-up: no live graph)."""
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    appmod._pharos.clear()
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    return TestClient(appmod.app)


def hosted(tmp_path, answer=None, error=None, seen=None):
    """A 'hosted' model client on a stand-in transport. answer: the JSON-able reply every call gets; error: the exception
    every call raises instead. seen collects (model id, budget) per call that reached the transport."""
    snap = Snapshot(tmp_path / "llm", mode="live", sleep=0)

    def http(method, url, params, body, headers=None, store_client_errors=True, budget=None):
        if seen is not None:
            seen.append((body.get("model"), budget))
        if error is not None:
            raise error
        return {"choices": [{"message": {"content": json.dumps(answer)}}], "usage": {}}
    snap._http = http
    n = NIM(backend="nim", snap=snap, fallback="offline")
    n.key = "nvapi-stub"
    return n


class FakeReply:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text

    def json(self):
        return {"ok": True}


def fake_transport(monkeypatch, behaviour):
    """Replaces requests.post and time.sleep inside snapshot.py. behaviour(n) -> a status code or an exception to raise for
    the n-th call. Returns (calls: the timeout of every call, sleeps: every sleep in seconds)."""
    calls, sleeps = [], []

    def post(url, json=None, timeout=None, headers=None):
        calls.append(timeout)
        out = behaviour(len(calls))
        if isinstance(out, Exception):
            raise out
        return FakeReply(out)
    monkeypatch.setattr(sn.requests, "post", post)
    monkeypatch.setattr(sn.time, "sleep", lambda s: sleeps.append(s))
    return calls, sleeps


# ------------------------------------------------------------------------------------------------ SEC-01
def old_find_entities(g, question, limit=10):
    """The per-phrase loop find_entities had before 11 Nov (one query per phrase), for comparison."""
    found = set()
    for pat in index_graph.ID_PATTERNS:
        for m in re.findall(pat, question):
            for cand in (m, f"CHEMBL.COMPOUND:{m}" if m.startswith("CHEMBL") else None,
                         f"UniProtKB:{m}" if re.fullmatch(r"[OPQ][0-9][A-Z0-9]{3}[0-9]", m) else None):
                if cand and cand in g.nodes:
                    found.add(cand)
    words = [re.sub(r"'s$", "", w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", question)]
    for i in range(len(words)):
        for j in (1, 2, 3):
            phrase = " ".join(words[i:i + j]).lower()
            if len(phrase) >= 3:
                found.update(r[0] for r in g.ci.con.execute("SELECT id FROM nodes WHERE lname = ? OR lsymbol = ?", (phrase, phrase)))
    return sorted(found)[:limit]


def test_sec01_question_phrases_are_the_1_to_3_word_windows():
    assert index_graph.question_phrases("Which diseases is PTGS2's gene in?") == [
        "which", "which diseases", "which diseases is", "diseases", "diseases is", "diseases is ptgs2", "is ptgs2",
        "is ptgs2 gene", "ptgs2", "ptgs2 gene", "ptgs2 gene in", "gene", "gene in"]
    assert index_graph.question_phrases("a b") == ["a b"] and index_graph.question_phrases("") == []


def test_sec01_find_entities_reads_the_nodes_table_once_and_finds_the_same_ids(index_file):
    g = CardIndex(index_file).graph()
    questions = ["Which diseases is the gene PTGS2 associated with?", "Which compounds target PTGS2 and what does ibuprofen's "
                 "label say about pain, inflammation and prostaglandin G/H synthase 2 in adults?", pharos_app.DEMO_QUESTIONS[0]["question"],
                 "nothing known here at all", f"what is {GENE}"] + [d["question"] for d in pharos_app.DEMO_QUESTIONS]
    for q in questions:
        assert g.find_entities(q) == old_find_entities(g, q), q
    assert any(g.find_entities(q) for q in questions)
    statements = []
    g.ci.con.set_trace_callback(statements.append)
    try:
        g.find_entities("Which compounds target PTGS2 and what is known about pain in adults today")   # 13 words, 36 phrases
    finally:
        g.ci.con.set_trace_callback(None)
    scans = [s for s in statements if "lname" in s]
    assert len(scans) == 1 and " IN (" in scans[0]
    many = [f"phrase number {i}" for i in range(950)]                       # more than one chunk still works
    assert g.ids_named(many + ["ptgs2"]) == g.ids_named(["ptgs2"]) != set()


def test_sec01_each_thread_reads_the_index_through_its_own_read_only_connection(index_file):
    idx = CardIndex(index_file)
    mine, theirs = idx.con, []
    assert idx.con is mine                                                  # the same thread keeps its connection
    t = threading.Thread(target=lambda: theirs.append((idx.con, idx.con.execute("SELECT count(*) FROM nodes").fetchone()[0])))
    t.start()
    t.join()
    assert theirs[0][0] is not mine and theirs[0][1] == mine.execute("SELECT count(*) FROM nodes").fetchone()[0] > 0
    with pytest.raises(Exception):
        mine.execute("CREATE TABLE x (a)")                                  # read-only


def test_sec01_a_question_is_at_most_500_characters_on_every_ask_endpoint(trial_client):
    long = "why " * 126                                                     # 504 characters
    for path in ("/api/ask", "/api/query", "/api/pharos/ask"):
        r = trial_client.post(path, json={"question": long})
        assert r.status_code == 422 and r.json()["detail"] == "Please keep the question under 500 characters.", path
    for path in ("/api/ask_stream", "/api/report.pdf", "/api/report_subgraph.zip"):
        r = trial_client.get(path, params={"q": long})
        assert r.status_code == 422 and "under 500 characters" in r.json()["detail"], path
    assert trial_client.post("/api/query", json={"question": "x" * 500}).status_code == 200


def test_sec01_the_newest_index_file_wins(tmp_path):
    assert pharos_index.INDEX_NAMES[0] == "card_index_v3.sqlite"
    d = tmp_path / "data" / "kg" / "pharos_index"
    d.mkdir(parents=True)
    for name in ("card_index.sqlite", "card_index_v2.sqlite"):
        (d / name).write_bytes(b"")
    assert pharos_index.index_path(tmp_path) == d / "card_index_v2.sqlite"
    (d / "card_index_v3.sqlite").write_bytes(b"")
    assert pharos_index.index_path(tmp_path) == d / "card_index_v3.sqlite"


# ------------------------------------------------------------------------------------------------ SEC-02
def test_sec02_batch_waits_are_unchanged(tmp_path, monkeypatch):
    """The pipelines keep the long waits: a server that always says 429 is asked 16 times with 585 s of sleep (12 rate-limit
    waits of 10..60 s, then 4 attempts with 1+2+4+8 s), as measured in the review."""
    calls, sleeps = fake_transport(monkeypatch, lambda n: 429)
    with pytest.raises(RuntimeError):
        Snapshot(tmp_path, mode="live", sleep=0).post("x", "https://example.org/api", {"a": 1}, store_client_errors=False)
    assert len(calls) == 16 and sum(sleeps) == 585 and set(calls) == {180}


def test_sec02_a_web_call_does_not_wait_behind_a_rate_limit(tmp_path, monkeypatch):
    calls, sleeps = fake_transport(monkeypatch, lambda n: 429)
    with pytest.raises(sn.RateLimited):
        Snapshot(tmp_path, mode="live", sleep=0).post("x", "https://example.org/api", {"a": 1}, store_client_errors=False,
                                                      budget=HttpBudget(timeout=30, attempts=2))
    assert len(calls) == 1 and sum(sleeps) == 0 and not list(tmp_path.rglob("*.json.gz"))


def test_sec02_a_web_call_times_out_after_30_s_and_retries_once(tmp_path, monkeypatch):
    import requests
    calls, sleeps = fake_transport(monkeypatch, lambda n: requests.exceptions.ReadTimeout("slow"))
    with pytest.raises(RuntimeError, match="failed after retries"):
        Snapshot(tmp_path, mode="live", sleep=0).post("x", "https://example.org/api", {"a": 1}, budget=HttpBudget(timeout=30, attempts=2))
    assert calls == [30, 30] and sum(sleeps) <= 1.0                      # at most 61 s instead of 735 s
    calls, sleeps = fake_transport(monkeypatch, lambda n: 500 if n == 1 else 200)
    r = Snapshot(tmp_path, mode="live", sleep=0).post("x", "https://example.org/api", {"a": 2}, budget=HttpBudget(timeout=30, attempts=2))
    assert r.body == {"ok": True} and len(calls) == 2                    # the one retry can succeed


def test_sec02_nothing_is_sent_after_the_deadline_but_a_stored_answer_is_still_returned(tmp_path, monkeypatch):
    calls, _ = fake_transport(monkeypatch, lambda n: 200)
    s = Snapshot(tmp_path, mode="live", sleep=0)
    live = HttpBudget(timeout=30, attempts=2, deadline=time.monotonic() + 5)
    assert s.post("x", "https://example.org/api", {"a": 1}, budget=live).body == {"ok": True}
    assert len(calls) == 1 and 0 < calls[0] <= 5                         # the timeout never exceeds the time left
    past = HttpBudget(timeout=30, attempts=2, deadline=time.monotonic() - 1)
    with pytest.raises(sn.DeadlineExceeded):
        s.post("x", "https://example.org/api", {"a": 2}, budget=past)
    assert len(calls) == 1                                               # no request went out
    again = s.post("x", "https://example.org/api", {"a": 1}, budget=past)
    assert again.cached and again.body == {"ok": True} and len(calls) == 1
    assert past.remaining() < 0 and HttpBudget().remaining() is None


def test_sec02_an_answer_that_cannot_be_stored_is_still_an_answer_for_the_web_only(tmp_path, monkeypatch):
    fake_transport(monkeypatch, lambda n: 200)
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")                                   # mkdir under a file fails (also as root)
    s = Snapshot(blocked / "snapshots", mode="live", sleep=0)
    assert s.post("x", "https://example.org/api", {"a": 1}, budget=HttpBudget()).body == {"ok": True}
    with pytest.raises(OSError):
        s.post("x", "https://example.org/api", {"a": 1})                 # a pipeline must stop


def test_sec02_a_stand_in_transport_with_the_older_arguments_still_works(tmp_path):
    """Tests and the harness replace Snapshot._http with six-argument stand-ins; a budget is then simply not passed on."""
    assert sn._takes_budget(Snapshot(tmp_path)._http) and sn._takes_budget(lambda *a: None)
    assert sn._takes_budget(lambda m, u, p, b, headers=None, store_client_errors=True, budget=None: None)
    assert not sn._takes_budget(lambda m, u, p, b, headers=None, store_client_errors=True: None)
    assert not sn._takes_budget(lambda m, u, p, b, headers=None, store_client_errors=True, **kw: None)
    s = Snapshot(tmp_path, mode="live", sleep=0)
    s._http = lambda m, u, p, b, headers=None, store_client_errors=True: {"old": True}
    assert s.post("x", "https://example.org/api", {"a": 1}, budget=HttpBudget()).body == {"old": True}


def test_sec02_web_client_is_a_per_request_copy_with_a_budget_and_a_deadline(tmp_path, monkeypatch):
    seen = []
    base = hosted(tmp_path, error=RuntimeError("HTTP 503"), seen=seen)
    base.calls.append(("earlier", "m", "nim", "k"))
    a, b = nimmod.web_client(base=base), nimmod.web_client(20, base=base)
    assert a is not base and a.calls == [] and a.fallbacks == [] and base.budget is None and len(base.calls) == 1
    assert a.snap is base.snap and a.cfg is base.cfg and a.key == base.key
    assert (a.budget.timeout, a.budget.attempts, a.budget.rate_limit_waits) == (30, 2, 0)
    assert 70 < a.seconds_left() <= 75 and 15 < b.seconds_left() <= 20 and base.seconds_left() is None
    monkeypatch.setenv("TMKG_WEB_NIM_TIMEOUT", "12")
    monkeypatch.setenv("TMKG_WEB_NIM_DEADLINE", "40")
    c = nimmod.web_client(base=base)
    assert c.budget.timeout == 12 and 35 < c.seconds_left() <= 40
    r = a.chat("querier", [{"role": "user", "content": "ENTITIES:\n\nQUESTION: anything"}], schema=querier.SCHEMA)
    assert r.backend == nimmod.OFFLINE and r.meta["fallback_from"] == "nim"      # the labelled stand-in, at once
    assert [m for m, _ in seen] == [a.model("querier"), a.backup_model(a.model("querier"))] and all(bud is a.budget for _, bud in seen)
    assert len(a.calls) == 1 and len(a.fallbacks) == 1 and len(base.calls) == 1 and base.fallbacks == []


def test_sec02_the_app_takes_its_client_from_one_function_and_shares_it_with_router_modules(tmp_path):
    base = hosted(tmp_path, answer={"cypher": "", "explanation": ""})
    appmod._state["nim"] = base
    assert appmod._state is nimmod._web                                  # one registry: nim.web_client() sees the app's client
    for client in (appmod.model_client(), nimmod.web_client()):
        assert client is not base and client.snap is base.snap and client.budget is not None and client.seconds_left() > 60
    assert nimmod.web_base() is base


def test_sec02_a_report_under_a_dead_hosted_model_finishes_at_once_with_the_stand_in(trial_client, tmp_path, monkeypatch):
    """Every hosted call fails: the report is still written (stand-in, labelled). Once the request's time is used up no
    further hosted call is even attempted."""
    seen = []
    appmod._state["nim"] = hosted(tmp_path, error=RuntimeError("POST failed after retries: ReadTimeout"), seen=seen)
    t0 = time.time()
    r = trial_client.post("/api/ask", json={"question": TRIAL_QUESTION})
    assert r.status_code == 200 and time.time() - t0 < 30
    body = r.json()
    assert body["label"]["kind"] == "fallback" and body["sections"] and seen and all(b is not None for _, b in seen)
    assert {c["backend"] for c in body["run"]["model_calls"] if c["role"] != "embed"} == {nimmod.OFFLINE}
    client = nimmod.web_client(0.001, base=NIM(backend="nim", snap=Snapshot(tmp_path / "llm2", mode="live", sleep=0), fallback="offline"))
    time.sleep(0.01)                                                     # the request's time is used up
    calls, _ = fake_transport(monkeypatch, lambda n: 200)                # the real transport
    out = client.chat("querier", [{"role": "user", "content": "ENTITIES:\n\nQUESTION: anything"}], schema=querier.SCHEMA)
    assert out.backend == nimmod.OFFLINE and "DeadlineExceeded" in out.meta["reason"] and calls == []


# ------------------------------------------------------------------------------------------------ SEC-03
def test_sec03_querier_failure_says_that_the_model_did_not_answer(index_file, tmp_path):
    g = CardIndex(index_file).graph()
    n = hosted(tmp_path, error=PermissionError("HTTP 401: key revoked"))
    res = pharos_app.ask(g, n, "Which diseases is the gene PTGS2 associated with?")
    assert res["rows"] == [] and res["no_query"] is True and res["fallback_from"] == "nim" and res["reason"] == "the hosted model could not be reached"
    assert len(res["attempts"]) == 3 and res["backend"] == "nim"
    label = usability.answer_label(res)
    assert label["kind"] == "model_unavailable" and label["offline"] is True
    assert label["text"].startswith("The hosted model did not answer") and "tested questions" in label["text"]
    ok = hosted(tmp_path / "ok", answer={"cypher": "", "explanation": "cannot"})   # the model answers, but writes no query
    res2 = pharos_app.ask(g, ok, "Which diseases is the gene PTGS2 associated with?")
    assert res2["no_query"] is True and res2["fallback_from"] is None and usability.answer_label(res2)["kind"] == "hosted"
    assert usability.answer_label(dict(backend="nim", fallback_from="nim", rows=[{"a": 1}]))["kind"] == "fallback"


def test_sec03_the_page_is_told_the_model_did_not_answer_not_that_the_graph_has_no_rows(pharos_client, tmp_path):
    appmod._state["nim"] = hosted(tmp_path, error=PermissionError("HTTP 401: key revoked"))
    for question in ("Which diseases is the gene PTGS2 associated with?", "Which compounds target PTGS2?"):
        r = pharos_client.post("/api/pharos/ask", json={"question": question})
        body = r.json()
        assert r.status_code == 200 and body["rows"] == [] and body["label"]["kind"] == "model_unavailable"
        assert body["label"]["text"] and body["fallback_from"] == "nim"
        s = body["summary"]
        assert s["headline"] == "The hosted model did not answer, so this question was not run on the graph."
        assert "no rows" not in json.dumps(s).lower() and s["model_unavailable"] is True and s["confidence"]["level"] == "low"
        # FIX1 F4 (G1-U-02): a recognised gene leads to its card; "try again" stays as the second way on
        assert s["next_step"].startswith("Open the PTGS2 card") and "try the question again" in s["next_step"]
        assert s["cards"][0]["label"] == "PTGS2" and s["points"][0]["text"] == body["label"]["text"]
    appmod._state["nim"] = NIM(backend="offline")                       # no hosted model configured: the old wording stays
    plain = pharos_client.post("/api/pharos/ask", json={"question": "Which compounds target PTGS2?"}).json()
    assert plain["label"]["kind"] != "model_unavailable"
    assert appmod.model_unavailable_summary(dict(error="summary not available"), {"text": "x"}) == dict(error="summary not available")


# ------------------------------------------------------------------------------------------------ SEC-04
def test_sec04_the_limit_covers_query_and_the_report_exports(trial_client, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "2")
    h = {"X-Forwarded-For": "203.0.113.7"}
    assert trial_client.post("/api/ask", json={"question": TRIAL_QUESTION}, headers=h).status_code == 200
    assert trial_client.post("/api/query", json={"question": "What was the primary outcome of TULIP-1?"}, headers=h).headers["X-Ask-Remaining"] == "0"
    for method, path, kw in (("post", "/api/query", dict(json={"question": "What was the primary outcome of TULIP-1?"})),
                             ("post", "/api/ask", dict(json={"question": TRIAL_QUESTION})),
                             ("post", "/api/pharos/ask", dict(json={"question": "x"})),
                             ("get", "/api/ask_stream", dict(params={"q": TRIAL_QUESTION})),
                             ("get", "/api/report.pdf", dict(params={"q": OTHER_QUESTION})),
                             ("get", "/api/report_subgraph.zip", dict(params={"q": OTHER_QUESTION}))):
        r = getattr(trial_client, method)(path, headers=h, **kw)
        assert r.status_code == 429 and "per visitor per hour" in r.json()["detail"], path
    assert trial_client.get("/api/search", params={"q": "tulip"}, headers=h).status_code == 200     # never limited


def test_sec04_exports_use_the_report_the_server_already_wrote(trial_client, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    h = {"X-Forwarded-For": "203.0.113.8"}
    first = trial_client.post("/api/ask", json={"question": TRIAL_QUESTION}, headers=h)
    assert first.status_code == 200 and appmod.cached_report(TRIAL_QUESTION)["question"] == TRIAL_QUESTION
    runs = []
    monkeypatch.setattr(orchestrator, "ask", lambda *a, **k: runs.append(a) or (_ for _ in ()).throw(AssertionError("re-run")))
    pdf = trial_client.get("/api/report.pdf", params={"q": TRIAL_QUESTION}, headers=h)
    zp = trial_client.get("/api/report_subgraph.zip", params={"q": " " + TRIAL_QUESTION + " "}, headers=h)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and zp.status_code == 200 and zp.content[:2] == b"PK"
    assert runs == [] and "X-Ask-Remaining" not in pdf.headers        # not run again, not counted
    assert trial_client.get("/api/report.pdf", params={"q": OTHER_QUESTION}, headers=h).status_code == 429   # a new question is


def test_sec04_the_stream_keeps_its_report_for_the_exports(trial_client, monkeypatch):
    r = trial_client.get("/api/ask_stream", params={"q": TRIAL_QUESTION})
    assert "event: report" in r.text and appmod.cached_report(TRIAL_QUESTION) is not None
    monkeypatch.setattr(orchestrator, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-run")))
    assert trial_client.get("/api/report.pdf", params={"q": TRIAL_QUESTION}).status_code == 200


def test_sec04_only_the_last_reports_are_kept():
    for i in range(appmod.MAX_REPORTS + 5):
        appmod.remember_report(f"q{i}", dict(question=f"q{i}"))
    assert len(appmod._reports) == appmod.MAX_REPORTS and appmod.cached_report("q0") is None
    assert appmod.cached_report(f"q{appmod.MAX_REPORTS + 4}")["question"] == f"q{appmod.MAX_REPORTS + 4}"
    assert appmod.report_key(" a ") == appmod.report_key("a") != appmod.report_key("b")


def test_sec04_day_cap_counts_all_visitors_together(trial_client, monkeypatch):
    log = type(appmod._day_log)()
    assert [appmod.day_allowed(10.0 + i, 2, log=log)[0] for i in range(3)] == [True, True, False]
    assert appmod.day_allowed(12.0, 2, log=log) == (False, 86400 - 2 + 1)
    assert appmod.day_allowed(86411.0, 2, log=log)[0]                   # the oldest left the 24 hours
    assert appmod.day_allowed(1.0, 2, log=type(log)(), record=False)[0] and appmod.day_allowed(1.0, 0, log=log) == (True, 0)
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_DAY", "2")
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "20")
    q = {"question": "What was the primary outcome of TULIP-1?"}
    codes = [trial_client.post("/api/query", json=q, headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]                                     # three different visitors, one shared budget
    r = trial_client.post("/api/query", json=q, headers={"X-Forwarded-For": "198.51.100.99"})
    assert r.json()["scope"] == "day" and "at most 2 questions a day for all visitors" in r.json()["detail"] and int(r.headers["Retry-After"]) > 0
    assert trial_client.get("/api/pharos/demo_questions").status_code == 200


def test_sec04_a_request_refused_for_its_form_uses_up_no_question(trial_client, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "2")
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_DAY", "3")
    h = {"X-Forwarded-For": "203.0.113.20"}
    for _ in range(5):                                                  # too long: refused, not counted
        r = trial_client.post("/api/query", json={"question": "x" * 600}, headers=h)
        assert r.status_code == 422 and r.headers["X-Ask-Remaining"] == "2"
    assert len(appmod._day_log) == 0 and appmod._ask_log.get("ip:203.0.113.20") == []
    assert trial_client.post("/api/query", json={"question": "What was the primary outcome of TULIP-1?"}, headers=h).headers["X-Ask-Remaining"] == "1"
    assert len(appmod._day_log) == 1
    log, day = {"v": [1.0, 2.0]}, type(appmod._day_log)([1.0, 2.0])
    appmod.uncount("v", 2.0, log=log, day_log=day)
    appmod.uncount("v", 9.0, log=log, day_log=day)                      # not the last count: nothing is removed
    assert log == {"v": [1.0]} and list(day) == [1.0]


def test_sec04_model_slots_turn_a_pile_up_into_a_plain_503(trial_client, monkeypatch):
    monkeypatch.setenv("TMKG_MODEL_CONCURRENCY", "1")
    monkeypatch.setenv("TMKG_MODEL_WAIT_SECONDS", "0")
    with appmod.model_slot():                                           # one request is using the model
        with pytest.raises(appmod.Busy):
            with appmod.model_slot(wait=0):
                pass
        r = trial_client.post("/api/query", json={"question": "What was the primary outcome of TULIP-1?"})
        assert r.status_code == 503 and r.json() == {"detail": appmod.BUSY_TEXT, "error": "busy"} and r.headers["Retry-After"] == "30"
        s = trial_client.get("/api/ask_stream", params={"q": TRIAL_QUESTION})
        assert s.status_code == 200 and "event: error" in s.text and "busy" in s.text and "event: report" not in s.text
        assert trial_client.get("/api/search", params={"q": "tulip"}).status_code == 200     # everything else still answers
    assert trial_client.post("/api/query", json={"question": "What was the primary outcome of TULIP-1?"}).status_code == 200
    monkeypatch.setenv("TMKG_MODEL_CONCURRENCY", "0")                   # 0 = no cap
    with appmod.model_slot(), appmod.model_slot(), appmod.model_slot():
        pass


# ------------------------------------------------------------------------------------------------ SEC-05
def test_sec05_every_answer_carries_the_security_headers(trial_client):
    for path in ("/", "/classic", "/api/landing", "/api/does-not-exist"):
        h = trial_client.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY", path
        assert h["referrer-policy"] == "strict-origin-when-cross-origin" and "camera=()" in h["permissions-policy"]
        csp = h["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert "strict-transport-security" not in h                    # plain HTTP: no HSTS
    assert trial_client.get("/", headers={"X-Forwarded-Proto": "https"}).headers["strict-transport-security"] == "max-age=31536000"
    assert trial_client.post("/api/query", json={"question": "x" * 600}).headers["x-content-type-options"] == "nosniff"   # errors too


def test_sec05_the_pages_load_nothing_the_policy_forbids():
    """The policy allows this origin only: the three pages must not load a script, style, font or image from another site."""
    for name in ("app_page.html", "app_page_v2.html", "app_page_v3.html"):
        html = (ROOT / "pipelines" / name).read_text(encoding="utf-8")
        assert not re.findall(r"<(?:script|link|img|iframe)[^>]+(?:src|href)=[\"']https?://", html), name
        assert not re.search(r"@import\s+url\([\"']?https?://|\beval\(|new Function\(", html), name


def test_sec05_pages_and_json_are_compressed_but_the_event_stream_is_not(trial_client):
    plain = trial_client.get("/", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    c = trial_client
    r = c.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.text == plain.text
    assert c.get("/api/personas", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"
    s = c.get("/api/ask_stream", params={"q": TRIAL_QUESTION}, headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in s.headers and "event: report" in s.text


def caddyfile():
    ud = (AWS / "user_data.sh.tmpl").read_text()
    start = ud.index("cat >> /srv/app/Caddyfile <<CADDY\n")
    return ud[start:ud.index("\nCADDY\n", start)]


def test_sec05_sec06_caddy_compresses_adds_headers_and_limits_bodies():
    cf = caddyfile()
    assert "encode @compressible zstd gzip" in cf and "@compressible not path /api/ask_stream" in cf
    block = cf[cf.index("header {"):cf.index("}", cf.index("header {"))]
    for name in ("Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy", "Content-Security-Policy", "X-Frame-Options"):
        assert name in block, name
    assert f'?Content-Security-Policy "{appmod.CONTENT_SECURITY_POLICY}"' in block       # the same policy as the app's
    assert "frame-ancestors 'none'" in block and "default-src 'self'" in block
    assert re.search(r"request_body @requests \{\s+max_size 256KB", cf) and re.search(r"request_body @answers \{\s+max_size 1MB", cf)
    assert sorted(re.search(r"@answers path (.+)", cf).group(1).split()) == sorted(appmod.ANSWER_BODY_PATHS)
    assert appmod.MAX_BODY_BYTES == 256 * 1024 and appmod.MAX_ANSWER_BODY_BYTES == 1024 * 1024
    assert "$" not in cf.replace("${HOST}", "") and "`" not in cf       # nothing the unquoted here-document would expand


# ------------------------------------------------------------------------------------------------ SEC-06
def test_sec06_large_bodies_are_refused_before_they_are_read(trial_client):
    big = json.dumps({"question": "x", "pad": "y" * (300 * 1024)})
    r = trial_client.post("/api/query", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json() == {"detail": "This request is too large (at most 256 KB).", "error": "too_large"}
    answer = dict(kind="query", result=dict(columns=["a"], rows=[{"a": "y" * 1000} for _ in range(500)], evidence=[]))
    assert 256 * 1024 < len(json.dumps(answer)) < 1024 * 1024
    assert trial_client.post("/api/summarize", json=answer).status_code == 200            # an answer the page holds may be bigger
    huge = dict(answer, pad="y" * (1024 * 1024))
    assert trial_client.post("/api/summarize", json=huge).status_code == 413
    twenty_mb = b'{"kind": "query", "result": {"pad": "' + b"y" * (20 * 1024 * 1024) + b'"}}'
    assert trial_client.post("/api/summarize", content=twenty_mb, headers={"Content-Type": "application/json"}).status_code == 413


def test_sec06_a_body_without_a_length_is_cut_off_at_the_limit(trial_client):
    def chunks():
        yield b'{"question": "'
        for _ in range(40):
            yield b"y" * 10240
        yield b'"}'
    r = trial_client.post("/api/query", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422) and r.json()["error"] == "invalid_request"


def test_sec06_n_is_clamped(pharos_client):
    assert [appmod.clamp_n(n) for n in (-5, 0, 1, 20, 50, 51, 100000000)] == [1, 1, 1, 20, 50, 50, 50]
    seen = []
    g = appmod.answering_graph()
    real = g.predicted_links
    g.predicted_links = lambda node, n=20, task=None: seen.append(n) or real(node, n=n, task=task)
    try:
        for n in (100000000, -5, 7):
            assert pharos_client.get("/api/pharos/predict", params={"node": GENE, "n": n}).status_code == 200
    finally:
        del g.predicted_links
    assert seen == [50, 1, 7]
    assert pharos_client.get(f"/api/pharos/card/{GENE}", params={"n": 100000000}).status_code == 200
    assert pharos_client.get("/api/pharos/search", params={"q": "p" * 201}).status_code == 422


# ------------------------------------------------------------------------------------------------ SEC-07
def test_sec07_the_image_sets_the_small_server_query_budget():
    docker = (AWS / "Dockerfile").read_text()
    for setting in ("TMKG_QUERY_MAX_BINDINGS=20000", "TMKG_QUERY_MAX_STEPS=500000", "TMKG_QUERY_MAX_SECONDS=15", "TMKG_QUERY_CONCURRENCY=2"):
        assert re.search(rf"^ENV .*\b{setting}\b", docker, re.M), setting
    assert "TMKG_QUERY_MAX_SECONDS 60" in querier.run_local.__doc__ and "TMKG_QUERY_MAX_SECONDS 20" not in querier.run_local.__doc__
    assert querier._limits(None, None)[:3] == (2_000_000, 60.0, 100_000)                 # the code defaults are unchanged


def test_sec07_the_budget_of_the_image_stops_a_broad_query(trial_kg, monkeypatch):
    monkeypatch.setenv("TMKG_QUERY_MAX_BINDINGS", "20")
    res = querier.run_local("MATCH (a)-[r]->(b) RETURN a.id AS a, r.id AS evidence LIMIT 500", trial_kg)
    assert res["rows"] == [] and "too broad" in res["problems"][0]


def test_sec07_at_most_two_graph_queries_run_at_the_same_time(trial_kg, monkeypatch):
    cy = "MATCH (p:ClinicalTrialProgram {id:'P19'})-[r:has_trial]->(t:ClinicalTrial) RETURN t.id AS trial, r.id AS evidence LIMIT 5"
    assert querier.run_local(cy, trial_kg)["rows"]                      # no setting: no gate
    monkeypatch.setenv("TMKG_QUERY_CONCURRENCY", "2")
    with querier.query_slot(), querier.query_slot():                    # two queries are running
        with pytest.raises(querier.QueryBusy):
            with querier.query_slot(wait=0):
                pass
        monkeypatch.setenv("TMKG_QUERY_WAIT_SECONDS", "0")
        with pytest.raises(querier.QueryBusy):
            querier.run_local(cy, trial_kg)
    assert querier.run_local(cy, trial_kg)["rows"]                      # the slots were given back
    running, peak, lock = [0], [0], threading.Lock()
    real = querier._run_local

    def slow(*a, **k):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return real(*a, **k)
    monkeypatch.setattr(querier, "_run_local", slow)
    monkeypatch.setenv("TMKG_QUERY_WAIT_SECONDS", "20")
    threads = [threading.Thread(target=querier.run_local, args=(cy, trial_kg)) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 2


def test_sec07_a_busy_graph_is_a_plain_503(trial_client, monkeypatch):
    monkeypatch.setenv("TMKG_QUERY_CONCURRENCY", "1")
    monkeypatch.setenv("TMKG_QUERY_WAIT_SECONDS", "0")
    with querier.query_slot():
        r = trial_client.post("/api/query", json={"question": "What was the primary outcome of TULIP-1?"})
    assert r.status_code == 503 and r.json()["error"] == "busy" and "try again" in r.json()["detail"].lower()
    assert r.headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------------------------------------------ SEC-08
def user_data():
    return (AWS / "user_data.sh.tmpl").read_text()


def test_sec08_logs_are_capped_swap_exists_and_the_metadata_service_is_one_hop():
    ud = user_data()
    runs = re.findall(r"docker run -d (?:.*\\\n)*.*", ud)
    assert len(runs) == 2 and all("--log-opt max-size=20m --log-opt max-file=3" in r and "--restart unless-stopped" in r for r in runs)
    swap = ud[ud.index("if [ ! -f /swapfile ]"):ud.index("\nfi\n", ud.index("if [ ! -f /swapfile ]"))]
    assert "count=2048" in swap and "mkswap /swapfile" in swap and "swapon /swapfile" in swap and "/etc/fstab" in swap
    assert ud.index("/swapfile") < ud.index("docker build") < ud.index("# Front door")
    assert "chmod -R a+rX /srv/data" in ud
    assert "HttpTokens=required,HttpPutResponseHopLimit=1" in (AWS / "deploy_app.sh").read_text()
    docker = (AWS / "Dockerfile").read_text()
    assert "--start-period=60s" in docker and "timeout=4" in docker and "/api/pharos/status" in docker


def test_sec08_the_template_adds_no_word_that_render_would_replace():
    """lib.sh render() replaces these words everywhere in the template; outside their known places they would corrupt it."""
    known = ("s3://BUCKET_NAME", "--region REGION", 'FRONT="TLS_MODE"', 'HOST="PUBLIC_HOST"', 'ACME_MAIL="ACME_EMAIL"')
    for line in user_data().splitlines():
        if line.lstrip().startswith("#"):
            continue
        rest = line
        for k in known:
            rest = rest.replace(k, "")
        assert not re.search(r"BUCKET_NAME|ACCOUNT_ID|REGION|TAG_VALUE|TLS_MODE|PUBLIC_HOST|ACME_EMAIL", rest), line


@pytest.mark.parametrize("health,restarts", [("unhealthy", 1), ("healthy", 0), ("starting", 0), ("", 0)])
def test_sec08_the_heal_timer_restarts_only_an_unhealthy_container(tmp_path, health, restarts):
    ud = user_data()
    script = ud[ud.index("cat > /srv/app/heal.sh <<'HEAL'\n") + len("cat > /srv/app/heal.sh <<'HEAL'\n"):ud.index("\nHEAL\n")]
    (tmp_path / "heal.sh").write_text(script + "\n")
    stub = tmp_path / "bin" / "docker"
    stub.parent.mkdir()
    stub.write_text(f'#!/bin/bash\necho "$@" >> "{tmp_path}/calls"\n'
                    f'if [ "$1" = inspect ]; then [ -n "{health}" ] && echo "{health}" || exit 1; fi\n')
    stub.chmod(0o755)
    r = subprocess.run(["bash", str(tmp_path / "heal.sh")], env={**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}"},
                       capture_output=True, text=True)
    calls = (tmp_path / "calls").read_text().splitlines()
    assert r.returncode == 0 and calls[0].startswith("inspect --format") and calls[0].endswith("tmkg-app")
    assert calls.count("restart tmkg-app") == restarts
    timer = ud[ud.index("cat > /etc/systemd/system/tmkg-heal.timer"):ud.index("systemctl enable --now tmkg-heal.timer")]
    assert "OnUnitActiveSec=1min" in timer and "WantedBy=timers.target" in timer
    assert "ExecStart=/srv/app/heal.sh" in ud and ud.index("tmkg-heal.timer") < ud.index("# Front door")


# ------------------------------------------------------------------------------------------------ SEC-09
def test_sec09_pages_and_status_routes_answer_head(trial_client, pharos_client):
    appmod._state.update(kg=KGIndex())
    for path in ("/", "/classic", "/api/status", "/api/pharos/status", "/DATA_LICENSES.md"):
        r = pharos_client.head(path)
        assert r.status_code == 200 and r.content == b"" and pharos_client.get(path).status_code == 200, path
    assert pharos_client.head("/").headers["content-type"].startswith("text/html")
    for path in ("/api/ask_stream?q=x", "/api/report.pdf?q=x"):         # HEAD never starts a report
        assert pharos_client.head(path).status_code == 405


# ------------------------------------------------------------------------------------------------ SEC-10
class Sub:
    facts = ["[e0000000000000001] a fact"]
    edges = {"e0000000000000001": {}}


def test_sec10_a_malformed_specialist_reply_becomes_a_section_note():
    class BadModel:
        backend = "nim"

        def chat(self, role, messages, schema=None):
            raise ValueError("specialist reply does not match schema: ['$.claims: missing']")
    claims, note = specialists.run_specialist_safe(BadModel(), "failure_diagnostician", "q", Sub())
    assert claims == [] and note == specialists.SECTION_FAILED and "could not be written" in note
    ok, none = specialists.run_specialist_safe(NIM(backend="offline"), "failure_diagnostician", "q", type("S", (), {"facts": [], "edges": {}})())
    assert ok == [] and none is None


def test_sec10_one_failed_specialist_no_longer_kills_the_report(trial_kg, monkeypatch):
    real = specialists.run_specialist

    def flaky(nim, role, question, sg):
        if role == "population_stratifier":
            raise ValueError("population_stratifier reply does not match schema")
        return real(nim, role, question, sg)
    monkeypatch.setattr(specialists, "run_specialist", flaky)
    rep = orchestrator.ask(TRIAL_QUESTION, kg=trial_kg, nim=NIM(backend="offline"))
    by_role = {s["role"]: s for s in rep["sections"]}
    assert by_role["population_stratifier"]["claims"] == [] and by_role["population_stratifier"]["note"] == specialists.SECTION_FAILED
    assert by_role["failure_diagnostician"]["claims"]                   # the other sections are written
    assert specialists.SECTION_FAILED in orchestrator.to_markdown(rep)


def test_sec10_a_failed_stage_gives_a_json_body_and_an_error_event(trial_client, monkeypatch):
    def broken(nim, kg, claim, sg):
        raise RuntimeError("/srv/secret/path.py exploded")
    monkeypatch.setattr(specialists, "verify_claim", broken)
    r = trial_client.post("/api/ask", json={"question": TRIAL_QUESTION})
    assert r.status_code == 503 and r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": appmod.REPORT_FAILED_TEXT, "error": "report_failed"} and "secret" not in r.text
    s = trial_client.get("/api/ask_stream", params={"q": TRIAL_QUESTION})
    events = re.findall(r"event: (\w+)\ndata: (.*)\n\n", s.text)
    assert s.status_code == 200 and events[-1][0] == "error" and [e for e, _ in events[:-1]] and {e for e, _ in events[:-1]} == {"step"}
    assert json.loads(events[-1][1]) == {"detail": appmod.REPORT_FAILED_TEXT, "error": "report_failed"} and "secret" not in s.text
    assert trial_client.get("/api/report.pdf", params={"q": TRIAL_QUESTION}).status_code == 503
    assert appmod.cached_report(TRIAL_QUESTION) is None                 # a failed report is not kept


def test_sec10_an_unexpected_error_is_a_json_body_without_internals(trial_kg, monkeypatch):
    appmod._state.update(kg=trial_kg, nim=NIM(backend="offline"))
    monkeypatch.setattr(appmod, "search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("/srv/secret boom")))
    c = TestClient(appmod.app, raise_server_exceptions=False)
    r = c.get("/api/search", params={"q": "tulip"})
    assert r.status_code == 500 and r.json() == {"detail": appmod.SERVER_ERROR_TEXT, "error": "server_error"}


# ------------------------------------------------------------------------------------------------ SEC-11
def test_sec11_run_note_is_computed_from_who_answered():
    off, hostedb = nimmod.OFFLINE, "nim"
    assert orchestrator.run_note("offline", [("specialist", "m", off, "")]) == "Generated by the offline stand-in (rule-based, no language model)."
    assert orchestrator.run_note("nim", [("specialist", "m", hostedb, "k")] * 3) == "Generated with NVIDIA NIM models listed above."
    mixed = orchestrator.run_note("nim", [("specialist", "m", hostedb, "k")] * 3 + [("verifier", "m", off, "")] * 2)
    assert "2 of the 5 steps of this report were answered by the offline stand-in" in mixed
    allfail = orchestrator.run_note("nim", [("specialist", "m", off, "")] * 4)
    assert allfail.startswith("Generated by the offline stand-in") and "any of the 4 steps" in allfail and "NVIDIA" not in allfail
    assert orchestrator.run_note("nim", []) == "Generated with NVIDIA NIM models listed above."


def pdf_text(data):
    from pypdf import PdfReader
    return re.sub(r"\s+", " ", " ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages))


def test_sec11_exports_say_that_the_stand_in_answered(trial_client, tmp_path):
    appmod._state["nim"] = hosted(tmp_path, error=RuntimeError("HTTP 503"))
    body = trial_client.post("/api/ask", json={"question": TRIAL_QUESTION}).json()
    label = body["label"]
    assert label["kind"] == "fallback" and label["text"].startswith("Fixed-rule answer")
    assert "Generated with NVIDIA NIM models listed above." not in body["run"]["note"]
    assert body["run"]["note"].startswith("Generated by the offline stand-in") and "could not be reached" in body["run"]["note"]
    assert label["text"] in body["markdown"] and body["run"]["note"] in body["markdown"]
    text = pdf_text(trial_client.get("/api/report.pdf", params={"q": TRIAL_QUESTION}).content)
    assert "Fixed-rule answer: the hosted model could not be reached" in text and "Generated by the offline stand-in" in text
    assert "Generated with NVIDIA NIM models listed above" not in text


def test_sec11_a_hosted_report_carries_no_stand_in_label(trial_kg):
    rep = dict(question="q", sections=[], rejected=[], run=dict(note="Generated with NVIDIA NIM models listed above.", backend="nim",
               graph_version={"snapshot": "abc"}, model_calls=[dict(role="specialist", backend="nim")]))
    md = orchestrator.to_markdown(rep)
    assert "stand-in" not in md and "stand-in" not in pdf_text(appmod.report_pdf(rep))
    stored = dict(rep, label=dict(kind="cached", text="Stored answer of the hosted model (recorded earlier, shown without calling the API).", offline=False))
    assert "Stored answer of the hosted model" in orchestrator.to_markdown(stored) and "Stored answer of the hosted model" in pdf_text(appmod.report_pdf(stored))


# ------------------------------------------------------------------------------------------------ SEC-15
def test_sec15_the_image_is_pinned_locked_and_not_root():
    docker = (AWS / "Dockerfile").read_text()
    assert re.search(r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$", docker, re.M)
    assert "COPY requirements.lock.txt ." in docker and "pip install --no-cache-dir -r requirements.lock.txt" in docker
    assert "-r requirements.txt" not in docker
    lines = docker.splitlines()
    user = next(i for i, l in enumerate(lines) if l.startswith("USER "))
    assert lines[user] == "USER tmkg" and user < next(i for i, l in enumerate(lines) if l.startswith("CMD "))
    assert "useradd" in docker and "chown -R tmkg:tmkg /app/data" in docker and "mkdir -p /app/data/snapshots_llm" in docker
    assert re.search(r"caddy:2@sha256:[0-9a-f]{64}", user_data()) and not re.search(r"caddy:2\s*$", user_data(), re.M)


def test_sec15_the_lock_file_pins_every_direct_requirement_at_the_same_version():
    def pins(name):
        out = {}
        for line in (ROOT / name).read_text().splitlines():
            m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)", line.strip())
            if m:
                out[m.group(1).lower().replace("_", "-")] = m.group(2)
        return out
    direct, lock = pins("requirements.txt"), pins("requirements.lock.txt")
    assert len(direct) >= 15 and len(lock) > len(direct)
    assert {k: lock.get(k) for k in direct} == direct
    for needed in ("starlette", "pydantic", "anyio", "h11", "uvicorn", "fastapi"):       # the web stack no longer floats
        assert needed in lock, needed


# ------------------------------------------------------------------------------------------------ SEC-16
def test_sec16_the_interactive_api_pages_are_off(trial_client):
    assert trial_client.get("/docs").status_code == 404 and trial_client.get("/redoc").status_code == 404
    assert trial_client.get("/openapi.json").status_code == 404        # PK1: off with the two pages (app.openapi() still builds)


def test_sec16_a_very_large_file_is_sent_to_the_downloads_folder_on_the_hosted_server(tmp_path, monkeypatch):
    big, small = appmod.BIG_FILE_BYTES + 1, appmod.BIG_FILE_BYTES
    hosted_env = {"TMKG_DOWNLOADS_BASE": "https://example.org/downloads/"}
    assert appmod.big_file_url("edge_verdicts.tsv.gz", big, env=hosted_env) == "https://example.org/downloads/verdicts/"
    assert appmod.big_file_url("edge_verdicts.tsv.gz", small, env=hosted_env) is None     # small enough to serve
    assert appmod.big_file_url("edge_verdicts.tsv.gz", big, env={}) is None               # the Mac: served as before
    assert appmod.big_file_url("predictions.tsv.gz", big, env=hosted_env) is None         # no folder known for it
    folders = {f["path"] for f in json.loads((ROOT / "downloads.json").read_text())["files"]}
    assert set(appmod.BIG_FILE_FOLDERS.values()) <= folders
    f = tmp_path / "data" / "kg" / "pharos" / "edge_verdicts.tsv.gz"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 100)
    monkeypatch.setattr(appmod, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(appmod, "BIG_FILE_BYTES", 10)
    c = TestClient(appmod.app, follow_redirects=False)
    assert c.get("/api/file", params={"name": "edge_verdicts.tsv.gz"}).status_code == 200
    monkeypatch.setenv("TMKG_DOWNLOADS_BASE", "https://example.org/downloads/")
    r = c.get("/api/file", params={"name": "edge_verdicts.tsv.gz"})
    assert r.status_code == 307 and r.headers["location"] == "https://example.org/downloads/verdicts/"


# ------------------------------------------------------------------------------------------------ SEC-17
def test_sec17_eight_first_requests_build_the_graph_once(monkeypatch):
    built = []

    class SlowGraph:
        def __init__(self):
            built.append(1)
            time.sleep(0.05)
    monkeypatch.setattr(appmod, "KGIndex", SlowGraph)
    saved = dict(appmod._state)
    appmod._state.clear()
    try:
        results = []
        threads = [threading.Thread(target=lambda: results.append(appmod.state())) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(built) == 1 and len(results) == 8 and len({id(kg) for kg, _ in results}) == 1
        assert len({id(n) for _, n in results}) == 1 and results[0][1].fallback == "offline"
    finally:
        appmod._state.clear()
        appmod._state.update(saved)


def test_sec17_the_card_index_is_opened_once_and_warm_up_never_fails(index_file, monkeypatch):
    opened = []
    real = pharos_index.CardIndex

    class Counting(real):
        def __init__(self, path):
            opened.append(path)
            time.sleep(0.05)
            super().__init__(path)
    monkeypatch.setattr(pharos_index, "CardIndex", Counting)
    monkeypatch.setenv("TMKG_PHAROS_INDEX", str(index_file))
    appmod._pharos.clear()
    threads = [threading.Thread(target=appmod.card_index) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(opened) == 1 and appmod.card_index() is not None
    appmod._pharos.clear()
    assert appmod.warm() == ["state", "card_index"] and "index" in appmod._pharos
    monkeypatch.setattr(appmod, "state", lambda: (_ for _ in ()).throw(RuntimeError("no graph here")))
    assert appmod.warm() == ["card_index"]                              # a failure is logged, the app still starts
    monkeypatch.setenv("TMKG_WARM", "off")
    assert appmod.warm() == []


def test_sec17_the_limiter_forgets_visitors_whose_hour_has_passed():
    log, swept = {"old": [10.0, 20.0], "recent": [3000.0], "empty": []}, {"at": 0.0}
    assert appmod.forget_old_visitors(3700.0, log=log, swept=swept) == 2 and set(log) == {"recent"}
    log["again"] = [1.0]
    assert appmod.forget_old_visitors(3710.0, log=log, swept=swept) == 0 and "again" in log     # at most one sweep a minute
    assert appmod.forget_old_visitors(3770.0, log=log, swept=swept) == 1 and set(log) == {"recent"}
    assert appmod.ask_allowed("recent", 3771.0, 2, log=log)[0] and len(log["recent"]) == 2      # counting is unchanged


# ------------------------------------------------------------------------------------------------ SEC-18
JSDOM_TEST = '''
import pytest

@pytest.mark.skipif(True, reason="node + jsdom not available")
def test_ui():
    pass

@pytest.mark.skipif(True, reason="needs torch_geometric")
def test_other():
    pass
'''


@pytest.mark.parametrize("required,code,words", [("1", 1, "1 skipped, 1 error"), ("", 0, "2 skipped")])
def test_sec18_under_make_check_a_missing_jsdom_fails_instead_of_skipping(tmp_path, required, code, words):
    (tmp_path / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / "test_ui.py").write_text(JSDOM_TEST)
    env = {k: v for k, v in os.environ.items() if k != "TMKG_REQUIRE_JSDOM"}
    if required:
        env["TMKG_REQUIRE_JSDOM"] = required
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(tmp_path), str(tmp_path)],
                       capture_output=True, text=True, env=env, cwd=tmp_path)
    assert r.returncode == code and words in r.stdout, r.stdout
    if required:
        assert "jsdom is required" in r.stdout and "make jsdom" in r.stdout


def test_sec18_make_check_requires_jsdom_and_installs_it_before_the_tests():
    make = (ROOT / "Makefile").read_text()
    assert re.search(r"^tests: jsdom$", make, re.M) and re.search(r"TMKG_REQUIRE_JSDOM=1 \$\(PY\) -m pytest", make)


def test_byo_routes_keep_their_own_body_cap_and_count_a_refused_paste():
    """Merge of PK1 with PK8: the app-wide body limit skips the two source-card routes (they cap the body while reading it),
    and a 400 on the draft still counts toward the visitor limit while a 400 elsewhere is given back."""
    import routes_byo
    assert {routes_byo.DRAFT_PATH, routes_byo.BUILD_PATH} <= appmod.OWN_BODY_LIMIT_PATHS
    assert 400 in appmod.COUNTED_REFUSALS[routes_byo.DRAFT_PATH]
    assert "/api/ask" not in appmod.COUNTED_REFUSALS and "/api/ask" not in appmod.OWN_BODY_LIMIT_PATHS
