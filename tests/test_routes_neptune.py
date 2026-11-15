"""PK15: Amazon Neptune beside the local graph (pipelines/routes_neptune.py, neptune_backend.py, the ask route in app.py).
Fixtures only: the host's status folder is a temp dir, Neptune's answer is the local run of the same query; no network.

  B1 the state from the host's status file (every branch), the wake request (atomic, at most one write per 60 s, not a question),
     GET /api/neptune/status (loaded counts from the shipped manifest), backend_options by state;
  B2 asking on Neptune never fails the user: asleep / waking / unavailable / error / slow -> the local answer with a note; the live
     runner reaches the cluster every time and stores nothing; the snapshot path still replays (tests, parity suite);
  B3 the same validated query on both graphs (the model is called once; a tested question uses its fixed query on both), the match
     numbers and the sentence printed as they are;
  B4 the server half of the page: via_cloudfront and the "Runs on" line in /api/landing, the answer label naming the graph;
  B5 the Numbers sentence from outputs/neptune/live_parity.json (None when absent or the counts differ), in /api/results.
"""
import datetime
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import app as appmod  # noqa: E402
import neptune_backend as nb  # noqa: E402
import numbers_page  # noqa: E402
import pharos_app  # noqa: E402
import querier  # noqa: E402
import routes_neptune as RN  # noqa: E402
import usability  # noqa: E402
from nim import NIM  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from snapshot import Snapshot  # noqa: E402

from fixtures.pharos_fixture import build  # noqa: E402

DEMO = pharos_app.DEMO_QUESTIONS[0]
NOW = 1_800_000_000.0                                     # a fixed time for the pure functions


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def host(d, state, t=None):
    """Writes the host's status.json into folder d (as PK14 A2 does every 20 s). Returns d."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps({"state": state, "checked_at": iso(time.time() if t is None else t)}))
    return d


def env_for(d, **extra):
    return dict({"NEPTUNE_ENDPOINT": "c.example", "NEPTUNE_STATUS_DIR": str(d)}, **extra)


@pytest.fixture(autouse=True)
def fresh_wake_window():
    RN._wake["at"] = None
    RN._wake["written"].clear()
    RN._wake["day"].clear()
    yield
    RN._wake["at"] = None


# ------------------------------------------------------------------------------------------------ B1: the state, every branch
def test_state_not_configured_without_an_endpoint(tmp_path):
    host(tmp_path, "available", NOW)
    assert RN.neptune_state({}, NOW) == "not_configured"
    assert RN.neptune_state({"NEPTUNE_ENDPOINT": "  ", "NEPTUNE_STATUS_DIR": str(tmp_path)}, NOW) == "not_configured"


@pytest.mark.parametrize("hoststate,want", [("available", "available"), ("stopped", "asleep"), ("starting", "waking"),
                                            ("stopping", "unavailable"), ("unknown", "unavailable"), ("", "unavailable"),
                                            ("AVAILABLE", "available")])
def test_state_follows_the_host(tmp_path, hoststate, want):
    host(tmp_path, hoststate, NOW - 10)
    assert RN.neptune_state(env_for(tmp_path), NOW) == want


def test_a_wake_request_waiting_means_waking_unless_available(tmp_path):
    host(tmp_path, "stopped", NOW)
    (tmp_path / "wake.request").write_text("{}")
    assert RN.neptune_state(env_for(tmp_path), NOW) == "waking"
    host(tmp_path, "stopping", NOW)
    assert RN.neptune_state(env_for(tmp_path), NOW) == "waking"
    host(tmp_path, "available", NOW)                          # a request file left behind does not hide an awake cluster
    assert RN.neptune_state(env_for(tmp_path), NOW) == "available"


def test_state_unavailable_when_the_file_cannot_be_believed(tmp_path):
    env = env_for(tmp_path)
    assert RN.neptune_state(env, NOW) == "unavailable"                                  # no folder, no file
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "status.json").write_text("{not json")
    assert RN.neptune_state(env, NOW) == "unavailable"                                  # unreadable
    (tmp_path / "status.json").write_text("[1, 2]")
    assert RN.neptune_state(env, NOW) == "unavailable"                                  # not an object
    (tmp_path / "status.json").write_text(json.dumps({"state": "available"}))
    assert RN.neptune_state(env, NOW) == "unavailable"                                  # no checked_at
    (tmp_path / "status.json").write_text(json.dumps({"state": "available", "checked_at": "yesterday"}))
    assert RN.neptune_state(env, NOW) == "unavailable"                                  # unreadable checked_at
    host(tmp_path, "available", NOW - 91)
    assert RN.neptune_state(env, NOW) == "unavailable"                                  # older than 90 s
    host(tmp_path, "available", NOW - 90)
    assert RN.neptune_state(env, NOW) == "available"                                    # 90 s is still fresh
    host(tmp_path, "available", NOW + 600)
    assert RN.neptune_state(env, NOW) == "unavailable"                                  # a clock far in the future


def test_checked_at_with_an_offset_or_without_a_zone(tmp_path):
    (tmp_path / "status.json").write_text(json.dumps({"state": "available", "checked_at": "2027-01-15T08:00:00+00:00"}))
    t = datetime.datetime(2027, 1, 15, 8, 0, 30, tzinfo=datetime.timezone.utc).timestamp()
    assert RN.neptune_state(env_for(tmp_path), t) == "available"
    (tmp_path / "status.json").write_text(json.dumps({"state": "available", "checked_at": "2027-01-15T08:00:00"}))
    assert RN.neptune_state(env_for(tmp_path), t) == "available"                       # no zone = UTC


def test_status_dir_defaults_to_slash_neptune():
    assert RN.status_dir({}) == Path("/neptune") and RN.status_dir({"NEPTUNE_STATUS_DIR": "/x"}) == Path("/x")


def test_wake_minutes_and_words():
    assert RN.wake_minutes({}) is None and RN.wake_words({}) == "a few minutes"
    assert RN.wake_minutes({"NEPTUNE_WAKE_MINUTES": "6"}) == 6 and RN.wake_words({"NEPTUNE_WAKE_MINUTES": "6"}) == "about 6 minutes"
    assert RN.wake_minutes({"NEPTUNE_WAKE_MINUTES": "4.2"}) == 5                      # rounded up
    assert RN.wake_words({"NEPTUNE_WAKE_MINUTES": "1"}) == "about 1 minute"
    assert RN.wake_minutes({"NEPTUNE_WAKE_MINUTES": "soon"}) is None and RN.wake_minutes({"NEPTUNE_WAKE_MINUTES": "0"}) is None


def test_every_state_has_a_user_sentence_without_operations_words():
    for st in RN.STATES:
        t = RN.state_sentence(st, {})
        assert (t == "") == (st == "available")
        for word in ("cluster", "endpoint", "instance", "serverless", "NCU", "NEPTUNE_", "status.json"):
            assert word.lower() not in t.lower()
    assert RN.state_sentence("asleep") == ("This answer comes from this server's copy of the graph. The same question can also "
                                           "run on Amazon Neptune (asleep to save cost; waking takes a few minutes).")
    for note in RN.ASK_NOTES.values():
        assert "cluster" not in note and "endpoint" not in note and ("this server's graph" in note or "this server's copy of the graph" in note)


# ------------------------------------------------------------------------------------------------------- B1: waking it
def test_wake_writes_the_request_once_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(RN, "WAKE_HOLD", 0)          # the 60 s write window alone (the 90 s hold: the next test)
    env = env_for(host(tmp_path, "stopped"))
    assert RN.request_wake(env, clock=1000.0) == "waking"
    req = tmp_path / "wake.request"
    assert req.exists() and "requested_at" in json.loads(req.read_text())
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []        # renamed into place, no temp left
    req.unlink()                                                                         # the host took it and started
    assert RN.request_wake(env, clock=1059.0) == "waking" and not req.exists()           # inside 60 s: no second write
    assert RN.request_wake(env, clock=1061.0) == "waking" and req.exists()               # after 60 s: written again


def test_waking_is_held_for_90_s_after_this_server_wrote_a_wake(tmp_path):
    """S17 item 2: the host deletes the request when it starts the cluster, and Amazon Neptune can still report 'stopped' for a
    few seconds; the page must not flicker back to 'asleep' (and offer the button again) in that moment."""
    d = host(tmp_path, "stopped")
    env = env_for(d)
    assert RN.neptune_state(env) == "asleep"
    assert RN.request_wake(env, clock=1000.0) == "waking"
    (d / "wake.request").unlink()                                                        # taken by the host
    assert RN.neptune_state(env) == "waking"                                             # still 'stopped' on the host
    RN._wake["written"][str(d)] -= RN.WAKE_HOLD + 1                                      # 91 s later
    assert RN.neptune_state(env) == "asleep"
    other = host(tmp_path / "other", "stopped")                                          # the hold is per folder
    assert RN.neptune_state(env_for(other)) == "asleep"


def test_wakes_per_day_are_capped_for_the_whole_server(tmp_path, monkeypatch):
    """FIX2 F4: at most TMKG_NEPTUNE_WAKES_PER_DAY wake requests are written per 24 h, then 'capped'."""
    monkeypatch.setattr(RN, "WAKE_HOLD", 0)
    d = host(tmp_path, "stopped")
    env = env_for(d, TMKG_NEPTUNE_WAKES_PER_DAY="2")
    assert RN.wakes_per_day(env) == 2 and RN.wakes_per_day({}) == RN.WAKES_PER_DAY == 6
    assert RN.wakes_per_day({"TMKG_NEPTUNE_WAKES_PER_DAY": "x"}) == 6
    for i, clock in enumerate((0.0, 100.0)):
        assert RN.request_wake(env, clock=clock) == "waking"
        (d / "wake.request").unlink()
    assert RN.request_wake(env, clock=200.0) == "capped" and not (d / "wake.request").exists()
    RN._wake["day"].popleft()
    RN._wake["day"].appendleft(0.0)                                                      # the oldest is a day old
    assert RN.request_wake(env, clock=300.0) == "waking"


@pytest.mark.parametrize("hoststate,want", [("available", "available"), ("starting", "waking"), ("stopping", "unavailable")])
def test_wake_changes_nothing_unless_asleep(tmp_path, hoststate, want):
    env = env_for(host(tmp_path, hoststate))
    assert RN.request_wake(env, clock=5000.0) == want and not (tmp_path / "wake.request").exists()
    assert RN.request_wake({}, clock=5000.0) == "not_configured"


def test_wake_in_a_folder_that_cannot_be_written(tmp_path, monkeypatch):
    env = env_for(host(tmp_path, "stopped"))
    real = Path.write_text

    def refuse(self, *a, **k):
        if self.name.endswith(".tmp"):
            raise PermissionError("read-only")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", refuse)
    assert RN.request_wake(env, clock=1.0) == "unavailable" and not (tmp_path / "wake.request").exists()


def test_manifest_counts_and_the_status_payload(tmp_path):
    root = tmp_path / "repo"
    (root / RN.MANIFEST).parent.mkdir(parents=True)
    (root / RN.MANIFEST).write_text(json.dumps({"counts": {"nodes": 11, "edges": 29}}))
    assert RN.manifest_counts([tmp_path / "nothing", root]) == {"nodes": 11, "links": 29}
    assert RN.manifest_counts([tmp_path / "nothing"]) is None
    d = host(tmp_path / "st", "stopped")
    st = RN.status(env_for(d, NEPTUNE_WAKE_MINUTES="3"), roots=[root])
    assert st == {"state": "asleep", "wake_minutes": 3, "wake_words": "about 3 minutes", "loaded_from": "manifest",
                  "sentence": RN.state_sentence("asleep", {"NEPTUNE_WAKE_MINUTES": "3"}), "loaded": {"nodes": 11, "links": 29}}
    assert "loaded" not in RN.status({}, roots=[root])                                  # not configured: no counts


@pytest.mark.parametrize("hoststate", ["available", "stopped", "starting", "stopping"])
def test_loaded_comes_from_live_parity_in_every_state_when_no_manifest_is_shipped(tmp_path, hoststate):
    """S17 item 1: the export manifest is not in git, so on the server `loaded` was missing in every state; the measured
    counts of live_parity.json (when equal to its own manifest counts) stand in, and say so."""
    root = tmp_path / "repo"
    parity(root)
    st = RN.status(env_for(host(tmp_path / "st", hoststate)), roots=[root])
    assert st["loaded"] == {"nodes": 1326, "links": 29295} and st["loaded_from"] == "live_parity"
    parity(root, man={"nodes": 1, "links": 2})                                           # the file's own counts disagree
    assert "loaded" not in RN.status(env_for(host(tmp_path / "st", hoststate)), roots=[root])
    assert RN.wake_minutes({}, [root]) is None                                           # no wake_minutes in this file
    p = root / RN.LIVE_PARITY
    p.write_text(json.dumps(dict(json.loads(p.read_text()), wake_minutes=11)))
    assert RN.wake_minutes({}, [root]) == 11 and RN.wake_words({"NEPTUNE_WAKE_MINUTES": "4"}, [root]) == "about 4 minutes"


def test_numbers_row_says_the_ready_questions_and_the_layer_split(tmp_path):
    """FIX2 F5 (G2-ES-03): the Numbers sentence must not read better than the per-answer one: the ready questions with
    identical evidence, the search index holding more rows, and the links as kept links plus trial-layer links."""
    p = parity(tmp_path, nodes=10, links=105)
    d = json.loads(p.read_text())
    d["answers"] = [{"id": "pharos_demo_1", "links_local": 215, "links_neptune": 50, "links_identical": 23},
                    {"id": "pharos_demo_2", "links_local": 8, "links_neptune": 8, "links_identical": 8},
                    {"id": "q_x", "links_local": 3, "links_neptune": 3, "links_identical": 3}]
    p.write_text(json.dumps(d))
    (tmp_path / "outputs" / "g3").mkdir(parents=True)
    (tmp_path / "outputs" / "g3" / "summary.md").write_text("# G3\n\n9 nodes, 100 edges; 3 edges removed by pruning.\n")
    (tmp_path / "outputs" / "kg_edges.csv").write_text("id\n" + "".join(f"e{i}\n" for i in range(5)))
    s = RN.numbers_row(tmp_path)
    assert "The 105 links are the 100 kept links of the pruned graph plus the 5 links of the trial layer." in s
    assert "Of the 2 ready questions, 1 returns identical evidence links there;" in s
    assert "holds more rows than the download" in s
    (tmp_path / "outputs" / "kg_edges.csv").write_text("id\ne1\n")                    # does not add up: no split sentence
    assert "kept links of the pruned graph" not in RN.numbers_row(tmp_path)
    (tmp_path / RN.MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / RN.MANIFEST).write_text(json.dumps({"counts": {"nodes": 10, "edges": 105},
                                                    "kept_by_layer": {"pharos": 60, "dto": 40, "trialmindkg": 5}}))
    assert "the 100 kept links of the pruned graph plus the 5 links of the trial layer" in RN.numbers_row(tmp_path)
    for word in ("G3", "G4", "manifest", ".json", "pharos_demo"):
        assert word not in s


def test_committed_live_parity_sentence():
    s = RN.numbers_row(RN.ROOT)
    if s is None:                                                     # an untracked export manifest that disagrees, on a Mac
        return
    lp = json.loads((RN.ROOT / RN.LIVE_PARITY).read_text())
    assert f"{lp['loaded']['links']:,} links" in s and "ready questions" in s


def test_backend_options_by_state(tmp_path):
    d = tmp_path / "st"
    for hoststate, avail, st in (("available", True, "available"), ("stopped", False, "asleep"), ("starting", False, "waking"),
                                 ("weird", False, "unavailable")):
        host(d, hoststate)
        o = nb.backend_options(env_for(d))
        assert o[0] == {"id": "local", "label": "This server's graph", "available": True, "reason": ""}
        assert o[1]["available"] is avail and o[1]["state"] == st and o[1]["reason"] == RN.state_sentence(st, env_for(d))


# ------------------------------------------------------------------------------------------------- B2/B3: pure functions
def test_compare_and_the_sentence():
    m = RN.compare({"rows": [1, 2], "evidence": ["a", "b", "b"]}, {"rows": [1, 2], "evidence": ["b", "a"]})
    assert m == {"rows_local": 2, "rows_neptune": 2, "links_local": 2, "links_neptune": 2, "links_identical": 2}
    assert RN.match_sentence(m, 1234) == "Same question on Amazon Neptune: 2 rows in 1.2 s; 2 of 2 evidence links identical."
    m = RN.compare({"rows": [1], "evidence": ["a", "b", "c"]}, {"rows": [1], "evidence": ["a", "b"]})
    assert RN.match_sentence(m, 40) == ("Same question on Amazon Neptune: 1 row in under 0.1 s; 2 of 3 evidence links identical "
                                        "(Amazon Neptune returned 2).")
    m = RN.compare({"rows": [1], "evidence": ["a"]}, {"rows": [1, 2], "evidence": ["a", "z"]})
    s = RN.match_sentence(m, 500)
    assert "1 of 1 evidence link identical (Amazon Neptune returned 2)" in s           # never "1 of 1" alone when the sets differ
    assert RN.match_sentence(RN.compare({}, {}), 100).endswith("0 rows in 0.1 s; no evidence links on either graph.")


def answer(cypher="MATCH (n) RETURN n.id AS id LIMIT 1", **k):
    return dict(cypher=cypher, rows=[{"id": "x"}], evidence=["e1", "e2"], problems=[], **k)


def test_ask_on_neptune_only_when_available(tmp_path):
    calls = []
    run = lambda cy: calls.append(cy) or {"columns": ["id"], "rows": [{"id": "x"}], "evidence": ["e1", "e2"]}  # noqa: E731
    for hoststate, st in (("stopped", "asleep"), ("starting", "waking"), ("weird", "unavailable")):
        nep, remote = RN.ask_on_neptune(answer(), run, env=env_for(host(tmp_path, hoststate)))
        assert remote is None and nep["asked"] is True and nep["answered"] is False and nep["state"] == st and nep["note"]
    nep, remote = RN.ask_on_neptune(answer(), run, env={})
    assert nep["state"] == "not_configured" and remote is None
    assert calls == []                                                                   # a sleeping Neptune is never contacted
    env = env_for(host(tmp_path, "available"))
    nep, remote = RN.ask_on_neptune(answer(), run, env=env)
    assert nep["answered"] is True and isinstance(nep["ms"], int) and nep["match"]["links_identical"] == 2 and remote["rows"]
    assert nep["sentence"].startswith("Same question on Amazon Neptune: 1 row in ") and calls == [answer()["cypher"]]


def test_ask_on_neptune_skips_answers_without_a_runnable_query(tmp_path):
    env = env_for(host(tmp_path, "available"))
    boom = lambda cy: pytest.fail("must not be called")  # noqa: E731
    for a in (answer(cypher=""), answer(abstained=True), dict(answer(), problems=["too broad"])):
        nep, remote = RN.ask_on_neptune(a, boom, env=env)
        assert remote is None and nep["answered"] is False and nep["state"] == "available" and "not run on Amazon Neptune" in nep["note"]


def test_ask_on_neptune_errors_and_timeouts_give_a_note_never_an_exception(tmp_path):
    env = env_for(host(tmp_path, "available"))

    def fail(cy):
        raise nb.NeptuneError("Neptune query failed: HTTP 500 secret detail")
    nep, remote = RN.ask_on_neptune(answer(), fail, env=env)
    assert remote is None and nep["state"] == "unavailable" and "500" not in nep["note"] and "secret" not in nep["note"]
    nep, remote = RN.ask_on_neptune(answer(), lambda cy: 1 / 0, env=env)
    assert remote is None and nep["answered"] is False
    nep, remote = RN.ask_on_neptune(answer(), lambda cy: "not a dict", env=env)
    assert remote is None and nep["answered"] is False
    t0 = time.monotonic()
    nep, remote = RN.ask_on_neptune(answer(), lambda cy: time.sleep(1.0) or {"rows": []}, env=env, seconds=0.2)
    assert time.monotonic() - t0 < 0.9 and remote is None and "did not answer within 0.2 s" in nep["note"]


def test_ask_on_neptune_ranks_rows_and_survives_a_failing_rank(tmp_path):
    env = env_for(host(tmp_path, "available"))
    run = lambda cy: {"columns": ["id"], "rows": [{"id": "b"}, {"id": "a"}], "evidence": ["e1"]}  # noqa: E731
    nep, remote = RN.ask_on_neptune(answer(), run, env=env, rank=lambda r: dict(r, rows=sorted(r["rows"], key=lambda x: x["id"])))
    assert [r["id"] for r in remote["rows"]] == ["a", "b"]
    nep, remote = RN.ask_on_neptune(answer(), run, env=env, rank=lambda r: 1 / 0)
    assert [r["id"] for r in remote["rows"]] == ["b", "a"] and nep["answered"] is True


# -------------------------------------------------------------------------------------------------- B2: the live runner
def test_the_live_runner_reaches_the_cluster_every_time_and_stores_nothing(tmp_path):
    snap = Snapshot(tmp_path / "snap", mode="live", sleep=0)
    seen = []

    def fake_http(method, url, params, body, headers=None, store_client_errors=True, budget=None):
        seen.append((method, url, body, store_client_errors, budget))
        return {"results": [{"id": "e1", "evidence": "e1"}]}
    snap._http = fake_http
    r = nb.NeptuneRunner("c.example", snap=snap, live=True)
    cy = "MATCH (g:Gene)-[r:has_gene_product]->(p) RETURN p.id AS id, r.id AS evidence LIMIT 1"
    a, b = r.run(cy), r.run(cy)
    assert a == b and a["evidence"] == ["e1"] and len(seen) == 2                       # live: no replay
    m, url, body, store_errors, budget = seen[0]
    assert m == "POST" and url == "https://c.example:8182/openCypher" and body == {"query": cy} and store_errors is False
    assert budget.attempts == 1 and budget.timeout == 20.0 and budget.rate_limit_waits == 0 and budget.remaining() <= 20.0
    assert not (tmp_path / "snap").exists()                                              # nothing written to disk
    snap._http = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("POST failed: HTTP 503"))
    with pytest.raises(nb.NeptuneError, match="503"):
        r.run(cy)


def test_the_snapshot_runner_still_replays(tmp_path):
    snap = Snapshot(tmp_path / "snap", mode="live", sleep=0)
    seen = []
    snap._http = lambda m, u, p, b, headers=None, store_client_errors=True: seen.append(1) or {"results": [{"id": "x"}]}
    r = nb.NeptuneRunner("c.example", snap=snap)                                         # the tests and the parity suite
    cy = "MATCH (n:Gene) RETURN n.id AS id LIMIT 1"
    assert r.run(cy) == r.run(cy) and len(seen) == 1 and list((tmp_path / "snap" / "neptune").glob("*.json.gz"))


# --------------------------------------------------------------------------------------------------------- the app routes
@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.fixture
def client(repo, monkeypatch, tmp_path):
    appmod._pharos.update(g=PharosGraph(root=repo), index=None)
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    for k in ("NEPTUNE_ENDPOINT", "NEPTUNE_STATUS_DIR", "NEPTUNE_WAKE_MINUTES", "TMKG_GRAPH_BACKEND", "TMKG_ASK_LIMIT_PER_HOUR",
              "TMKG_ASK_LIMIT_PER_DAY"):
        monkeypatch.delenv(k, raising=False)
    return TestClient(appmod.app)


@pytest.fixture
def neptune(monkeypatch, tmp_path):
    """Amazon Neptune configured and available; its live answer is the local run of the same query (what a loaded copy returns).
    Returns the folder (write host(d, ...) to change the state) and the list of queries Neptune received."""
    d = host(tmp_path / "status", "available")
    monkeypatch.setenv("NEPTUNE_ENDPOINT", "c.example")
    monkeypatch.setenv("NEPTUNE_STATUS_DIR", str(d))
    sent = []

    def live(self, cypher):
        sent.append(cypher)
        return {"results": querier.run_local(cypher, appmod.answering_graph())["rows"]}
    monkeypatch.setattr(nb.NeptuneRunner, "_post_live", live)
    return d, sent


def test_status_and_wake_routes(client, neptune, monkeypatch):
    d, _ = neptune
    assert client.get("/api/neptune/status").json()["state"] == "available"
    assert client.post("/api/neptune/wake").json()["state"] == "available" and not (d / "wake.request").exists()
    host(d, "stopped")
    monkeypatch.setenv("NEPTUNE_WAKE_MINUTES", "5")
    j = client.post("/api/neptune/wake").json()
    assert j == {"state": "waking", "wake_minutes": 5, "wake_words": "about 5 minutes", "sentence": RN.state_sentence("waking")}
    assert (d / "wake.request").exists() and client.get("/api/neptune/status").json()["state"] == "waking"


def test_wake_is_not_a_question(client, neptune, monkeypatch):
    d, _ = neptune
    host(d, "stopped")
    assert "/api/neptune/wake" not in appmod.ASK_PATHS and "/api/neptune/status" not in appmod.ASK_PATHS
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    appmod._ask_log.clear()
    for _ in range(4):
        assert client.post("/api/neptune/wake").status_code == 200
        assert client.get("/api/neptune/status").status_code == 200
    assert client.post("/api/pharos/ask", json={"question": DEMO["question"]}).status_code == 200    # the one question is still free
    appmod._ask_log.clear()


def test_asking_on_neptune_returns_neptunes_answer_and_the_comparison(client, neptune):
    _, sent = neptune
    local = client.post("/api/pharos/ask", json={"question": DEMO["question"]}).json()
    r = client.post("/api/pharos/ask", json={"question": DEMO["question"], "backend": "neptune"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["graph_backend"] == "neptune" and sent == [DEMO["cypher"]]                 # a tested question: its fixed query
    assert j["rows"] == local["rows"] and j["evidence"] == local["evidence"] and local["evidence"]
    n = j["neptune"]
    assert n["asked"] and n["answered"] and isinstance(n["ms"], int)
    assert n["match"] == {"rows_local": len(local["rows"]), "rows_neptune": len(j["rows"]), "links_local": len(set(local["evidence"])),
                          "links_neptune": len(set(j["evidence"])), "links_identical": len(set(local["evidence"]))}
    assert j["label"]["graph"] == "Amazon Neptune" and j["label"]["graph_text"] == "The rows come from Amazon Neptune."
    assert "from_index" not in j and "neptune" not in local and "graph" not in local["label"]


def test_the_model_writes_the_query_once(client, neptune, tmp_path):
    _, sent = neptune
    snap = Snapshot(tmp_path / "llm", mode="live", sleep=0)
    model_calls = []
    cy = DEMO["cypher"]

    def fake(m, u, p, b, headers=None, store_client_errors=True, budget=None):
        model_calls.append(u)
        return {"choices": [{"message": {"content": json.dumps({"cypher": cy, "explanation": "stub"})}}], "usage": {}}
    snap._http = fake
    n = NIM(backend="nim", snap=snap)
    n.key = "nvapi-stub"
    appmod._state["nim"] = n
    q = "Which diseases does PTGS2 relate to, and who says so?"                          # not a tested question: the model writes it
    j = client.post("/api/pharos/ask", json={"question": q, "backend": "neptune"}).json()
    assert j["graph_backend"] == "neptune" and sent == [j["cypher"]] and len(model_calls) == 1


@pytest.mark.parametrize("hoststate,st", [("stopped", "asleep"), ("starting", "waking"), ("stopping", "unavailable")])
def test_a_sleeping_or_unreachable_neptune_gives_the_local_answer(client, neptune, hoststate, st):
    d, sent = neptune
    host(d, hoststate)
    local = client.post("/api/pharos/ask", json={"question": DEMO["question"]}).json()
    r = client.post("/api/pharos/ask", json={"question": DEMO["question"], "backend": "neptune"})
    assert r.status_code == 200
    j = r.json()
    assert j["graph_backend"] == "local" and j["rows"] == local["rows"] and sent == []
    assert j["neptune"] == {"asked": True, "answered": False, "state": st, "note": j["neptune"]["note"]} and j["neptune"]["note"]
    assert j["label"]["graph"] == "this server's graph"


def test_a_stale_status_file_is_never_believed(client, neptune):
    d, sent = neptune
    host(d, "available", time.time() - 600)
    j = client.post("/api/pharos/ask", json={"question": DEMO["question"], "backend": "neptune"}).json()
    assert j["graph_backend"] == "local" and j["neptune"]["state"] == "unavailable" and sent == []


def test_a_neptune_error_or_a_slow_answer_gives_the_local_answer(client, neptune, monkeypatch):
    monkeypatch.setattr(nb.NeptuneRunner, "_post_live", lambda self, cy: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    j = client.post("/api/pharos/ask", json={"question": DEMO["question"], "backend": "neptune"}).json()
    assert j["graph_backend"] == "local" and j["neptune"]["answered"] is False and j["rows"]
    monkeypatch.setattr(RN, "ASK_SECONDS", 0.2)
    monkeypatch.setattr(nb.NeptuneRunner, "_post_live", lambda self, cy: time.sleep(1.0) or {"results": []})
    t0 = time.monotonic()
    j = client.post("/api/pharos/ask", json={"question": DEMO["question"], "backend": "neptune"}).json()
    assert time.monotonic() - t0 < 0.9 and j["graph_backend"] == "local" and "did not answer within" in j["neptune"]["note"]


def test_the_app_uses_the_live_runner(client, neptune, monkeypatch):
    made = []
    real = nb.NeptuneRunner.__init__

    def spy(self, *a, **k):
        made.append(k.get("live"))
        real(self, *a, **k)
    monkeypatch.setattr(nb.NeptuneRunner, "__init__", spy)
    client.post("/api/pharos/ask", json={"question": DEMO["question"], "backend": "neptune"})
    assert made == [True]


def test_an_unknown_backend_is_still_refused(client):
    assert client.post("/api/pharos/ask", json={"question": "x", "backend": "oracle"}).status_code == 400


# ------------------------------------------------------------------------------------------- B4: landing, label, Runs on
def test_via_cloudfront_and_runs_on():
    assert RN.via_cloudfront({"via": "1.1 abc123.cloudfront.net (CloudFront)"}) is True
    assert RN.via_cloudfront({"via": "1.1 Caddy"}) is False and RN.via_cloudfront({}) is False
    assert RN.runs_on(False, "not_configured", False) == {"models": False, "neptune": False, "cloudfront": False, "aws": False, "text": ""}
    assert RN.runs_on(True, "available", True)["text"] == ("Models: NVIDIA NIM. Graph database: Amazon Neptune. "
                                                          "Delivery: Amazon CloudFront. Hosted on AWS.")
    assert RN.runs_on(True, "not_configured", False)["text"] == "Models: NVIDIA NIM."
    assert RN.runs_on(False, "asleep", False)["text"] == ("Graph database: Amazon Neptune (the full graph; asleep, woken on request). "
                                                         "Hosted on AWS.")
    assert RN.runs_on(False, "waking", False)["text"] == "Graph database: Amazon Neptune. Hosted on AWS."
    assert RN.runs_on(False, "not_configured", True)["text"] == "Delivery: Amazon CloudFront. Hosted on AWS."


def test_landing_carries_the_via_boolean_and_the_runs_on_line(client, neptune):
    j = client.get("/api/landing").json()
    assert j["via_cloudfront"] is False and j["runs_on"]["text"] == "Graph database: Amazon Neptune. Hosted on AWS."
    j = client.get("/api/landing", headers={"Via": "1.1 d1.cloudfront.net (CloudFront)"}).json()
    assert j["via_cloudfront"] is True and "Delivery: Amazon CloudFront." in j["runs_on"]["text"]
    assert j["backends"][1]["state"] == "available"


def test_landing_without_neptune_or_cloudfront_has_an_empty_runs_on(client):
    j = client.get("/api/landing").json()
    assert j["via_cloudfront"] is False and j["runs_on"]["text"] == "" and j["backends"][1]["state"] == "not_configured"


def test_the_label_names_the_graph_only_when_neptune_was_asked():
    base = dict(backend="nim", rows=[{"a": 1}])
    assert usability.answer_label(base) == usability._model_answer_label(base) and "graph" not in usability.answer_label(base)
    on = usability.answer_label(dict(base, graph_backend="neptune", neptune={"asked": True, "answered": True}))
    off = usability.answer_label(dict(base, graph_backend="local", neptune={"asked": True, "answered": False}))
    assert on["graph"] == "Amazon Neptune" and off["graph"] == "this server's graph" and on["kind"] == off["kind"] == "hosted"
    assert "did not answer" in off["graph_text"]


# ----------------------------------------------------------------------------------------------- B5: the Numbers sentence
def parity(root, nodes=1326, links=29295, pinned=((True,), (True,), (False,), (True,)), counts_equal=True, man=None):
    p = Path(root) / RN.LIVE_PARITY
    p.parent.mkdir(parents=True, exist_ok=True)
    man = man or {"nodes": nodes, "links": links}
    p.write_text(json.dumps({"date": "2026-11-14", "loaded": {"nodes": nodes, "links": links}, "manifest": man,
                             "counts_equal": counts_equal, "pinned": [{"id": f"F0{i + 1}", "digest_equal": x[0]} for i, x in enumerate(pinned)]}))
    return p


def test_numbers_row_reads_the_file(tmp_path):
    assert RN.numbers_row(tmp_path) is None                                              # absent
    parity(tmp_path)
    assert RN.numbers_row(tmp_path) == ("The whole graph is loaded in Amazon Neptune: 1,326 nodes and 29,295 links, equal to the "
                                        "download. 3 of 4 pinned questions return the same evidence there.")
    parity(tmp_path, pinned=((True,),))
    assert RN.numbers_row(tmp_path).endswith("1 of 1 pinned question returns the same evidence there.")
    parity(tmp_path, pinned=())
    assert RN.numbers_row(tmp_path).endswith("equal to the download.")


def test_numbers_row_is_none_when_the_counts_differ(tmp_path):
    parity(tmp_path, man={"nodes": 1326, "links": 29000})
    assert RN.numbers_row(tmp_path) is None
    parity(tmp_path, counts_equal=False)
    assert RN.numbers_row(tmp_path) is None
    parity(tmp_path)
    (tmp_path / RN.MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / RN.MANIFEST).write_text(json.dumps({"counts": {"nodes": 1326, "edges": 1}}))
    assert RN.numbers_row(tmp_path) is None                                              # the shipped manifest disagrees
    (tmp_path / RN.MANIFEST).write_text(json.dumps({"counts": {"nodes": 1326, "edges": 29295}}))
    assert RN.numbers_row(tmp_path).startswith("The whole graph is loaded")
    (tmp_path / RN.LIVE_PARITY).write_text("{broken")
    assert RN.numbers_row(tmp_path) is None


def test_numbers_row_falls_back_to_the_image(tmp_path):
    parity(tmp_path / "image")
    assert RN.numbers_row(tmp_path / "data", tmp_path / "image").startswith("The whole graph is loaded")


def test_numbers_page_and_results_carry_the_sentence(client, repo, tmp_path, monkeypatch):
    # FIX2 F7 / S17 item 3, a fixture bug: the app's fallback root and the status route's roots were this repository, whose
    # committed live_parity.json (and, on the Mac, an untracked export manifest) is not the fixture's. Both now point at the
    # fixture, with an empty folder as the fallback.
    empty = tmp_path / "no_repo"
    empty.mkdir()
    monkeypatch.setattr(appmod, "ROOT", empty)
    monkeypatch.setattr(RN, "ROOT", empty)
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    assert numbers_page.numbers(tmp_path)["neptune"] is None
    parity(tmp_path)
    assert numbers_page.numbers(tmp_path)["neptune"] == RN.numbers_row(tmp_path)
    p = parity(repo)
    try:
        r = client.get("/api/results").json()
        assert r["neptune"] == RN.numbers_row(repo)
        assert r["neptune"] is not None
        assert client.get("/api/neptune/status").json().get("numbers") == RN.numbers_row(repo)
    finally:
        p.unlink()


def test_no_number_is_typed_into_the_code():
    src = (ROOT / "pipelines" / "routes_neptune.py").read_text(encoding="utf-8")
    for n in ("1,326,108", "29,295,399", "1326108", "29295399"):
        assert n not in src


# ------------------------------------------------------------------------------------------- FIX2 F4: waking cannot be abused
@pytest.fixture
def asleep(monkeypatch, tmp_path):
    d = host(tmp_path / "status", "stopped")
    monkeypatch.setenv("NEPTUNE_ENDPOINT", "c.example")
    monkeypatch.setenv("NEPTUNE_STATUS_DIR", str(d))
    monkeypatch.setattr(RN, "WAKE_HOLD", 0)                       # each test step sees the host's state as written
    appmod._ask_log.clear()
    yield d
    appmod._ask_log.clear()


def test_a_same_site_wake_still_works(client, asleep):
    r = client.post("/api/neptune/wake", headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.json()["state"] == "waking" and (asleep / "wake.request").exists()
    for origin in ("https://tmkg.16-178-197-241.sslip.io", "https://d1mdyg1ov91fxt.cloudfront.net"):
        assert not RN.cross_site({"origin": origin, "host": "10.0.0.5:8000"})       # both addresses of the hosted site


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"},
                                     {"Origin": "https://tmkg.16-178-197-241.sslip.io.evil.example"}])
def test_a_cross_site_wake_is_refused(client, asleep, headers):
    r = client.post("/api/neptune/wake", headers=headers)
    assert r.status_code == 403 and r.json()["detail"] == RN.WAKE_CROSS_SITE and not (asleep / "wake.request").exists()


def test_wakes_count_under_the_per_address_ceiling(client, asleep, monkeypatch):
    """The probe of G2-SEC-02: five POSTs with five fresh cookies while asleep all woke it. Now they count under the ask
    limit's per-address ceiling (3 x TMKG_ASK_LIMIT_PER_HOUR), whatever the cookie."""
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")                                  # ceiling 3 per address
    codes = []
    for i in range(5):
        client.cookies.clear()
        client.cookies.set("tmkg_vid", f"{i:032x}")
        codes.append(client.post("/api/neptune/wake", headers={"X-Forwarded-For": "203.0.113.9"}).status_code)
        RN._wake["at"] = None                                                            # past the 60 s write window
        (asleep / "wake.request").unlink(missing_ok=True)
    assert codes == [200, 200, 200, 429, 429]
    assert client.post("/api/neptune/wake", headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 200   # another address


def test_the_day_cap_answers_429_with_a_plain_sentence(client, asleep, monkeypatch):
    monkeypatch.setenv("TMKG_NEPTUNE_WAKES_PER_DAY", "1")
    assert client.post("/api/neptune/wake").json()["state"] == "waking"
    RN._wake["at"] = None
    (asleep / "wake.request").unlink()
    r = client.post("/api/neptune/wake")
    assert r.status_code == 429 and r.json()["detail"] == RN.WAKE_CAPPED and r.json()["state"] == "asleep"
    assert not (asleep / "wake.request").exists()
    for word in ("cluster", "429", "TMKG_", "cap"):
        assert word not in RN.WAKE_CAPPED


def test_numbers_row_says_the_copies_differ_only_when_a_ready_question_differs(tmp_path):
    p = parity(tmp_path)
    d = json.loads(p.read_text())
    d["answers"] = [{"id": "pharos_demo_1", "links_local": 8, "links_neptune": 8, "links_identical": 8}]
    p.write_text(json.dumps(d))
    s = RN.numbers_row(tmp_path)
    assert "Of the 1 ready questions, 1 returns identical evidence links there." in s and "different links" not in s
