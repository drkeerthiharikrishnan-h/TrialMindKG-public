"""bin/agent-cli nim-check and nim.preflight: every configured model gets one tiny structured call; a 404-for-this-account or a
wrong reply fails; the catalogue listing is a hint; nothing is stored in data/snapshots_llm; the offline backend says so."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import nim
from snapshot import Snapshot

ROOT = Path(__file__).resolve().parents[1]
CFG = nim.yaml.safe_load(nim.CONFIG.read_text())
MODELS = sorted(set(CFG["roles"].values()))


def live_nim(tmp_path, answer, listed=None, key="nvapi-test-secret"):
    """A NIM whose HTTP layer calls answer(url, body) -> body or raises; GET /models lists `listed` (default: all models)."""
    snap = Snapshot(tmp_path / "frozen", mode="live", sleep=0)
    seen = []

    def fake_http(method, url, params, body, headers=None, store_client_errors=True, budget=None):
        seen.append((method, url, dict(headers or {})))
        if method == "GET" and url.endswith("/models"):
            ids = MODELS + [CFG["embedding"], CFG["reranker"]] if listed is None else listed
            return {"data": [{"id": i} for i in ids]}
        return answer(url, body)
    snap._http = fake_http
    n = nim.NIM(backend="nim", snap=snap)
    n.key = key
    n.seen = seen
    return n


def healthy(url, body):
    if url.endswith("/chat/completions"):
        return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}}
    if url.endswith("/embeddings"):
        return {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
    return {"rankings": [{"index": 1, "logit": 2.0}, {"index": 0, "logit": 1.0}]}


def test_all_models_answer(tmp_path):
    n = live_nim(tmp_path, healthy)
    rep = nim.preflight(n, snap=n.snap)
    kinds = [r["kind"] for r in rep["rows"]]
    assert rep["ok"] is True and kinds.count("chat") == len(MODELS) and "embedding" in kinds and "reranker" in kinds
    assert all(r["status"] == "ok" and r["listed"] is True for r in rep["rows"])
    chat = [r for r in rep["rows"] if r["kind"] == "chat"]
    assert sorted(r["model"] for r in chat) == MODELS and all(r["roles"] for r in chat)
    assert "all models answer" in nim.preflight_text(rep)
    # one call per distinct model, however many roles share it; the key goes in the header only
    assert sum(1 for m, u, h in n.seen if u.endswith("/chat/completions")) == len(MODELS)
    assert all(h.get("Authorization") == "Bearer nvapi-test-secret" for m, u, h in n.seen)


def test_the_catalogue_can_list_a_model_that_answers_404(tmp_path):
    bad = CFG["roles"]["extractor"]

    def answer(url, body):
        if url.endswith("/chat/completions") and body["model"] == bad:
            raise RuntimeError("POST failed after retries: HTTP 404")
        return healthy(url, body)
    n = live_nim(tmp_path, answer)
    rep = nim.preflight(n, snap=n.snap)
    failed = [r for r in rep["rows"] if r["status"] == "failed"]
    assert rep["ok"] is False and [r["model"] for r in failed] == [bad]
    assert failed[0]["listed"] is True and "not found for this account" in failed[0]["detail"]
    text = nim.preflight_text(rep)
    assert "FAILED" in text and "1 model(s) FAILED" in text and "extractor" in text


def test_a_model_missing_from_the_catalogue_is_flagged_but_judged_by_its_call(tmp_path):
    n = live_nim(tmp_path, healthy, listed=[m for m in MODELS if m != CFG["roles"]["verifier"]])
    rep = nim.preflight(n, snap=n.snap)
    row = [r for r in rep["rows"] if r["model"] == CFG["roles"]["verifier"]][0]
    assert row["listed"] is False and row["status"] == "ok" and rep["ok"] is True
    assert "NOT LISTED" in nim.preflight_text(rep)


def test_a_wrong_reply_or_an_unreadable_listing_is_reported(tmp_path):
    def wrong(url, body):
        if url.endswith("/chat/completions"):
            return {"choices": [{"message": {"content": '{"ok": false}'}}]}
        return healthy(url, body)
    n = live_nim(tmp_path, wrong)
    rep = nim.preflight(n, snap=n.snap)
    assert rep["ok"] is False and all(r["status"] == "failed" for r in rep["rows"] if r["kind"] == "chat")
    n = live_nim(tmp_path / "second", healthy)          # a fresh folder: the first listing is not reused
    n.snap._http = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")) if a[1].endswith("/models") else healthy(a[1], a[3])
    rep2 = nim.preflight(n, snap=n.snap)
    assert rep2["listed"] is None and all(r["listed"] is None for r in rep2["rows"]) and "not readable" in nim.preflight_text(rep2)
    m = live_nim(tmp_path, healthy)
    assert nim.preflight(m, snap=m.snap, list_models=False)["listed"] is None


def test_without_a_snapshot_argument_the_preflight_uses_a_throw_away_folder(tmp_path, monkeypatch):
    """No snapshot given: a temp folder in refresh mode, so nothing reaches data/snapshots_llm and no stored answer is reused."""
    made = []
    real = nim.Snapshot

    def spy(directory, mode=None, sleep=0.3):
        s = real(directory, mode=mode, sleep=0)
        s._http = lambda m, u, p, b, headers=None, store_client_errors=True, budget=None: (
            {"data": []} if u.endswith("/models") else healthy(u, b))
        made.append((Path(directory), mode))
        return s
    monkeypatch.setattr(nim, "Snapshot", spy)
    n = live_nim(tmp_path, healthy)
    rep = nim.preflight(n)
    assert rep["ok"] is True and len(made) == 1
    folder, mode = made[0]
    assert mode == "refresh" and ROOT / "data" not in folder.parents and tmp_path not in folder.parents


def test_offline_backend_has_nothing_to_check():
    rep = nim.preflight(nim.NIM(backend="offline"))
    assert rep["ok"] is None and rep["rows"] == [] and "offline stand-in" in nim.preflight_text(rep)


def test_cli_exit_codes_and_json(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "NVIDIA_API_KEY": "", "NIM_BASE_URL": ""}
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "agent-cli"), "nim-check"], capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 2 and "offline stand-in" in r.stdout            # no key: exit 2, says why
    j = subprocess.run([sys.executable, str(ROOT / "bin" / "agent-cli"), "nim-check", "--json"], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    assert j.returncode == 2 and json.loads(j.stdout)["backend"] == "offline"


def test_config_documents_the_fallback_and_defaults_to_none():
    assert CFG["fallback"] == "none"
    assert nim.NIM(backend="offline").fallback == "none"
    assert nim.NIM(backend="offline", fallback="offline").fallback == "offline"


def test_snapshot_marks_stored_answers_as_cached(tmp_path):
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    snap._http = lambda *a, **k: {"x": 1}
    first = snap.post("nim", "https://example.org/v1", {"a": 1})
    second = snap.post("nim", "https://example.org/v1", {"a": 1})
    assert first.cached is False and second.cached is True and first.key == second.key
