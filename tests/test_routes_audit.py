"""Tests for pipelines/routes_audit.py: the independent check on an edge, with fake collectors and a fake judge
(no network, no model, no data/)."""
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import app as appmod  # noqa: E402
import edge_evidence as ee  # noqa: E402
import routes_audit as ra  # noqa: E402
from nim import NIM  # noqa: E402

# marker values of the pruner: none of them may reach a collector, the judge or the page
SECRET = ["PRUNERVERDICT", "0.123456", "PRUNERREASON"]
PPI = "e00000000000000a1"
GENE_PRODUCT = "e00000000000000b2"


def explained(eid):
    """What the graph's explain_edge() returns: the edge with the pruner's verdict next to it."""
    if eid == GENE_PRODUCT:
        return {"id": eid, "verdict": {"verdict": SECRET[0], "confidence": SECRET[1], "reasons": [SECRET[2]]},
                "subject": {"id": "NCBIGene:1956", "symbol": "EGFR"}, "object": {"id": "UniProtKB:P00533", "name": "EGFR protein"},
                "provenance": {"subject": "NCBIGene:1956", "predicate": "biolink:has_gene_product", "object": "UniProtKB:P00533",
                               "source": "Pharos", "primary_knowledge_source": "infores:pharos", "n_records": "1"},
                "properties": {}}
    if not eid.startswith("e0"):
        return None                                                       # a registry link: not in the index
    return {"id": eid, "statement": "A interacts with B",
            "verdict": {"verdict": SECRET[0], "confidence": SECRET[1], "reasons": [SECRET[2]]},
            "corroboration": [{"verdict": SECRET[0]}], "timeline": {"summary": SECRET[2]},
            "subject": {"id": "UniProtKB:P01112", "symbol": "HRAS", "name": "GTPase HRas"},
            "object": {"id": "UniProtKB:P04049", "symbol": "RAF1"},
            "provenance": {"subject": "UniProtKB:P01112", "predicate": "biolink:physically_interacts_with",
                           "object": "UniProtKB:P04049", "source": "STRING", "primary_knowledge_source": "infores:string",
                           "knowledge_level": "statistical_association", "n_records": "3"},
            "properties": {"stringdb_score": "0.91", "pmids": "8670882|111|222|333|444|555", "verdict": SECRET[0],
                           "confidence": SECRET[1], "reasons": SECRET[2]}}


def source(name="IntAct", status="answered", items=1, note="1 records for this pair", url="https://www.ebi.ac.uk/intact/search?query=x"):
    return ee.source_record(name, status, [ee.make_item(f"{name} record {i + 1}: physical association", "https://www.ebi.ac.uk/x", "evidence")
                                           for i in range(items)], url, note)


class Fakes:
    """Fake collectors (build) and a fake judge that record what they were given."""

    def __init__(self, sources=None, label="supported", delay=0.0, judge_error=None, problems=(), judge_delay=0.0):
        self.sources, self.label, self.delay, self.judge_delay = sources, label, delay, judge_delay
        self.judge_error, self.problems = judge_error, list(problems)
        self.built, self.judged = [], []

    def build(self, edge, fetch, lookups, edge_id=""):
        self.built.append((edge_id, dict(edge), fetch))
        time.sleep(self.delay)
        d = {"schema": 1, "edge_id": edge_id, "edge": dict(edge), "group": ee.edge_group(edge), "required": ["IntAct"],
             "collected": "2026-11-12", "sources": [dict(s, items=[dict(i) for i in s["items"]]) for s in (self.sources or [source()])]}
        ee.number_items(d["sources"])
        return d

    def judge(self, dossier, nim, model):
        self.judged.append((dossier, nim, model))
        time.sleep(self.judge_delay)
        if self.judge_error:
            raise self.judge_error
        items = [it["id"] for it in ee.dossier_items(dossier)][:1]
        return {"label": "cannot tell" if self.problems else self.label, "label_raw": self.label, "problems": self.problems,
                "rationale": "IntAct holds a physical association record for the pair. It was detected by two hybrid. A third sentence.",
                "items": items, "model": model, "key": ""}


def hosted_nim():
    return SimpleNamespace(backend="nim", cfg={"roles": {}, "spotcheck_raters": {"judge_a": "judge-a", "judge_b": "judge-b"}})


def make(tmp_path, fakes=None, nim=None, explain=explained, **checker_args):
    """A small app around the router: (client, checker, fakes, host). The host stands in for pipelines/app.py."""
    fakes = fakes or Fakes()
    log = {}
    host = SimpleNamespace(
        ROOT=tmp_path / "image", _data_root=lambda: tmp_path / "dataroot", nan_safe=appmod.nan_safe,
        answering_graph=lambda: SimpleNamespace(explain_edge=explain), state=lambda: (None, nim or hosted_nim()),
        visitor_key=appmod.visitor_key, ask_log=log,
        ask_allowed=lambda v, now, limit: appmod.ask_allowed(v, now, limit, log=log),
        ask_remaining=lambda v, now, limit: appmod.ask_remaining(v, now, limit, log=log))
    chk = ra.Checker(build=fakes.build, judge=fakes.judge, lookups=SimpleNamespace(), **{"per_minute": 0, **checker_args})
    app = FastAPI()
    app.include_router(ra.make_router(host, chk))
    return TestClient(app), chk, fakes, host


def write_stored(folder, eid, **changes):
    d = {"schema": 1, "edge_id": eid, "edge": {}, "group": "protein_protein", "required": [], "collected": "2026-11-12",
         "sources": [source(), source("Reactome", "failed", 0, "TimeoutError: HTTPSConnectionPool(host='reactome.org') /srv/x.py", ""),
                     dict(source("STRING", items=0, note="link present"), url="javascript:alert(1)")]}
    ee.number_items(d["sources"])
    js = [{"label": "supported", "rationale": "First sentence about IA1. Second sentence. Third sentence.", "items": ["IA1"]}] * 3
    rec = {**ee.audit_record(d, js), **changes}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{eid}.json").write_text(json.dumps(rec))
    return rec


def no_secret(*things):
    blob = json.dumps(things, default=str)
    return not any(s in blob for s in SECRET)


# ------------------------------------------------------------------------------------------------ the blind edge
def test_blind_edge_equals_the_spot_check_row_and_reads_no_verdict():
    import audit_spotcheck as a
    import make_spotcheck_blinded as m
    assert ra.STAT_COLS == m.STAT_COLS
    for eid in (PPI, GENE_PRODUCT):
        assert ra.blind_edge(explained(eid)) == a.blind_row(explained(eid))
    row = ra.blind_edge(explained(PPI))
    assert list(row) == ee.BLIND_FIELDS and no_secret(row)
    assert row["subject_name"] == "HRAS" and row["predicate"] == "physically_interacts_with"
    assert row["source"] == "STRING (infores:string)" and row["source_statistic"] == "stringdb_score=0.91; n_records=3"
    assert row["references"] == "PMID:8670882 | PMID:111 | PMID:222 | PMID:333 | PMID:444 (+1 more)"
    assert ra.blind_edge(None) is None and ra.blind_edge({"id": "x"}) is None and ra.blind_edge("x") is None
    rec = {"activity": "7.2", "activity_type": "IC50", "p_value": "", "n_records": "1"}
    assert ra.stat_string(rec) == m.stat_string(rec) == "activity=7.2 (IC50)"
    assert ra.references({"pmids": "1|2"}) == m.references({"pmids": "1|2"}) == "PMID:1 | PMID:2" and ra.references({}) == ""


# ------------------------------------------------------------------------------------------------ stored records
def test_safe_edge_id_and_load_stored(tmp_path):
    assert ra.safe_edge_id(PPI) and ra.safe_edge_id("NCT01234567:x-1")
    for bad in ("", "..", "a/..", "../etc/passwd", "a/b", "a\\b", ".hidden", "a b", "x" * 129, "a..b", "a\x00b"):
        assert not ra.safe_edge_id(bad)
    folder = tmp_path / "edges"
    rec = write_stored(folder, PPI)
    assert ra.load_stored(PPI, [tmp_path / "none", folder]) == rec                 # first folder that holds it
    assert ra.load_stored("e0missing", [folder]) is None
    (tmp_path / "secret.json").write_text(json.dumps(rec))
    assert ra.load_stored("../secret", [folder]) is None                           # never outside the folder
    (folder / "e0broken.json").write_text("{not json")
    (folder / "e0shape.json").write_text(json.dumps({"judgement": {"label": "keep"}, "sources": []}))
    (folder / "index.json").write_text(json.dumps({PPI: "supported"}))
    assert ra.load_stored("e0broken", [folder]) is None and ra.load_stored("e0shape", [folder]) is None
    assert ra.load_stored("index", [folder]) is None                               # the label index is not a record


def test_stored_hit_needs_no_collector_no_judge_and_is_not_counted(tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    client, chk, fakes, host = make(tmp_path)
    write_stored(tmp_path / "dataroot" / "data" / "audit" / "edges", PPI, verdict=SECRET[0], confidence=SECRET[1], reasons=SECRET[2])
    for _ in range(3):                                                             # more often than the visitor limit allows
        r = client.get(f"/api/pharos/audit/{PPI}")
        assert r.status_code == 200
    p = r.json()
    assert fakes.built == [] and fakes.judged == [] and host.ask_log == {}
    assert p["available"] is True and p["origin"] == "stored" and p["edge_id"] == PPI and p["checked"] == "2026-11-12"
    assert p["statement"] == "checked by an auditing agent against sources the pruner does not read"
    assert p["claim_kind"] == "physical interaction of two proteins"
    assert set(p) == {"edge_id", "available", "origin", "checked", "statement", "claim_kind", "sources", "judgement", "complete", "note",
                      "raters_note"}
    assert p["raters_note"] == ""                                                  # no spot-check result under these roots
    assert [s["source"] for s in p["sources"]] == ["IntAct", "Reactome", "STRING"]
    intact, reactome, string = p["sources"]
    assert intact["status"] == "answered" and intact["returned"] == "1 records for this pair" and intact["url"].startswith("https://")
    assert intact["items"] == [{"id": "IA1", "text": "IntAct record 1: physical association", "url": "https://www.ebi.ac.uk/x", "kind": "evidence"}]
    assert reactome["status"] == "failed" and reactome["returned"] == "This source did not answer."   # no error text, host or path
    assert string["url"] == ""                                                     # only web links are passed on
    assert p["judgement"] == {"label": "supported", "rationale": "First sentence about IA1. Second sentence.",
                              "raters": 3, "raters_agreeing": 3, "items": ["IA1"]}
    assert p["complete"] is False and p["note"] == "1 of 3 sources did not answer in full; the judgement rests on what the others returned."
    assert no_secret(p) and "HTTPSConnectionPool" not in r.text and "/srv/" not in r.text


def test_stored_record_in_the_image_is_found_when_the_data_root_has_none(tmp_path):
    client, _, fakes, _ = make(tmp_path)
    write_stored(tmp_path / "image" / "data" / "audit" / "edges", PPI)
    assert client.get(f"/api/pharos/audit/{PPI}").json()["origin"] == "stored" and fakes.built == []


# ------------------------------------------------------------------------------------------------ on demand
def test_on_demand_success_is_blind_cached_and_counted_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "5")
    client, chk, fakes, host = make(tmp_path)
    r = client.get(f"/api/pharos/audit/{PPI}")
    p = r.json()
    assert r.status_code == 200 and p["available"] is True and p["origin"] == "on_demand" and p["complete"] is True and p["note"] == ""
    assert p["statement"] == ee.STATEMENT and p["checked"] == "2026-11-12"
    assert p["judgement"] == {"label": "supported", "raters": 1, "raters_agreeing": 1, "items": ["IA1"],
                              "rationale": "IntAct holds a physical association record for the pair. It was detected by two hybrid."}
    assert p["sources"][0]["items"][0]["id"] == "IA1" and r.headers["X-Ask-Remaining"] == "4"
    # what the collectors and the judge received: the blind columns only, a fetcher that ends inside the budget, one judge
    (eid, edge, fetch), = fakes.built
    assert eid == PPI and list(edge) == ee.BLIND_FIELDS and isinstance(fetch, ra.BudgetFetcher)
    (dossier, nim, model), = fakes.judged
    assert model == "judge-b" and no_secret(edge, dossier, p)
    assert isinstance(nim.snap, ra.OneShotPost) and nim.fallback == "none"
    # the second request is answered from the cache: no new check, not counted
    r2 = client.get(f"/api/pharos/audit/{PPI}")
    assert r2.json() == p and len(fakes.built) == 1 and len(fakes.judged) == 1 and "X-Ask-Remaining" not in r2.headers
    assert sum(len(v) for v in host.ask_log.values()) == 1


def test_timeout_gives_the_plain_sentence_and_the_late_result_is_cached(tmp_path):
    client, chk, fakes, _ = make(tmp_path, Fakes(judge_delay=1.5), budget=0.2, collect=0.1, min_judge=0.05)
    t0 = time.monotonic()
    r = client.get(f"/api/pharos/audit/{PPI}")
    waited = time.monotonic() - t0
    assert r.status_code == 200 and r.json() == {"edge_id": PPI, "available": False, "message": "independent check unavailable right now"}
    assert waited < 1.2                                                            # the request did not wait for the slow check
    deadline = time.monotonic() + 5
    while chk.cached(PPI) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.get(f"/api/pharos/audit/{PPI}").json()["origin"] == "on_demand" and len(fakes.built) == 1


class FakeResponse:
    """A streamed HTTP answer: `pieces` are handed out one by one, `pause` seconds apart."""

    def __init__(self, pieces, status=200, pause=0.0, encoding="utf-8"):
        self.pieces, self.status_code, self.pause, self.encoding = pieces, status, pause, encoding

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ra.requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        for p in self.pieces:
            time.sleep(self.pause)
            yield p


def test_budget_fetcher_ends_at_the_deadline(monkeypatch):
    seen, answer = [], {}

    def request(method, url, timeout=None, stream=None, **kwargs):
        seen.append(SimpleNamespace(method=method, url=url, timeout=timeout, stream=stream, kwargs=kwargs))
        return answer["r"]

    monkeypatch.setattr(ra.requests, "request", request)
    f = ra.BudgetFetcher(deadline=time.monotonic() + 1.0)
    answer["r"] = FakeResponse([b'{"ok"', b": 1}"])
    assert f.json("x", "https://example.org/a", {"q": 1}, headers={"Accept": "application/json"}) == {"ok": 1}
    assert f.post("x", "https://example.org/b", {"query": "q"}) == {"ok": 1}
    answer["r"] = FakeResponse(["h\u00e9llo\t".encode(), b"tab"])
    assert f.text("x", "https://example.org/c") == "h\u00e9llo\ttab"
    assert [(c.method, c.kwargs) for c in seen] == [("GET", {"params": {"q": 1}, "headers": {"Accept": "application/json"}}),
                                                    ("POST", {"json": {"query": "q"}}), ("GET", {"params": None})]
    assert all(c.stream and 0 < c.timeout[1] <= 1.0 and c.timeout[0] <= c.timeout[1] for c in seen)   # cut to the time left
    for status in (400, 404):                                                      # "no such record" is an answer, as in FastFetcher
        answer["r"] = FakeResponse([b"{}"], status=status)
        assert f.json("x", "https://example.org/d") is None
    answer["r"] = FakeResponse([b"{}"], status=500)
    with pytest.raises(ra.requests.HTTPError):
        f.json("x", "https://example.org/e")
    # an answer that keeps trickling in is cut off at the deadline
    slow = ra.BudgetFetcher(deadline=time.monotonic() + 0.15)
    answer["r"] = FakeResponse([b"a"] * 50, pause=0.05)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        slow.text("x", "https://example.org/slow")
    assert time.monotonic() - t0 < 0.6
    # after the deadline no call is made; a collector turns that into a 'failed' source
    n = len(seen)
    with pytest.raises(TimeoutError):
        slow.json("x", "https://example.org/late")
    assert ee.collect_quickgo("P01112", "GO:0005886", slow)["status"] == "failed" and len(seen) == n


def test_real_collector_and_deterministic_judge_need_no_model(tmp_path):
    """The gene-to-protein claim is decided from the UniProt record alone: it works without a hosted model."""
    body = {"entryType": "UniProtKB reviewed (Swiss-Prot)", "uniProtKBCrossReferences": [{"database": "GeneID", "id": "1956"}]}
    fetch = SimpleNamespace(json=lambda source, url, params=None, headers=None: body)
    chk_args = dict(build=lambda edge, f, lookups, edge_id="": ee.build_dossier(edge, fetch, lookups, today="2026-11-12", edge_id=edge_id),
                    judge=ee.judge)
    client, chk, _, _ = make(tmp_path, nim=NIM(backend="offline"))
    chk.build, chk.judge = chk_args["build"], chk_args["judge"]
    p = client.get(f"/api/pharos/audit/{GENE_PRODUCT}").json()
    assert p["available"] is True and p["claim_kind"] == "gene and its protein product"
    assert p["judgement"]["label"] == "supported" and p["judgement"]["items"] == ["UP1"] and p["judgement"]["raters"] == 1
    assert p["sources"][0]["source"] == "UniProt" and p["sources"][0]["url"] == "https://www.uniprot.org/uniprotkb/P00533/entry"
    assert no_secret(p)


def test_real_judge_makes_one_request_inside_the_budget_and_leaves_the_app_client_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    nim = NIM(backend="nim")
    roles_before, snap_before = dict(nim.cfg["roles"]), nim.snap
    posts = []

    def post(url, json=None, headers=None, timeout=None):
        posts.append(SimpleNamespace(url=url, body=json, headers=headers, timeout=timeout))
        reply = {"label": "supported", "rationale": "IA1 is a physical association record.", "items": ["IA1"]}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": __import__("json").dumps(reply)}}], "usage": {}})

    monkeypatch.setattr(ra.requests, "post", post)
    fakes = Fakes()
    client, chk, _, _ = make(tmp_path, fakes, nim=nim, budget=5.0, collect=3.0)
    chk.judge = ee.judge
    p = client.get(f"/api/pharos/audit/{PPI}").json()
    assert p["available"] is True and p["judgement"]["label"] == "supported" and p["judgement"]["items"] == ["IA1"]
    call, = posts                                                                  # one request, no retry, no second model
    assert call.url.endswith("/chat/completions") and call.body["model"] == nim.cfg["spotcheck_raters"]["judge_b"]
    assert call.headers == {"Authorization": "Bearer test-key"} and 0 < call.timeout[1] <= 5.0
    assert no_secret(call.body) and "physically_interacts_with" in call.body["messages"][1]["content"]
    assert nim.cfg["roles"] == roles_before and nim.snap is snap_before


def test_one_shot_post_raises_after_the_deadline_and_on_http_errors(monkeypatch):
    with pytest.raises(TimeoutError):
        ra.OneShotPost(time.monotonic() - 1).post("nim", "https://example.org", {})

    def bad(status):
        def raise_():
            raise ra.requests.HTTPError(f"HTTP {status}")
        return lambda url, json=None, headers=None, timeout=None: SimpleNamespace(status_code=status, raise_for_status=raise_, json=dict)

    for status in (429, 500):                                                      # a rate limit is not waited for
        monkeypatch.setattr(ra.requests, "post", bad(status))
        with pytest.raises(ra.requests.HTTPError):
            ra.OneShotPost(time.monotonic() + 5).post("nim", "https://example.org", {})


@pytest.mark.parametrize("case", ["not_in_graph", "explain_raises", "no_model", "judge_fails", "judge_reply_unusable", "no_label",
                                  "no_source_answered", "no_time_for_judge", "bad_id"])
def test_every_failure_is_the_plain_sentence(tmp_path, case):
    fakes, nim, explain, args, eid = Fakes(), None, explained, {}, PPI
    if case == "not_in_graph":
        eid = "NCT00000001-link"
    elif case == "explain_raises":
        def explain(_):
            raise KeyError("index closed")
    elif case == "no_model":
        nim = NIM(backend="offline")
    elif case == "judge_fails":
        fakes = Fakes(judge_error=RuntimeError("HTTP 500 from https://integrate.api.nvidia.com"))
    elif case == "judge_reply_unusable":
        fakes = Fakes(problems=["cites an item that is not in the dossier: IA9"])
    elif case == "no_label":
        fakes = Fakes(label="keep")
    elif case == "no_source_answered":
        fakes = Fakes(sources=[source(status="failed", items=0, note="ConnectionError: x"), source("Reactome", "failed", 0, "x")])
    elif case == "no_time_for_judge":
        fakes, args = Fakes(delay=0.2), dict(budget=1.0, collect=0.3, min_judge=0.9)
    elif case == "bad_id":
        eid = "a..b"
    client, chk, fakes, _ = make(tmp_path, fakes, nim=nim, explain=explain, **args)
    r = client.get(f"/api/pharos/audit/{eid}")
    assert r.status_code == 200 and r.json() == {"edge_id": eid, "available": False, "message": ra.UNAVAILABLE}
    assert "nvidia" not in r.text and "Error" not in r.text
    assert chk.cached(eid) is None                                                 # a failure is never served as a result
    started = len(fakes.built)
    assert chk.failed_recently(eid) is bool(started)                               # a check that ran and failed is remembered
    assert client.get(f"/api/pharos/audit/{eid}").json()["available"] is False and len(fakes.built) == started
    if case in ("not_in_graph", "explain_raises", "no_model", "bad_id"):
        assert fakes.built == []                                                   # no outbound call was made for it
    if case in ("no_source_answered", "no_time_for_judge"):
        assert fakes.judged == []                                                  # the model was not called


def test_partial_result_says_so_and_is_checked_again_later(tmp_path):
    fakes = Fakes(sources=[source(), source("Europe PMC co-mention search", "failed", 0, "TimeoutError: time budget")], label="cannot tell")
    client, chk, _, _ = make(tmp_path, fakes, partial_ttl=1.0)
    p = client.get(f"/api/pharos/audit/{PPI}").json()
    assert p["complete"] is False and p["judgement"]["label"] == "cannot tell"
    assert p["note"] == "1 of 2 sources did not answer in full; the judgement rests on what the other returned."
    assert p["sources"][1]["returned"] == "This source did not answer."
    client.get(f"/api/pharos/audit/{PPI}")
    assert len(fakes.built) == 1                                                   # kept for a short while
    time.sleep(1.1)
    client.get(f"/api/pharos/audit/{PPI}")
    assert len(fakes.built) == 2                                                   # then checked again
    part = ra.public_payload({"sources": [{"source": "Open Targets literature sentences", "status": "failed", "items": [],
                                           "returned": "3 co-mentions. The query that includes more specific disease terms did not "
                                                       "answer; only the claimed term was searched."}],
                              "judgement": {"label": "cannot tell"}}, "e1", "stored")
    assert "answered only in part" in part["sources"][0]["returned"] and part["note"] == "None of the 1 sources answered in full."
    assert part["judgement"] == {"label": "cannot tell", "rationale": "", "raters": 0, "raters_agreeing": 0, "items": []}


def test_visitor_limit_counts_new_checks_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    client, chk, fakes, _ = make(tmp_path)
    write_stored(tmp_path / "dataroot" / "data" / "audit" / "edges", "e0stored")
    assert client.get(f"/api/pharos/audit/{PPI}").json()["available"] is True      # the visitor's one question
    r = client.get("/api/pharos/audit/e0another")
    body = r.json()
    assert r.status_code == 429 and body["remaining"] == 0 and body["limit"] == 1 and body["available"] is False
    assert int(r.headers["Retry-After"]) == body["retry_after"] > 0 and "independent checks per visitor per hour" in body["detail"]
    assert len(fakes.built) == 1                                                   # the refused check made no outbound call
    assert client.get(f"/api/pharos/audit/{PPI}").json()["origin"] == "on_demand"  # cached: still served
    assert client.get("/api/pharos/audit/e0stored").json()["origin"] == "stored"   # stored: still served
    assert client.get("/api/pharos/audit/NCT1-link").status_code == 200            # an unavailable edge is not counted either


def test_checks_per_minute_are_capped_for_all_visitors_together(tmp_path):
    client, chk, fakes, host = make(tmp_path, per_minute=2)
    assert [client.get(f"/api/pharos/audit/e0{i}").json()["available"] for i in range(4)] == [True, True, False, False]
    assert len(fakes.built) == 2 and chk.reserve("e0new") == ("busy", None)
    status, ticket = chk.reserve("e0new", now=time.monotonic() + 61)               # a minute later there is room again
    assert status == "go"
    chk.release("e0new", ticket)


def test_one_check_per_edge_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "10")
    client, chk, fakes, host = make(tmp_path, Fakes(delay=0.3))
    out = []
    threads = [threading.Thread(target=lambda: out.append(client.get(f"/api/pharos/audit/{PPI}").json())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(out) == 4 and all(p["available"] and p["judgement"]["label"] == "supported" for p in out)
    assert len(fakes.built) == 1 and len(fakes.judged) == 1                        # the four requests shared one check
    assert sum(len(v) for v in host.ask_log.values()) == 1                         # and one question was counted


def test_cache_is_bounded(tmp_path):
    client, chk, fakes, _ = make(tmp_path, cache_max=2)
    for i in range(3):
        client.get(f"/api/pharos/audit/e0{i}")
    assert chk.cached("e00") is None and chk.cached("e01") is not None and chk.cached("e02") is not None


def test_judge_model_choice(monkeypatch):
    nim = hosted_nim()
    assert ra.judge_model(nim) == "judge-b"
    monkeypatch.setenv("TMKG_AUDIT_MODEL", "other/model")
    assert ra.judge_model(nim) == "other/model"
    monkeypatch.delenv("TMKG_AUDIT_MODEL")
    assert ra.judge_model(SimpleNamespace(cfg={"spotcheck_raters": {"judge_a": "judge-a"}})) == "judge-a"
    assert ra.judge_model(SimpleNamespace(cfg={})) == ""
    real = NIM(backend="offline")
    assert ra.judge_model(real) == real.cfg["spotcheck_raters"]["judge_b"]         # the committed configuration names it


# ------------------------------------------------------------------------------------------------ the real app
def test_the_app_serves_the_endpoint_and_does_not_limit_stored_checks(tmp_path, monkeypatch):
    assert "/api/pharos/audit/{edge_id}" in appmod.app.openapi()["paths"]
    assert not any(p.startswith("/api/pharos/audit") for p in appmod.ASK_PATHS)    # the router counts new checks itself
    old = dict(appmod._pharos)
    appmod._pharos.clear()
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(tmp_path))
    monkeypatch.setenv("TMKG_PHAROS_INDEX", "off")
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    try:
        write_stored(tmp_path / "data" / "audit" / "edges", PPI)
        client = TestClient(appmod.app)
        for _ in range(3):
            r = client.get(f"/api/pharos/audit/{PPI}")
            assert r.status_code == 200 and r.json()["origin"] == "stored" and r.json()["judgement"]["label"] == "supported"
        assert "origin" not in client.get("/api/pharos/audit/..%2F..%2Fsecret").text
    finally:
        appmod._pharos.clear()
        appmod._pharos.update(old)


def test_outbound_hosts_are_documented_for_the_deployment():
    """Every host a collector or the judge calls is named in aws/README.md, and the collectors still call each of them."""
    import re
    import yaml
    root = Path(__file__).resolve().parents[1]
    readme = (root / "aws" / "README.md").read_text()
    code = (root / "pipelines" / "edge_evidence.py").read_text()
    section = readme[readme.index("## Independent check on an edge: outbound hosts"):]
    section = section[:section.index("\n## ", 5)]
    hosts = ["www.ebi.ac.uk", "reactome.org", "api.platform.opentargets.org", "rest.uniprot.org", "www.wikipathways.org", "string-db.org"]
    for host in hosts:
        assert f"`{host}`" in section and f"https://{host}/" in code, host
    # the other hosts in the collectors' code are pages the items link to (opened by the reader's browser, not by the server)
    links_only = {"europepmc.org", "platform.opentargets.org", "www.mousephenotype.org", "www.uniprot.org"}
    assert set(re.findall(r"https://([a-z0-9.\-]+)/", code)) == set(hosts) | links_only
    base_url = yaml.safe_load((root / "config" / "nim_models.yaml").read_text())["base_url"]
    assert re.match(r"https://([^/]+)/", base_url).group(1) in section
    assert "TMKG_AUDIT_PER_MINUTE" in section and "TMKG_AUDIT_MODEL" in section and "data/audit/edges" in section


def test_failed_check_is_not_repeated_or_counted_again_for_a_while(tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "5")
    fakes = Fakes(judge_error=RuntimeError("model down"))
    client, chk, _, host = make(tmp_path, fakes, failed_ttl=0.5)
    for _ in range(3):
        assert client.get(f"/api/pharos/audit/{PPI}").json() == {"edge_id": PPI, "available": False, "message": ra.UNAVAILABLE}
    assert len(fakes.built) == 1 and sum(len(v) for v in host.ask_log.values()) == 1
    time.sleep(0.6)
    fakes.judge_error = None                                                       # the model is back
    assert client.get(f"/api/pharos/audit/{PPI}").json()["available"] is True and len(fakes.built) == 2
    assert chk.failed_recently(PPI) is False


def test_reserve_is_one_step_and_release_gives_the_slot_back(tmp_path):
    _, chk, fakes, _ = make(tmp_path, workers=2, per_minute=3)
    s1, t1 = chk.reserve("e01")
    assert s1 == "go" and chk.reserve("e01") == ("join", t1)                       # a second request can only join
    s2, t2 = chk.reserve("e02")
    assert s2 == "go" and chk.reserve("e03") == ("busy", None)                     # two workers: no third check, nothing queues
    chk.release("e02", t2, "refused")
    with pytest.raises(ra.Unavailable):
        chk.wait(t2)                                                               # whoever joined it is told
    s3, t3 = chk.reserve("e03")
    assert s3 == "go"                                                              # the slot and the per-minute count came back
    chk.start("e01", t1, ra.blind_edge(explained(PPI)), hosted_nim(), "judge-b")
    assert chk.wait(t1)["judgement"]["label"] == "supported" and chk.reserve("e01")[0] == "go"
    chk.release("e03", t3)
    assert chk.check("e04", ra.blind_edge(explained(PPI)), hosted_nim(), "judge-b")["edge_id"] == "e04"
    assert chk.reserve("e05") == ("busy", None)                                    # three were started this minute


def test_visitor_refusal_gives_the_slot_back(tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    client, chk, fakes, _ = make(tmp_path, per_minute=2)
    assert client.get(f"/api/pharos/audit/{PPI}").json()["available"] is True
    for _ in range(3):
        assert client.get("/api/pharos/audit/e0other").status_code == 429
    status, ticket = chk.reserve("e0free")                                         # the refused requests used no slot
    assert status == "go"
    chk.release("e0free", ticket)


def test_technical_text_and_malformed_stored_records_never_reach_the_page(tmp_path):
    client, _, _, _ = make(tmp_path)
    folder = tmp_path / "dataroot" / "data" / "audit" / "edges"
    bad = "UniProt could not decide: HTTPError: 500 Server Error for url: https://rest.uniprot.org/uniprotkb/P1.json"
    write_stored(folder, "e0tech", judgement={"label": "cannot tell", "rationale": bad, "raters": "three", "raters_agreeing": None, "items": "IA1"})
    r = client.get("/api/pharos/audit/e0tech")
    j = r.json()["judgement"]
    assert r.status_code == 200 and j == {"label": "cannot tell", "rationale": ra.NO_DECISION, "raters": 0, "raters_agreeing": 0, "items": []}
    assert "rest.uniprot.org" not in r.text and "HTTPError" not in r.text
    assert ra.plain_rationale("Reactome: TimeoutError: time budget for the independent check is used up.") == ra.NO_DECISION
    assert ra.plain_rationale("IA1 shows binding. Two. Three.") == "IA1 shows binding. Two."
    write_stored(folder, "e0odd", sources=[{"source": "IntAct", "status": "answered", "items": "none", "returned": 5}])
    odd = client.get("/api/pharos/audit/e0odd")
    assert odd.status_code == 200 and odd.json()["sources"][0]["items"] == [] and odd.json()["sources"][0]["returned"] == "5"


def test_failing_model_call_is_one_request_no_second_model_no_stand_in(tmp_path, monkeypatch):
    """The app's client answers with a stand-in or a backup model when a hosted call fails; the judge must not."""
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("TMKG_AUDIT_MODEL", NIM(backend="offline").cfg["spotcheck_raters"]["judge_a"])
    nim = NIM(backend="nim", fallback="offline")
    posts = []

    def post(url, json=None, headers=None, timeout=None):
        posts.append(json["model"])
        raise ra.requests.ConnectionError("no route")

    monkeypatch.setattr(ra.requests, "post", post)
    client, chk, fakes, _ = make(tmp_path, nim=nim)
    chk.judge = ee.judge
    assert client.get(f"/api/pharos/audit/{PPI}").json() == {"edge_id": PPI, "available": False, "message": ra.UNAVAILABLE}
    assert posts == [nim.cfg["spotcheck_raters"]["judge_a"]] and nim.fallbacks == [] and nim.fallback == "offline"


def test_daily_limit_for_all_visitors_counts_new_checks(tmp_path, monkeypatch):
    from collections import deque
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_DAY", "2")
    client, chk, fakes, host = make(tmp_path)
    day = deque()
    host.day_allowed = lambda now, limit, record=True: appmod.day_allowed(now, limit, log=day, record=record)
    write_stored(tmp_path / "dataroot" / "data" / "audit" / "edges", "e0stored")
    assert [client.get(f"/api/pharos/audit/e0{i}").json()["available"] for i in range(2)] == [True, True]
    assert client.get("/api/pharos/audit/e00").json()["origin"] == "on_demand" and len(day) == 2      # cached: not counted
    r = client.get("/api/pharos/audit/e0third")
    assert r.status_code == 429 and r.json()["scope"] == "day" and r.json()["limit"] == 2 and "a day for all visitors" in r.json()["detail"]
    assert len(fakes.built) == 2 and len(day) == 2 and client.get("/api/pharos/audit/e0stored").json()["origin"] == "stored"
    status, ticket = chk.reserve("e0free")                                         # the refused check used no slot
    assert status == "go"
    chk.release("e0free", ticket)


# ------------------------------------------------------------------------------------------------ FIX2 F1: an outage is never "unsupported"
class DeadFetcher:
    """Every outbound call fails, as in the S08 / Gate 2 probe (the outside sources blocked)."""

    def __init__(self):
        self.calls = 0

    def _fail(self, *args, **kwargs):
        self.calls += 1
        raise ConnectionError("blocked")

    json = post = text = _fail


def outage_lookups():
    return SimpleNamespace(gene=lambda node_id: {"symbol": "EGFR", "ensembl": "ENSG00000146648", "uniprot": "P00533"},
                           disease_names=lambda node_id, name: [name], open_targets_ids=lambda node_id: ["MONDO_0005138"],
                           narrower=lambda node_id: [])


def unsupported_nim():
    """A model client whose judge always replies 'unsupported' (citing nothing)."""
    def chat(role, messages, schema=None):
        unsupported_nim.calls += 1
        return SimpleNamespace(data={"label": "unsupported", "rationale": "No source holds a record for the pair.", "items": []}, key="")
    return SimpleNamespace(backend="nim", chat=chat, cfg={"roles": {}, "spotcheck_raters": {"judge_b": "judge-b"}})


GD_EDGE = "e00000000000000c3"


def explained_outage(eid):
    if eid == GD_EDGE:
        return {"id": eid, "subject": {"id": "NCBIGene:1956", "symbol": "EGFR"},
                "object": {"id": "MONDO:0005233", "name": "non-small cell lung carcinoma"},
                "provenance": {"subject": "NCBIGene:1956", "predicate": "biolink:gene_associated_with_condition",
                               "object": "MONDO:0005233", "source": "DisGeNET", "primary_knowledge_source": "infores:disgenet",
                               "n_records": "1"}, "properties": {}}
    return explained(eid)


@pytest.mark.parametrize("eid", [PPI, GD_EDGE])
def test_outage_with_a_judge_saying_unsupported_is_never_published_unsupported(tmp_path, eid):
    """The probe's set-up: real collectors, every send failing, a judge that replies 'unsupported'. Gene-disease and
    protein-protein links (whose own-references source is 'not applicable') must not read 'unsupported'; no source
    answered, so the judge is not called and the visitor reads the one-sentence 'unavailable'."""
    fetch = DeadFetcher()
    unsupported_nim.calls = 0
    client, chk, _, _ = make(tmp_path, nim=unsupported_nim(), explain=explained_outage, budget=5.0, collect=3.0)
    chk.build = lambda edge, f, lookups, edge_id="": ee.build_dossier(edge, fetch, outage_lookups(), today="2026-11-14", edge_id=edge_id)
    chk.judge = ee.judge
    dossier = ee.build_dossier(ra.blind_edge(explained_outage(eid)), DeadFetcher(), outage_lookups(), today="2026-11-14")
    assert dossier["group"] == ("protein_protein" if eid == PPI else "gene_disease")
    assert {s["status"] for s in dossier["sources"]} <= {"failed", "not applicable", "not found"}
    assert not all(s["status"] == "failed" for s in dossier["sources"])          # the old guard did not fire for these
    p = client.get(f"/api/pharos/audit/{eid}").json()
    assert p == {"edge_id": eid, "available": False, "message": ra.UNAVAILABLE}
    assert fetch.calls > 0 and unsupported_nim.calls == 0                          # collectors tried; the judge was not asked


def test_unsupported_with_a_failed_required_source_becomes_cannot_tell(tmp_path):
    d = {"group": "protein_protein", "required": ["IntAct", "Reactome"], "collected": "2026-11-14",
         "edge": {"subject_id": "UniProtKB:P01112", "object_id": "UniProtKB:P04049"},
         "sources": [source(), source("Reactome", "failed", 0, "ConnectionError: x")]}
    ee.number_items(d["sources"])
    j = ee.check_label({"label": "unsupported", "rationale": "Nothing links the pair.", "items": []}, d)
    assert j["label"] == "cannot tell" and j["label_raw"] == "unsupported" and j["problems"] == []
    assert j["rationale"].startswith("Reactome did not answer, so finding nothing cannot be read as unsupported.")
    # a failed source that is not required, or a 'supported' reply, is left alone
    d2 = dict(d, required=["IntAct"])
    assert ee.check_label({"label": "unsupported", "rationale": "x", "items": []}, d2)["label"] == "unsupported"
    assert ee.check_label({"label": "supported", "rationale": "IA1", "items": ["IA1"]}, d)["label"] == "supported"
    # the same through the on-demand route: one required source failed, the judge says 'unsupported'
    fakes = Fakes(sources=[source(), source("Reactome", "failed", 0, "ConnectionError: x")])
    client, chk, _, _ = make(tmp_path, fakes)
    chk.judge = lambda dossier, nim, model: ee.check_label(
        {"label": "unsupported", "rationale": "Nothing links the pair.", "items": []}, dict(dossier, required=["IntAct", "Reactome"]))
    p = client.get(f"/api/pharos/audit/{PPI}").json()
    assert p["available"] is True and p["judgement"]["label"] == "cannot tell"
    assert p["note"] == "1 of 2 sources did not answer in full; the judgement rests on what the other returned."


def test_all_sources_answered_and_unsupported_is_unchanged(tmp_path):
    fakes = Fakes(sources=[source(items=0, note="no record for this pair"), source("Reactome", items=0, note="no shared reaction")])
    client, chk, _, _ = make(tmp_path, fakes)
    chk.judge = lambda dossier, nim, model: ee.check_label(
        {"label": "unsupported", "rationale": "No source holds a record for the pair.", "items": []}, dict(dossier, required=["IntAct", "Reactome"]))
    p = client.get(f"/api/pharos/audit/{PPI}").json()
    assert p["available"] is True and p["judgement"]["label"] == "unsupported" and p["complete"] is True and p["note"] == ""


def test_note_says_when_the_sources_that_did_not_fail_had_nothing_to_look_up():
    rec = {"group": "gene_disease", "checked": "2026-11-14",
           "sources": [source("Open Targets", "failed", 0, "x"), source("The row's own references", "not applicable", 0, "the row cites no paper")],
           "judgement": {"label": "cannot tell", "rationale": "x", "raters": 1, "raters_agreeing": 1, "items": []}}
    p = ra.public_payload(rec, "e1", "stored")
    assert p["note"] == "1 of 2 sources did not answer in full, and the other had nothing to look up for this link."
    rec["sources"].append(source("Open Targets literature sentences", "answered", 0, "none"))
    assert ra.public_payload(rec, "e1", "stored")["note"] == (
        "1 of 3 sources did not answer in full; the judgement rests on what the one that had something to look up returned.")


def test_raters_note_is_read_from_the_spot_check_result_and_reaches_the_payload(tmp_path):
    """FIX2 F3 (G2-AC-06): under every independent-check judgement, how often the automated raters agreed with the
    scientist's blind labels; both numbers come from the result file, none is typed."""
    import numbers_page as npg
    f = tmp_path / "image" / npg.SPOTCHECK_RESULT
    f.parent.mkdir(parents=True)
    f.write_text("Owner's blind labels against the machine label (majority of the three raters) on the rows she rated: "
                 "5 of 31 agree (16.1%), Cohen's kappa 0.01.\n", encoding="utf-8")
    assert ra.raters_note([tmp_path / "dataroot", tmp_path / "image"]) == (
        "Automated raters. On the 31 links a scientist rated blind, they agreed with her on 5.")
    assert ra.raters_note([tmp_path / "nowhere"]) == ""
    client, _, _, _ = make(tmp_path)
    write_stored(tmp_path / "image" / ra.STORED_REL, PPI)
    p = client.get(f"/api/pharos/audit/{PPI}").json()
    assert p["raters_note"] == "Automated raters. On the 31 links a scientist rated blind, they agreed with her on 5."
    committed = ra.raters_note([Path(__file__).resolve().parents[1]])
    if committed:                                                                  # the shipped file: 7 of 39 on 14 Nov
        assert committed.startswith("Automated raters. On the ") and "rated blind, they agreed with her on " in committed
