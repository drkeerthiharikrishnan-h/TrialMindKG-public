"""Ask limit on the hosted app (owner decision 8 Nov): at most TMKG_ASK_LIMIT_PER_HOUR questions per visitor per hour on the three
ask endpoints; 0 / unset = unlimited; other endpoints never limited; visitors told apart by the first X-Forwarded-For address."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))
import app as appmod  # noqa: E402


def test_counting_rule_sliding_window_and_unlimited():
    log = {}
    assert all(appmod.ask_allowed("a", 100.0 + i, 3, log=log)[0] for i in range(3))
    ok, wait = appmod.ask_allowed("a", 103.0, 3, log=log)
    assert not ok and wait == 3600 - 3 + 1                     # the oldest question (t=100) leaves the window at 3700
    assert appmod.ask_allowed("b", 103.0, 3, log=log)[0]       # another visitor is counted separately
    assert appmod.ask_allowed("a", 3700.5, 3, log=log)[0]      # a slot is free again once the oldest leaves the hour
    assert all(appmod.ask_allowed("c", 1.0, 0, log={})[0] for _ in range(50))   # 0 = unlimited


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "2")
    monkeypatch.setattr(appmod, "ASK_PATHS", appmod.ASK_PATHS | {"/api/personas"})   # a cheap stand-in for an ask endpoint
    monkeypatch.setattr(appmod, "_ask_log", {})
    return TestClient(appmod.app)


def test_third_question_in_an_hour_gets_429_with_a_plain_message(limited):
    h = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    assert [limited.get("/api/personas", headers=h).status_code for _ in range(2)] == [200, 200]
    r = limited.get("/api/personas", headers=h)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert "at most 2 questions per visitor per hour" in r.json()["detail"] and "Cards, search" in r.json()["detail"]
    assert limited.get("/api/personas", headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 200   # other visitor
    assert limited.get("/api/wording", headers=h).status_code == 200                                      # not an ask path


def test_no_limit_when_unset(monkeypatch):
    monkeypatch.delenv("TMKG_ASK_LIMIT_PER_HOUR", raising=False)
    monkeypatch.setattr(appmod, "ASK_PATHS", appmod.ASK_PATHS | {"/api/personas"})
    monkeypatch.setattr(appmod, "_ask_log", {})
    c = TestClient(appmod.app)
    assert {c.get("/api/personas").status_code for _ in range(30)} == {200}


def test_every_endpoint_that_can_spend_model_credits_is_limited():
    """SEC-04 (11 Nov): /api/query and the two report exports call the models too; PK8: the source-card draft; PK4: the
    on-demand summary of a card or a link's drawer."""
    assert appmod.ASK_PATHS == {"/api/ask", "/api/ask_stream", "/api/pharos/ask", "/api/query", "/api/report.pdf",
                                "/api/report_subgraph.zip", "/api/byo/draft", "/api/summary"}
    assert appmod.EXPORT_PATHS <= appmod.ASK_PATHS


# ---- CC-46 (9 Nov): the visitor is a per-browser cookie when present (a household behind one address no longer shares a
# budget), else the first X-Forwarded-For address; the remaining count is in the 429 body and in X-Ask-Remaining.
VID_A, VID_B = "a" * 32, "0123456789abcdef" * 2


def test_visitor_key_prefers_a_well_formed_cookie_and_falls_back_to_ip():
    assert appmod.visitor_key({appmod.VISITOR_COOKIE: VID_A}, "203.0.113.7, 10.0.0.1", "127.0.0.1") == "c:" + VID_A
    assert appmod.visitor_key({}, "203.0.113.7, 10.0.0.1", "127.0.0.1") == "ip:203.0.113.7"
    assert appmod.visitor_key({}, "", "127.0.0.1") == "ip:127.0.0.1"
    assert appmod.visitor_key({appmod.VISITOR_COOKIE: "not-an-id"}, "203.0.113.7", "x") == "ip:203.0.113.7"   # junk -> IP
    assert appmod.visitor_key(None, None, None) == "ip:unknown"


def test_ask_allowed_counts_cookie_keys_separately_on_one_ip():
    log = {}
    a, b = appmod.visitor_key({appmod.VISITOR_COOKIE: VID_A}, "203.0.113.7", ""), appmod.visitor_key({appmod.VISITOR_COOKIE: VID_B}, "203.0.113.7", "")
    assert [appmod.ask_allowed(a, 10.0 + i, 2, log=log)[0] for i in range(3)] == [True, True, False]
    assert appmod.ask_allowed(b, 13.0, 2, log=log)[0]                 # same address, other browser: own budget
    ip = appmod.visitor_key({}, "203.0.113.7", "")
    assert appmod.ask_allowed(ip, 13.0, 2, log=log)[0]                # no cookie: counted under the address
    assert appmod.ask_remaining(a, 13.0, 2, log=log) == 0 and appmod.ask_remaining(b, 13.0, 2, log=log) == 1
    assert appmod.ask_remaining(a, 13.0, 0, log=log) is None          # unlimited: no count


def test_page_load_sets_the_visitor_cookie_once():
    c = TestClient(appmod.app)
    r = c.get("/classic")
    sc = r.headers.get("set-cookie", "")
    assert appmod.VISITOR_COOKIE + "=" in sc and "Max-Age=31536000" in sc and "SameSite=lax" in sc.replace("Lax", "lax") and "HttpOnly" in sc
    vid = r.cookies.get(appmod.VISITOR_COOKIE)
    assert appmod._VID.match(vid)
    r2 = c.get("/")                                                    # the client now sends it: not set again
    assert appmod.VISITOR_COOKIE not in r2.headers.get("set-cookie", "")
    assert appmod.VISITOR_COOKIE not in TestClient(appmod.app).get("/api/personas").headers.get("set-cookie", "")   # pages only


def test_remaining_header_and_429_body(limited):
    limited.cookies.set(appmod.VISITOR_COOKIE, VID_A)
    h = {"X-Forwarded-For": "203.0.113.7"}
    assert [limited.get("/api/personas", headers=h).headers["X-Ask-Remaining"] for _ in range(2)] == ["1", "0"]
    r = limited.get("/api/personas", headers=h)
    j = r.json()
    assert r.status_code == 429 and r.headers["X-Ask-Remaining"] == "0" and j["remaining"] == 0 and j["limit"] == 2
    assert j["retry_after"] == int(r.headers["Retry-After"]) > 0
    limited.cookies.set(appmod.VISITOR_COOKIE, VID_B)                 # same address, other browser
    assert limited.get("/api/personas", headers=h).status_code == 200


def test_no_remaining_header_without_a_limit(monkeypatch):
    monkeypatch.delenv("TMKG_ASK_LIMIT_PER_HOUR", raising=False)
    monkeypatch.setattr(appmod, "ASK_PATHS", appmod.ASK_PATHS | {"/api/personas"})
    assert "X-Ask-Remaining" not in TestClient(appmod.app).get("/api/personas").headers


def test_limit_value_unchanged_in_the_image():
    """The hosted limit stays 20 per hour (raising it is the owner's decision)."""
    txt = (Path(__file__).resolve().parents[1] / "aws" / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV TMKG_ASK_LIMIT_PER_HOUR=20\n" in txt
