"""Tests for pipelines/nim.py (offline backend and the NIM request path with a stubbed HTTP layer)."""
import pytest
import nim
from snapshot import Snapshot


def test_parse_json_tolerates_fences_and_text():
    assert nim.parse_json('Sure:\n```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert nim.parse_json("no json here") is None


def test_load_env_reads_values_and_survives_unreadable_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nTMKG_TEST_A='abc'\nTMKG_TEST_B=\"d e\"\n")
    monkeypatch.delenv("TMKG_TEST_A", raising=False)
    monkeypatch.setenv("TMKG_TEST_B", "already")          # existing variables win
    nim.load_env(env)
    import os
    assert os.environ["TMKG_TEST_A"] == "abc" and os.environ["TMKG_TEST_B"] == "already"
    nim.load_env(tmp_path / "missing.env")                 # absent file: no error
    nim.load_env(tmp_path)                                 # a directory cannot be read: no error, nothing set

    def denied(self):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(type(env), "exists", denied)       # what a read-protected sandbox does
    nim.load_env(env)                                      # must not raise


def test_check_schema():
    s = {"type": "object", "required": ["c"], "properties": {"c": {"type": "string", "enum": ["x", "y"]}}}
    assert nim.check_schema({"c": "x"}, s) == []
    assert nim.check_schema({"c": "z"}, s) and nim.check_schema({}, s)


def test_offline_embed_and_rerank_are_deterministic():
    n = nim.NIM(backend="offline")
    a, b = n.embed(["sacituzumab govitecan"]), n.embed(["sacituzumab govitecan"])
    assert a == b and abs(sum(x * x for x in a[0]) - 1) < 1e-9
    order = n.rerank("semagludtide", ["SEMAGLUTIDE; Ozempic", "PEMBROLIZUMAB; Keytruda"])
    assert order[0][0] == 0


def test_offline_chat_needs_registered_stand_in():
    n = nim.NIM(backend="offline")
    with pytest.raises(nim.OfflineUnavailable):
        n.chat("baseline", [{"role": "user", "content": "x"}])


def test_nim_path_freezes_requests_without_storing_key(tmp_path, monkeypatch):
    sent = {}
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    def fake_http(method, url, params, body, headers=None, store_client_errors=True):
        sent["headers"] = headers
        return {"choices": [{"message": {"content": '{"category": "safety"}'}}], "usage": {"total_tokens": 5}}
    monkeypatch.setattr(snap, "_http", fake_http)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-secret")
    n = nim.NIM(backend="nim", snap=snap)
    schema = {"type": "object", "required": ["category"], "properties": {"category": {"type": "string"}}}
    r = n.chat("extractor", [{"role": "user", "content": "why stopped?"}], schema=schema)
    assert r.data == {"category": "safety"} and r.backend == "nim" and r.key
    assert sent["headers"]["Authorization"].endswith("nvapi-test-secret")
    stored = b"".join(p.read_bytes() for p in tmp_path.rglob("*.json.gz"))
    import gzip
    assert b"nvapi-test-secret" not in gzip.decompress(stored)
    # replay offline: same answer, no HTTP
    r2 = nim.NIM(backend="nim", snap=Snapshot(tmp_path, mode="offline")).chat(
        "extractor", [{"role": "user", "content": "why stopped?"}], schema=schema)
    assert r2.data == r.data and r2.key == r.key


def test_parse_json_handles_escaped_quotes_inside_text():
    reply = '{"q": "Gant in Graduate I\\\\\\"&II", "n": "x"}'
    assert nim.parse_json(reply)["q"].endswith('"&II')
    assert nim.parse_json('Here: {"a": "say \\"hi\\" {not a brace}"} done')["a"] == 'say "hi" {not a brace}'


# ---------------------------------------------------------------- CC-36: second model, start-up check
PRIMARY, BACKUP = "nvidia/nemotron-3.5-super-vl-120b-a12b", "nvidia/nemotron-3.5-lightning-30b-a3b"
SCHEMA = {"type": "object", "required": ["category"], "properties": {"category": {"type": "string"}}}


def _stub(snap, monkeypatch, failing):
    """Stubs the HTTP layer: a chat call to a model in `failing` raises, any other answers {"category": "safety"}."""
    seen = []

    def fake_http(method, url, params, body, headers=None, store_client_errors=True):
        seen.append(body["model"])
        if body["model"] in failing:
            raise RuntimeError("HTTP 404 not found for account")
        return {"choices": [{"message": {"content": '{"category": "safety"}'}}], "usage": {"total_tokens": 5}}
    monkeypatch.setattr(snap, "_http", fake_http)
    return seen


def test_config_names_a_backup_for_the_main_model():
    n = nim.NIM(backend="offline")
    assert n.model("extractor") == PRIMARY and n.backup_model(PRIMARY) == BACKUP and n.backup_model(BACKUP) is None
    assert BACKUP in n.cfg["roles"].values()                 # the backup is an id the config already uses (verifier/judge)


def test_backup_model_answers_when_the_main_model_fails(tmp_path, monkeypatch):
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    seen = _stub(snap, monkeypatch, failing={PRIMARY})
    n = nim.NIM(backend="nim", snap=snap, fallback="offline")
    r = n.chat("extractor", [{"role": "user", "content": "why stopped?"}], schema=SCHEMA)
    assert seen == [PRIMARY, BACKUP] and r.backend == "nim" and r.model == BACKUP and r.data == {"category": "safety"}
    assert r.meta["model_fallback_from"] == PRIMARY and "404" in r.meta["reason"]
    assert n.fallbacks == [{"role": "extractor", "from": PRIMARY, "to": BACKUP, "reason": r.meta["reason"]}]


def test_both_models_fail_then_the_stand_in_answers_and_says_so(tmp_path, monkeypatch):
    import stop_reasons  # noqa: F401  (registers the extractor stand-in)
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    seen = _stub(snap, monkeypatch, failing={PRIMARY, BACKUP})
    n = nim.NIM(backend="nim", snap=snap, fallback="offline")
    r = n.chat("extractor", [{"role": "user", "content": "Trial stopped due to safety concerns"}])
    assert seen == [PRIMARY, BACKUP] and r.backend == nim.OFFLINE and r.meta["fallback_from"] == "nim"
    assert "backup " + BACKUP in r.meta["reason"] and n.fallbacks[-1]["to"] == nim.OFFLINE


def test_without_fallback_no_backup_is_called(tmp_path, monkeypatch):
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    seen = _stub(snap, monkeypatch, failing={PRIMARY})
    n = nim.NIM(backend="nim", snap=snap, fallback="none")         # evaluations: never mix models or stand-ins
    with pytest.raises(Exception):
        n.chat("extractor", [{"role": "user", "content": "x"}], schema=SCHEMA)
    assert seen == [PRIMARY] and n.fallbacks == []


def test_preflight_summary_states_and_sentences():
    assert nim.preflight_summary(None)["state"] == "pending"
    assert nim.preflight_summary({"backend": "offline", "rows": []})["state"] == "offline"
    ok = nim.preflight_summary({"backend": "nim", "rows": [dict(model=PRIMARY, status="ok"), dict(model=BACKUP, status="ok")]})
    assert ok["state"] == "ok" and ok["n"] == 2 and "all 2 hosted models answered" in ok["sentence"]
    bad = nim.preflight_summary({"backend": "nim", "rows": [dict(model=PRIMARY, status="failed"), dict(model=BACKUP, status="ok"),
                                                            dict(model="nvidia/x-embed", status="ok")]})
    assert bad["state"] == "failed" and bad["failed"] == [PRIMARY] and "1 of 3" in bad["sentence"] and PRIMARY not in bad["sentence"] and "stand-in" not in bad["sentence"]
