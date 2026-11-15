"""Tests for pipelines/snapshot.py (offline; network calls are replaced)."""
import pytest
import snapshot as sn


def test_request_key_is_stable_and_order_independent():
    a = sn.request_key("GET", "https://x", {"a": 1, "b": 2})
    assert a == sn.request_key("GET", "https://x", {"b": 2, "a": 1})
    assert a != sn.request_key("GET", "https://x", {"a": 1, "b": 3})
    assert len(a) == 16


def test_live_stores_then_offline_replays(tmp_path, monkeypatch):
    live = sn.Snapshot(tmp_path, mode="live", sleep=0)
    monkeypatch.setattr(live, "_http", lambda *a: {"answer": 42})
    r1 = live.get("demo", "https://x", {"q": 1})
    off = sn.Snapshot(tmp_path, mode="offline")
    r2 = off.get("demo", "https://x", {"q": 1})
    assert r2.body == {"answer": 42} and r2.fetched_at == r1.fetched_at and r2.key == r1.key
    assert ("demo", r1.key) in off.used


def test_offline_missing_raises(tmp_path):
    with pytest.raises(sn.MissingFromSnapshot):
        sn.Snapshot(tmp_path, mode="offline").get("demo", "https://x")


def test_failed_request_is_not_stored(tmp_path, monkeypatch):
    s = sn.Snapshot(tmp_path, mode="live", sleep=0)
    def boom(*a):
        raise RuntimeError("outage")
    monkeypatch.setattr(s, "_http", boom)
    with pytest.raises(RuntimeError):
        s.get("demo", "https://x")
    assert not list(tmp_path.rglob("*.json.gz"))


def test_prune_removes_only_unused(tmp_path, monkeypatch):
    s = sn.Snapshot(tmp_path, mode="live", sleep=0)
    monkeypatch.setattr(s, "_http", lambda *a: [1])
    s.get("demo", "https://a"); s.get("demo", "https://b")
    s2 = sn.Snapshot(tmp_path, mode="offline")
    s2.get("demo", "https://a")
    assert s2.prune() == 1 and len(list(tmp_path.rglob("*.json.gz"))) == 1


def test_rate_limit_waits_do_not_use_up_retries(tmp_path, monkeypatch):
    import snapshot
    codes = iter([429, 429, 429, 429, 429, 200])
    class R:
        def __init__(self, c): self.status_code, self.text = c, ""
        def json(self): return {"ok": True}
    monkeypatch.setattr(snapshot.requests, "post", lambda *a, **k: R(next(codes)))
    monkeypatch.setattr(snapshot.time, "sleep", lambda s: None)
    s = snapshot.Snapshot(tmp_path, mode="live", sleep=0)
    assert s.post("x", "https://example.org/api", {"a": 1}).body == {"ok": True}


def test_post_timeout_is_configurable(tmp_path, monkeypatch):
    seen = []

    class R:
        status_code = 200

        def json(self):
            return {"ok": True}
    monkeypatch.setattr(sn.requests, "post", lambda url, **k: seen.append(k["timeout"]) or R())
    sn.Snapshot(tmp_path / "a", sleep=0).post("s", "http://x/a", {"q": 1})
    sn.Snapshot(tmp_path / "b", sleep=0, post_timeout=600).post("s", "http://x/b", {"q": 1})
    assert seen == [180, 600]                                  # the default is unchanged

