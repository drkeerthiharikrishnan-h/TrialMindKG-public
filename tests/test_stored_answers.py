"""CC-31h (review finding M8): stored answers for the worked examples and the role questions (pipelines/stored_answers.py),
served by /api/stored_answers and /api/stored_answer/{id}, recorded by scripts/precompute_examples.py, which never stores an
answer of the offline stand-in."""
import importlib.util
import json
import time
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import app as appmod  # noqa: E402
import persona_examples  # noqa: E402
import personas  # noqa: E402
import stored_answers as sa  # noqa: E402
from kg_query import KGIndex  # noqa: E402
from nim import NIM  # noqa: E402

spec = importlib.util.spec_from_file_location("precompute", ROOT / "scripts" / "precompute_examples.py")
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)


@pytest.fixture(scope="module")
def targets():
    kg, roles = KGIndex(), personas.load_personas()
    return sa.targets(persona_examples.build(kg, roles), roles), roles


def test_targets_are_the_model_questions_once_each(targets):
    ts, roles = targets
    assert ts and len({t["question"] for t in ts}) == len(ts) and len({t["id"] for t in ts}) == len(ts)
    assert {t["kind"] for t in ts} <= {"trials", "query", "pharos"} and all(t["endpoint"] == sa.ENDPOINTS[t["kind"]] for t in ts)
    role_qs = {q["text"] for p in roles for q in p["questions"] if q.get("tab") in ("trials", "pharos")}
    tested = {q for q in role_qs if sa.is_tested(q)}              # U5: answered by the tested question, never stored
    assert tested and role_qs - tested <= {t["question"] for t in ts} and not tested & {t["question"] for t in ts}
    assert all(sa.ID_RE.match(t["id"]) for t in ts)
    assert sa.question_id(" Why? ") == sa.question_id("Why?") and sa.question_id("a") != sa.question_id("b")


def test_record_index_and_load_round_trip(tmp_path):
    t = dict(id="x01", kind="query", question="What was the primary endpoint of RESOLVE-IT?", endpoint="/api/query")
    rec = sa.record(t, {"rows": [1]}, "nim", "m", recorded="2026-11-09T10:00:00Z")
    (tmp_path / "x01.json").write_text(json.dumps(rec))
    (tmp_path / "notes.json").write_text("{}")                          # not an id: ignored
    (tmp_path / "q_0123456789.json").write_text("not json")             # unreadable: ignored
    assert sa.index(tmp_path) == [dict(id="x01", question=t["question"], kind="query", recorded="2026-11-09T10:00:00Z",
                                       backend="nim", model="m")]
    assert sa.load(tmp_path, "x01")["answer"] == {"rows": [1]}
    assert sa.load(tmp_path, "../x01") is None and sa.load(tmp_path, "x99") is None and sa.index(tmp_path / "none") == []


def test_the_endpoints_serve_only_stored_ids(tmp_path, monkeypatch):
    t = dict(id="x02", kind="trials", question="Q?", endpoint="/api/ask")
    (tmp_path / "x02.json").write_text(json.dumps(sa.record(t, {"sections": []}, "nim", "m")))
    monkeypatch.setattr(sa, "FOLDER", tmp_path)
    c = TestClient(appmod.app)
    assert [a["id"] for a in c.get("/api/stored_answers").json()["answers"]] == ["x02"]
    assert c.get("/api/stored_answer/x02").json()["answer"] == {"sections": []}
    assert c.get("/api/stored_answer/x03").status_code == 404 and c.get("/api/stored_answer/..%2Fx02").status_code == 404


def test_hosted_tells_a_model_answer_from_the_stand_in():
    assert pre.hosted({"backend": "nim"}, "query") and not pre.hosted({"backend": "offline-stand-in"}, "query")
    assert not pre.hosted({"backend": "nim", "fallback_from": "nim"}, "pharos")
    ok = {"run": {"backend": "nim", "model_calls": [{"backend": "nim"}]}}
    assert pre.hosted(ok, "trials") and not pre.hosted({"run": {"backend": "nim", "model_calls": [{"backend": "offline-stand-in"}]}}, "trials")


def test_the_script_never_stores_a_stand_in_answer(targets, tmp_path, capsys):
    appmod._state.update(kg=KGIndex(), nim=NIM(backend="offline"))
    q = next(t for t in targets[0] if t["kind"] == "query")
    assert pre.main(["--dry-run"]) == 0 and q["id"] in capsys.readouterr().out
    assert pre.main(["--only", q["id"], "--out", str(tmp_path)]) == 1
    assert f"SKIP {q['id']}" in capsys.readouterr().out and not list(tmp_path.glob("*.json"))


def test_the_page_uses_stored_answers_and_offers_a_live_run():
    t = (ROOT / "pipelines" / "app_page_v3.html").read_text()
    for needle in ('fetch("/api/stored_answers")', "function storedBanner(meta)", "Ask again now", "async function ask(q, fromRoute, x, live, btn)",   # PK6: was "Re-run live"   # CC-46: + the clicked control
                   "async function trialQuery(q, x, live, btn)", "async function pharosAsk(q, live, btn)", "if (fromStore) markStored("):
        assert needle in t, needle


# ------------------------------------------------------------------ FIX2 F7 (S08 item 1): the ahead-of-time job can store a trial answer
def test_batch_waits_outlast_the_web_deadline_and_are_restored(tmp_path, monkeypatch):
    """A fake model slower than the web deadline: as a web request the second call is refused (no time left); inside
    batch_waits() it is made, and a 429 is waited out. On leaving, the web rules are back."""
    import nim
    import snapshot
    from types import SimpleNamespace
    replies = []

    def post(url, json=None, timeout=None, headers=None):
        t0 = time.monotonic()                                                             # slower than the 0.1 s web deadline
        while time.monotonic() - t0 < 0.15:                                               # (time.sleep is stubbed below)
            pass
        code = replies.pop(0) if replies else 200
        return SimpleNamespace(status_code=code, json=lambda: {"ok": True}, text="")
    monkeypatch.setattr(snapshot.requests, "post", post)
    monkeypatch.setattr(snapshot.time, "sleep", lambda s: None)                           # the 429 wait, without waiting
    snap = snapshot.Snapshot(directory=tmp_path, mode="live")
    web = nim.web_client(0.1, base=NIM(backend="offline")).budget
    assert snap._http("POST", "https://x.invalid/v1", None, {}, budget=web) == {"ok": True}
    with pytest.raises(snapshot.DeadlineExceeded):
        snap._http("POST", "https://x.invalid/v1", None, {}, budget=web)
    before = nim.web_budget
    with pre.batch_waits():
        b = nim.web_client(0.1, base=NIM(backend="offline")).budget
        assert b.deadline is None and b.rate_limit_waits == pre.BATCH_RATE_LIMIT_WAITS and b.timeout == pre.BATCH_TIMEOUT
        for _ in range(3):
            assert snap._http("POST", "https://x.invalid/v1", None, {}, budget=b) == {"ok": True}
        replies[:] = [429]
        assert snap._http("POST", "https://x.invalid/v1", None, {}, budget=b) == {"ok": True}       # waited out
    assert nim.web_budget is before and nim.web_client(0.1, base=NIM(backend="offline")).budget.deadline is not None


def test_for_storage_drops_the_attached_summary_and_keeps_the_program_ids():
    progs = {"P22": {"name": "Elafibranor — RESOLVE-IT"}, "P01": {"name": "Drug A — TRIAL"}}
    ans = {"rows": [{"program_1": "Elafibranor — RESOLVE-IT", "program_2": ["Drug A — TRIAL"], "target": "UniProtKB:P1", "e1": "e1"}],
           "summary_text": "x", "summary_meta": {"stored": True}, "cypher": "MATCH ..."}
    got = pre.for_storage(ans, {"kind": "query"}, progs)
    assert "summary_text" not in got and "summary_meta" not in got and got["cypher"] == "MATCH ..."
    assert got["rows"] == [{"program_1": "P22", "program_2": ["P01"], "target": "UniProtKB:P1", "e1": "e1"}]
    assert ans["summary_text"] == "x"                                                     # the input is not changed
    pharos = pre.for_storage({"rows": [{"name": "Elafibranor — RESOLVE-IT"}], "summary_text": "y"}, {"kind": "pharos"}, progs)
    assert pharos == {"rows": [{"name": "Elafibranor — RESOLVE-IT"}]}                       # only a trial-graph query is renamed back
    shown = sa.program_names(got["rows"], "MATCH (p:Program) RETURN p.id AS program_1, p.id AS program_2", progs)
    assert shown[0]["program_1"] == "Elafibranor — RESOLVE-IT"                              # the page renames them again
