"""Bring-your-own-table endpoints (pipelines/routes_byo.py): size caps, the approval gate, the visitor limit, the
temporary-directory rule and plain error answers. No test here calls a model: the client is replaced by an offline one,
so the fixed rules draft."""
import json, sys, tempfile, time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))
import app as appmod  # noqa: E402
import card_drafter as cd  # noqa: E402
import routes_byo  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HEADER, ROWS = cd.read_example_table("screen")
TABLE = cd.table_text(HEADER, ROWS)


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.setattr(routes_byo, "model_client", lambda: type("OfflineNIM", (), dict(backend="offline"))())
    monkeypatch.delenv("TMKG_ASK_LIMIT_PER_HOUR", raising=False)
    monkeypatch.setattr(appmod, "_ask_log", {})


@pytest.fixture
def client():
    return TestClient(appmod.app)


def drafted(client, table=TABLE, **extra):
    r = client.post("/api/byo/draft", json=dict(table=table, **extra))
    assert r.status_code == 200, r.text
    return r.json()


def tree_state(*dirs):
    out = {}
    for d in dirs:
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


# ---------------------------------------------------------------- wiring
def test_router_is_included_once_and_the_draft_is_a_limited_path():
    paths = appmod.app.openapi()["paths"]
    assert set(paths["/api/byo/examples"]) == {"get"} and set(paths["/api/byo/example/{example_id}"]) == {"get"}
    assert set(paths["/api/byo/draft"]) == {"post"} and set(paths["/api/byo/build"]) == {"post"}
    assert routes_byo.DRAFT_PATH in appmod.ASK_PATHS and routes_byo.BUILD_PATH not in appmod.ASK_PATHS
    assert (ROOT / "pipelines" / "app.py").read_text().count("include_router(routes_byo.router)") == 1


def test_model_client_is_made_once_without_the_batch_fallbacks(monkeypatch):
    monkeypatch.undo()                                                    # the real function, not the fixture's replacement
    monkeypatch.setattr(routes_byo, "_nim_box", {})
    first = routes_byo.model_client()
    assert first is routes_byo.model_client() and first.fallback == "none" and first.backend in ("nim", "offline")


# ---------------------------------------------------------------- stored examples
def test_stored_examples_are_listed_and_served(client):
    listing = client.get("/api/byo/examples").json()
    assert [x["id"] for x in listing["examples"]] == ["screen", "gene_protein"] and all(x["available"] for x in listing["examples"])
    assert listing["limits"] == dict(rows=200, kilobytes=256, columns=40, sample_rows=20, rounds=3, draft_valid_minutes=60)
    for x in listing["examples"]:
        run = client.get(f"/api/byo/example/{x['id']}").json()
        assert run == cd.stored_run(x["id"])
        assert set(run) == {"id", "title", "label", "source_named", "hint", "table", "draft", "build"}     # rows, mapping, messages, edges
        assert run["table"]["rows"] and run["draft"]["decisions"] and run["draft"]["report"]["rounds"] and run["build"]["edges"]
    assert "SYNTHETIC" in listing["examples"][0]["label"] and "HGNC" in listing["examples"][1]["source_named"]


def test_unknown_example_ids_are_404(client):
    for bad in ("nope", "screen.json", "..%2F..%2FCLAUDE", "%2Fetc%2Fpasswd"):
        r = client.get(f"/api/byo/example/{bad}")
        assert r.status_code == 404 and "CLAUDE" not in r.text


# ---------------------------------------------------------------- draft
def test_draft_returns_card_report_decisions_and_token(client):
    d = drafted(client, hint="compound screen")
    assert d["ok"] and d["rows"] == 8 and d["sample_rows"] == 8 and d["limits"]["rows"] == 200
    assert d["drafted_by"]["kind"] == "rules" and "no language model is configured here" in d["drafted_by"]["text"]
    assert d["report"]["rounds"] == [dict(round=1, drafted_by="rules", problems=[])] and d["report"]["sample"]["edges"] == 6
    assert "biolink:directly_physically_interacts_with" in d["card_yaml"] and len(d["decisions"]) == len(HEADER) + 2
    assert routes_byo.token_valid(d["draft_token"], routes_byo.table_digest(HEADER, ROWS))
    r = client.post("/api/byo/draft", json=dict(table=TABLE))
    assert r.headers["cache-control"] == "no-store"


def test_draft_uses_only_the_first_twenty_rows_as_the_sample(client, monkeypatch):
    seen = {}
    real = cd.draft_card
    def spy(header, sample_rows, hint=None, **k):
        seen["n"] = len(sample_rows)
        return real(header, sample_rows, hint, **k)
    monkeypatch.setattr(cd, "draft_card", spy)
    rows = [[f"CHEMBL{i}", "P00533", "50", "yes", "x"] for i in range(1, 121)]
    d = drafted(client, table=cd.table_text(HEADER, rows))
    assert seen["n"] == 20 and d["rows"] == 120 and d["sample_rows"] == 20


def test_draft_that_finds_no_mapping_is_an_honest_200(client):
    d = drafted(client, table="sample\tcomment\ns1\tlooks fine\ns2\trepeat this one\n")
    assert d["ok"] is False and d["card"] is None and "nothing was accepted" in d["message"]


# ---------------------------------------------------------------- size caps
def test_size_caps_answer_413_with_a_plain_sentence(client):
    line = "CHEMBL1\tP00533\t1\tyes\tx\n"
    head = "\t".join(HEADER) + "\n"
    assert client.post("/api/byo/draft", json=dict(table=head + line * 200)).status_code == 200            # 200 rows: allowed
    r = client.post("/api/byo/draft", json=dict(table=head + line * 201))
    assert r.status_code == 413 and r.json()["detail"] == "The table has 201 rows; the limit is 200. Paste a smaller slice."
    big = head + ("CHEMBL1\tP00533\t1\tyes\t" + "x" * 1900 + "\n") * 150                                   # under 200 rows, over 256 KB
    assert len(big.encode()) > cd.MAX_BYTES
    r = client.post("/api/byo/draft", json=dict(table=big))
    assert r.status_code == 413 and "the limit is 256 KB" in r.json()["detail"]
    for path in ("/api/byo/draft", "/api/byo/build"):                                                    # body cap: refused before parsing
        r = client.post(path, content=b'{"table": "' + b"x" * (routes_byo.BODY_CAP + 10) + b'"}', headers={"Content-Type": "application/json"})
        assert r.status_code == 413 and "at most 256 KB and 200 rows" in r.json()["detail"]
    r = client.post("/api/byo/build", json=dict(table=head + line * 201, card_yaml="x", approved=True, draft_token="t"))
    assert r.status_code == 413


def test_body_cap_is_enforced_while_reading_when_no_length_is_declared(client):
    def chunks():
        yield b'{"table": "'
        for _ in range(routes_byo.BODY_CAP // 65536 + 2):
            yield b"x" * 65536
        yield b'"}'
    r = client.post("/api/byo/draft", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_bad_requests_are_400_with_a_plain_sentence(client):
    for kwargs in (dict(content=b"not json"), dict(json=[1, 2]), dict(json={}), dict(json=dict(table=5)),
                   dict(json=dict(table="a,b\n1,2\n")), dict(json=dict(table=TABLE, hint="x" * 301)),
                   dict(json=dict(table=TABLE, source_name=["x"])), dict(json=dict(table=TABLE, hint="two\nlines"))):
        r = client.post("/api/byo/draft", **kwargs)
        assert r.status_code == 400 and isinstance(r.json()["detail"], str), kwargs


# ---------------------------------------------------------------- the scientist's gate
def test_build_runs_only_after_approve_mapping(client):
    d = drafted(client)
    body = dict(table=TABLE, card_yaml=d["card_yaml"], draft_token=d["draft_token"])
    for approved in (None, False, "true", 1, "yes"):
        r = client.post("/api/byo/build", json=dict(body, approved=approved) if approved is not None else body)
        assert r.status_code == 409 and "has not been approved" in r.json()["detail"] and "Approve mapping" in r.json()["detail"]
    r = client.post("/api/byo/build", json=dict(body, approved=True))
    assert r.status_code == 200 and r.json()["ok"] and r.json()["counts"]["edges"] == 6


def test_build_needs_a_current_draft_of_the_same_table(client, monkeypatch):
    d = drafted(client)
    body = dict(table=TABLE, card_yaml=d["card_yaml"], approved=True)
    other = cd.table_text(HEADER, ROWS[:5])
    for token, table in ((None, TABLE), ("", TABLE), ("abc", TABLE), ("1.2.3", TABLE), (d["draft_token"][:-1] + ("0" if d["draft_token"][-1] != "0" else "1"), TABLE),
                         (d["draft_token"], other), (drafted(client, table=other)["draft_token"], TABLE)):
        r = client.post("/api/byo/build", json=dict(body, table=table, draft_token=token))
        assert r.status_code == 409 and "no current draft" in r.json()["detail"], token
    digest = routes_byo.table_digest(HEADER, ROWS)
    now = time.time()
    assert routes_byo.token_valid(routes_byo.make_token(digest, now - 3599), digest, now)
    assert not routes_byo.token_valid(routes_byo.make_token(digest, now - 3601), digest, now)          # older than an hour
    assert not routes_byo.token_valid(routes_byo.make_token(digest, now + 60), digest, now)            # from the future
    token = d["draft_token"]
    monkeypatch.setattr(routes_byo, "_secret", b"another process")                                    # after a restart: draft again
    assert client.post("/api/byo/build", json=dict(body, draft_token=token)).status_code == 409


def test_same_table_pasted_with_other_line_endings_keeps_its_draft(client):
    d = drafted(client)
    r = client.post("/api/byo/build", json=dict(table=TABLE.replace("\n", "\r\n") + "\r\n", card_yaml=d["card_yaml"],
                                                approved=True, draft_token=d["draft_token"]))
    assert r.status_code == 200


def test_build_returns_edges_with_provenance_and_verdict_fields(client):
    d = drafted(client, source_name="Kinase panel, lab book 12")
    r = client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=d["card_yaml"], approved=True, draft_token=d["draft_token"],
                                                source_name="Kinase panel, lab book 12"))
    b = r.json()
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert set(b) == {"ok", "nodes", "edges", "not_mapped", "problems", "counts", "verdict_note", "value_notes", "card_yaml"}
    assert len(b["nodes"]) == 9 and len(b["edges"]) == 6
    for e in b["edges"]:
        assert set(e) == {"id", "subject", "predicate", "object", "provenance", "properties", "verdict"}
        assert e["provenance"]["source"] == "SYNTHETIC Kinase panel, lab book 12"                       # the table says synthetic
        assert e["provenance"]["primary_knowledge_source"] == "tmkg:pasted-table" and e["provenance"]["source_record"]
        assert set(e["verdict"]) == {"verdict", "confidence", "reasons"}


def test_an_edited_card_is_checked_again_and_refused_with_the_validators_messages(client):
    d = drafted(client)
    ok = dict(table=TABLE, approved=True, draft_token=d["draft_token"])
    edits = [(d["card_yaml"] + "lookups:\n  x: {ref_file: CLAUDE.md, key: a, value: b}\n", "keys not allowed in a drafted card: lookups"),
             (d["card_yaml"].replace("column: target_uniprot", "column: nowhere"), "is not a column of the table"),
             (d["card_yaml"].replace("biolink:Protein", "biolink:Cell"), "not in the allowed vocabulary"),
             (d["card_yaml"].replace("column: target_uniprot", "column: assay"), "does not look like UniProtKB identifiers"),
             ("edges: !!python/object/apply:os.system ['id']\n", "not valid YAML"), ("x" * 30000, "longer than")]
    for text, expect in edits:
        r = client.post("/api/byo/build", json=dict(ok, card_yaml=text))
        b = r.json()
        assert r.status_code == 422 and not b["ok"] and b["edges"] == [] and b["nodes"] == []
        assert any(expect in p for p in b["problems"]), (expect, b["problems"])
    assert client.post("/api/byo/build", json=dict(ok, card_yaml=None)).status_code == 400


def test_a_card_cannot_choose_its_own_source_table_or_knowledge_source(client):
    d = drafted(client)
    forged = d["card_yaml"].replace("table: table", "table: ../../data/internal/x").replace("tmkg:pasted-table", "infores:chembl") \
        .replace("source: SYNTHETIC pasted table", "source: ChEMBL 37")
    assert forged != d["card_yaml"]
    b = client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=forged, approved=True, draft_token=d["draft_token"])).json()
    assert b["ok"] and {e["provenance"]["primary_knowledge_source"] for e in b["edges"]} == {"tmkg:pasted-table"}
    assert {e["provenance"]["source"] for e in b["edges"]} == {"SYNTHETIC pasted table"} and "infores:chembl" not in b["card_yaml"]


def test_a_hand_corrected_card_builds_after_approval(client):
    d = drafted(client)
    edited = d["card_yaml"].replace("hit: 'yes'", "hit: 'no'")                                          # the scientist changes the filter
    assert edited != d["card_yaml"]
    b = client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=edited, approved=True, draft_token=d["draft_token"])).json()
    assert b["ok"] and b["counts"]["edges"] == 2 and b["edges"][0]["verdict"] is not None


# ---------------------------------------------------------------- visitor limit
def test_draft_counts_toward_the_visitor_limit_and_build_does_not(client, monkeypatch):
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "2")
    h = {"X-Forwarded-For": "203.0.113.9"}
    first = client.post("/api/byo/draft", json=dict(table=TABLE), headers=h)
    assert first.status_code == 200 and first.headers["X-Ask-Remaining"] == "1"
    assert client.post("/api/byo/draft", json=dict(table="a,b\n1,2\n"), headers=h).status_code == 400     # a refused paste counts too
    third = client.post("/api/byo/draft", json=dict(table=TABLE), headers=h)
    assert third.status_code == 429 and "at most 2 questions per visitor per hour" in third.json()["detail"]
    d = first.json()
    build = dict(table=TABLE, card_yaml=d["card_yaml"], approved=True, draft_token=d["draft_token"])
    assert [client.post("/api/byo/build", json=build, headers=h).status_code for _ in range(3)] == [200, 200, 200]
    assert client.get("/api/byo/examples", headers=h).status_code == 200 and client.get("/api/byo/example/screen", headers=h).status_code == 200
    assert client.post("/api/byo/draft", json=dict(table=TABLE), headers={"X-Forwarded-For": "198.51.100.4"}).status_code == 200


# ---------------------------------------------------------------- nothing persists
def test_nothing_is_written_under_data_or_outputs_and_no_temporary_folder_survives(client, monkeypatch):
    made = []
    real = tempfile.TemporaryDirectory
    def spy(*a, **k):
        t = real(*a, **k)
        made.append(Path(t.name))
        return t
    monkeypatch.setattr(cd.tempfile, "TemporaryDirectory", spy)
    # only the folders a draft or a build could write to; other jobs may write elsewhere under data/ meanwhile
    watched = ("data/curated", "data/reference", "data/snapshots", "data/snapshots_llm", "data/synthetic", "outputs", "examples")
    before = tree_state(*watched)
    d = drafted(client, hint="compound screen")
    r = client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=d["card_yaml"], approved=True, draft_token=d["draft_token"]))
    assert r.status_code == 200 and len(made) == 2
    assert all(ROOT not in p.parents for p in made) and not any(p.exists() for p in made)
    assert tree_state(*watched) == before
    assert not hasattr(routes_byo, "_drafts") and set(routes_byo._nim_box) <= {"nim"}                   # no per-request state kept


def test_too_many_builds_at_once_answer_503(client):
    d = drafted(client)
    body = dict(table=TABLE, card_yaml=d["card_yaml"], approved=True, draft_token=d["draft_token"])
    assert routes_byo._builds.acquire(blocking=False) and routes_byo._builds.acquire(blocking=False)
    try:
        r = client.post("/api/byo/build", json=body)
        assert r.status_code == 503 and r.headers["Retry-After"] == "5"
    finally:
        routes_byo._builds.release(); routes_byo._builds.release()
    assert client.post("/api/byo/build", json=body).status_code == 200                                 # the slot was given back


def test_internal_errors_answer_a_plain_sentence(client, monkeypatch):
    d = drafted(client)
    def boom(*a, **k):
        raise OSError("/srv/app/data/internal/secret.tsv: permission denied")
    monkeypatch.setattr(cd, "build_small_graph", boom)
    r = client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=d["card_yaml"], approved=True, draft_token=d["draft_token"]))
    assert r.status_code == 500 and r.json() == {"detail": "The graph could not be built. Please try again."}
    monkeypatch.setattr(cd, "draft_card", boom)
    r = client.post("/api/byo/draft", json=dict(table=TABLE))
    assert r.status_code == 500 and "secret" not in r.text
    assert client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=d["card_yaml"], approved=True,
                                                   draft_token=d["draft_token"])).status_code == 500   # and the build slot is free again
    assert routes_byo._builds.acquire(blocking=False) and routes_byo._builds.acquire(blocking=False)
    routes_byo._builds.release(); routes_byo._builds.release()


def test_slow_request_body_times_out():
    import asyncio

    class SlowRequest:
        headers = {}
        async def stream(self):
            await asyncio.sleep(1)
            yield b"{}"
    with pytest.raises(routes_byo.HTTPException) as err:
        asyncio.run(routes_byo.read_json(SlowRequest(), seconds=0.05))
    assert err.value.status_code == 408


def test_answers_carry_no_file_names_or_internal_words(client):
    import re
    banned = re.compile(r"(\.tsv|\.ya?ml|\.py\b|data/|outputs/|pipelines/|examples/|/tmp|\bG[0-4]\b|\bPK\d|\bNIM\b|stand-in|fallback|snapshot|ref_file)")
    d = drafted(client, hint="compound screen")
    b = client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=d["card_yaml"], approved=True, draft_token=d["draft_token"])).json()
    refused = client.post("/api/byo/build", json=dict(table=TABLE, card_yaml=d["card_yaml"].replace("target_uniprot", "zz"),
                                                      approved=True, draft_token=d["draft_token"])).json()
    for answer in (d, b, refused, client.get("/api/byo/examples").json()):
        text = json.dumps({k: v for k, v in answer.items() if k != "draft_token"})
        assert not banned.search(text), banned.search(text).group(0)
