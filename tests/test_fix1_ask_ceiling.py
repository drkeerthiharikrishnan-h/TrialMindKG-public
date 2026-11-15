"""FIX1 F3 (gate 1, G1-SEC-01, G1-SEC-04): one client cannot use up the day for everyone.

The per-visitor hourly limit counts under the visitor cookie, and a script that sends a new well-formed cookie with every ask
got a fresh budget each time (reviewer probe: hour 2, day 5, a new cookie per ask -> 200 x5, then 429 x3 from the DAY cap,
X-Ask-Remaining 1 on every 200). Now every ask is also counted under the client address, with an hourly ceiling of 3x the
visitor limit, checked before the cookie."""
import secrets
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import app as appmod  # noqa: E402


@pytest.fixture
def limited(monkeypatch):
    from collections import deque
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "2")
    monkeypatch.delenv("TMKG_ASK_LIMIT_PER_DAY", raising=False)
    monkeypatch.delenv("TMKG_ASK_LIMIT_PER_IP_HOUR", raising=False)
    monkeypatch.setattr(appmod, "ASK_PATHS", appmod.ASK_PATHS | {"/api/personas"})   # a cheap stand-in for an ask endpoint
    monkeypatch.setattr(appmod, "_ask_log", {})
    monkeypatch.setattr(appmod, "_day_log", deque())
    return TestClient(appmod.app)


def ask(client, ip, vid=None, path="/api/personas"):
    """One ask from address `ip` with visitor cookie `vid` (a new random well-formed id when None)."""
    client.cookies.clear()
    client.cookies.set(appmod.VISITOR_COOKIE, vid or secrets.token_hex(16))
    return client.get(path, headers={"X-Forwarded-For": f"{ip}, 10.0.0.1"})


def test_rotating_cookies_from_one_address_reach_429_at_the_address_ceiling(limited):
    rs = [ask(limited, "203.0.113.7") for _ in range(8)]
    assert [r.status_code for r in rs] == [200] * 6 + [429] * 2               # ceiling = 3 x 2
    assert [r.headers["X-Ask-Remaining"] for r in rs[:6]] == ["1"] * 5 + ["0"]   # the smaller of visitor and address left
    body = rs[6].json()
    assert body["scope"] == "address" and body["limit"] == 6 and body["remaining"] == 0
    assert int(rs[6].headers["Retry-After"]) == body["retry_after"] > 0
    assert "per hour from one network address" in body["detail"] and "Cards, search" in body["detail"]


def test_two_addresses_with_one_cookie_each_are_independent(limited):
    a, b = "a" * 32, "b" * 32
    assert [ask(limited, "203.0.113.7", a).status_code for _ in range(3)] == [200, 200, 429]   # the visitor limit, as before
    assert ask(limited, "203.0.113.7", a).json().get("scope") is None
    assert [ask(limited, "198.51.100.9", b).status_code for _ in range(2)] == [200, 200]       # other address: own budgets


def test_the_visitor_limit_still_applies_below_the_ceiling(limited):
    vid = "c" * 32
    assert [ask(limited, "203.0.113.7", vid).status_code for _ in range(3)] == [200, 200, 429]
    assert ask(limited, "203.0.113.7").status_code == 200                     # a second browser at the same address


def test_refused_requests_give_the_address_count_back(limited, monkeypatch):
    # a request refused for its form (here 404 on the stand-in path with a bad id) does not use up the address's questions
    monkeypatch.setattr(appmod, "ASK_PATHS", appmod.ASK_PATHS | {"/api/nothing_here"})
    for _ in range(10):
        assert ask(limited, "203.0.113.7", path="/api/nothing_here").status_code == 404
    assert ask(limited, "203.0.113.7").status_code == 200


def test_ceiling_rules():
    assert appmod.ip_limit(20) == 60 and appmod.ip_limit(0) == 0              # none when the visitor limit is off
    assert appmod.ip_key("203.0.113.7, 10.0.0.1", "127.0.0.1") == "net:203.0.113.7"
    assert appmod.ip_key("", "127.0.0.1") == "net:127.0.0.1" and appmod.ip_key(None, None) == "net:unknown"
    assert appmod.ip_key("", "x") != appmod.visitor_key({}, "", "x")          # never the cookieless visitor's key
    log = {}
    assert all(appmod.ip_ceiling_allowed("net:a", 10.0 + i, 3, log=log)[0] for i in range(3))
    ok, wait = appmod.ip_ceiling_allowed("net:a", 13.0, 3, log=log)
    assert not ok and wait == 3600 - 3 + 1
    assert appmod.ip_ceiling_allowed("net:a", 13.0, 0, log=log) == (True, 0)
    assert appmod.ip_ceiling_allowed("net:b", 13.0, 3, record=False, log=log)[0] and "net:b" not in log or log["net:b"] == []


def test_own_ceiling_from_the_environment(monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_IP_HOUR", "100")
    assert appmod.ip_limit(20) == 100
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_IP_HOUR", "0")
    assert appmod.ip_limit(20) == 0                                            # explicitly off


def test_ask_quota_reports_the_address_ceiling(limited):
    for _ in range(6):
        assert ask(limited, "203.0.113.7").status_code == 200
    q = ask(limited, "203.0.113.7", path="/api/ask_quota").json()
    assert q["remaining"] == 0 and q["retry_after"] > 0 and "network address" in q["detail"]


def test_the_on_demand_edge_check_counts_against_the_same_ceiling(tmp_path, monkeypatch):
    import test_routes_audit as TA
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    client, chk, fakes, host = TA.make(tmp_path)
    log = host.ask_log
    host.ip_key, host.ip_limit, host.ip_ceiling_refusal = appmod.ip_key, appmod.ip_limit, appmod.ip_ceiling_refusal
    host.ip_ceiling_allowed = lambda net, now, lim, record=True: appmod.ip_ceiling_allowed(net, now, lim, record=record, log=log)
    codes = []
    for i in range(5):
        client.cookies.clear()
        client.cookies.set(appmod.VISITOR_COOKIE, secrets.token_hex(16))
        codes.append(client.get(f"/api/pharos/audit/e0{i:014x}"))
    assert [r.status_code for r in codes] == [200, 200, 200, 429, 429]        # ceiling = 3 x 1, new cookie every time
    assert codes[3].json()["scope"] == "address" and codes[3].json()["available"] is False
    assert len(fakes.built) == 3                                               # the refused checks made no outbound call


def test_caddy_fills_hsts_only_when_missing():
    """G1-SEC-04: Caddy set Strict-Transport-Security unconditionally and the app set it again: two headers per response."""
    ud = (ROOT / "aws" / "user_data.sh.tmpl").read_text()
    block = ud[ud.index("cat >> /srv/app/Caddyfile <<CADDY\n"):]
    block = block[block.index("header {"):block.index("}", block.index("header {"))]
    assert '?Strict-Transport-Security "max-age=31536000"' in block
    assert not any(ln.strip().startswith("Strict-Transport-Security") for ln in block.splitlines())
