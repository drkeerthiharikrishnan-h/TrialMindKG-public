"""Tests for pipelines/pharos.py. Offline: a fake snapshot answers the GraphQL calls."""
import csv, gzip
import pytest
import pharos


class FakeResponse:
    def __init__(self, body):
        self.body, self.fetched_at, self.key = body, "2026-10-25T00:00:00Z", "k"


class FakeSnap:
    """Answers download() calls from a fixed table, honouring top/skip/batch like the Pharos API."""
    def __init__(self, table, fields):
        self.table, self.fields, self.queries = table, fields, []

    def path(self, source, key):
        from pathlib import Path
        return Path("/nonexistent") / key

    def post(self, source, url, body):
        q = body["query"]
        self.queries.append(q)
        if "dbVersion" in q:
            return FakeResponse({"data": {"dbVersion": "pharos319"}})
        if "sqlOnly:true" in q:
            return FakeResponse({"data": {"download": {"sql": "select protein.id as `id`, ..."}}})
        if "downloadLists" in q:
            return FakeResponse({"data": {"configuration": {"downloadLists": [
                {"listName": "Associated Diseases", "field": [{"name": f} for f in self.fields]}]}}})
        top = int(q.split("top:")[1].split(",")[0])
        skip = int(q.split("skip:")[1].split(",")[0])
        rows = self.table
        if "batch:" in q:
            acc = q.split('batch:"')[1].split('"')[0]
            rows = [r for r in rows if r["UniProt"] == acc]
        return FakeResponse({"data": {"download": {"result": True, "errorDetails": None,
                                                   "data": rows[skip:skip + top]}}})


TABLE = ([{"id": 1, "UniProt": "P1", "Associated Disease": f"d{i}"} for i in range(3)]
         + [{"id": 2, "UniProt": "P2", "Associated Disease": f"d{i}"} for i in range(4)]
         + [{"id": 3, "UniProt": "P3", "Associated Disease": "d0"}])


def test_pull_group_pages_and_repairs_split_targets():
    snap = FakeSnap(TABLE, ["UniProt", "Associated Disease"])
    fields, rows = pharos.pull_group(snap, "diseases", page=5, log=lambda *_: None)
    assert fields == ["UniProt", "Associated Disease"]
    assert rows == TABLE                        # target P2 was split across pages 1-2 and is complete, once
    assert any('batch:"P2"' in q for q in snap.queries)


def test_dedupe_keeps_order():
    a, b = {"id": 1, "x": "a"}, {"id": 1, "x": "b"}
    assert pharos.dedupe([a, b, dict(a)]) == [a, b]


def test_target_spanning_several_pages_is_complete_once():
    table = ([{"id": 1, "UniProt": "P1", "Associated Disease": "d0"}]
             + [{"id": 2, "UniProt": "P2", "Associated Disease": f"d{i}"} for i in range(9)]
             + [{"id": 3, "UniProt": "P3", "Associated Disease": "d0"}])
    _, rows = pharos.pull_group(FakeSnap(table, ["UniProt", "Associated Disease"]), "diseases", page=3,
                                log=lambda *_: None)
    assert rows == table


def test_download_error_raises(monkeypatch):
    monkeypatch.setattr(pharos.time, "sleep", lambda s: None)
    class Bad(FakeSnap):
        def post(self, source, url, body):
            return FakeResponse({"errors": [{"message": "boom"}]})
    with pytest.raises(pharos.PharosError):
        pharos.download(Bad([], []), "Target", ["UniProt"])


def test_write_table_and_manifest(tmp_path):
    p = tmp_path / "t.tsv.gz"
    n, targets, sha = pharos.write_table(["UniProt", "Score"], iter([{"id": 1, "UniProt": "P1", "Score": None}]), p)
    lines = gzip.decompress(p.read_bytes()).decode().splitlines()
    assert lines == ["id\tUniProt\tScore", "1\tP1\t"] and (n, targets, len(sha)) == (1, 1, 64)
    m = tmp_path / "m.tsv"
    pharos.write_manifest([dict(group="b", rows=1)], m)
    pharos.write_manifest([dict(group="a", rows=2)], m)          # merged, sorted by group
    with open(m) as f:
        assert [r["group"] for r in csv.DictReader(f, delimiter="\t")] == ["a", "b"]


def test_groups_have_known_models():
    assert {m for m, _ in pharos.GROUPS.values()} == {"Target", "Disease", "Ligand"}
    assert "expression" not in pharos.DEFAULT_GROUPS


def test_pull_group_halves_page_on_server_timeout():
    class Flaky(FakeSnap):
        def post(self, source, url, body):
            if "download(" in body["query"] and "top:8," in body["query"]:
                raise RuntimeError("HTTP 502")
            return super().post(source, url, body)
    old = pharos.MIN_PAGE
    pharos.MIN_PAGE = 2
    try:
        _, rows = pharos.pull_group(Flaky(TABLE, ["UniProt", "Associated Disease"]), "diseases", page=8,
                                    log=lambda *_: None)
    finally:
        pharos.MIN_PAGE = old
    assert rows == TABLE


def test_error_answer_is_not_kept_and_is_retried(tmp_path):
    from snapshot import Snapshot
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    answers = iter([{"errors": [{"message": "pool is full"}]}, {"data": {"dbVersion": "pharos319"}}])
    snap._http = lambda *a, **k: next(answers)
    assert pharos.query(snap, "{ dbVersion }", wait=0) == {"dbVersion": "pharos319"}
    assert pharos.query(snap, "{ dbVersion }", wait=0) == {"dbVersion": "pharos319"}   # the good answer is cached
    assert len(list(tmp_path.glob("pharos/*.json.gz"))) == 1


def test_pull_continues_after_a_failed_group(tmp_path, monkeypatch):
    monkeypatch.setattr(pharos, "TABLE_DIR", tmp_path)
    monkeypatch.setattr(pharos, "MANIFEST", tmp_path / "m.tsv")
    monkeypatch.setattr(pharos, "write_manifest", lambda e, path=None: None)

    def fake_iter(snap, g, fields, log=print):
        if g == "diseases":
            raise pharos.PharosError("boom")
        yield {"id": 1, "UniProt": "P1"}
    monkeypatch.setattr(pharos, "iter_group", fake_iter)
    monkeypatch.setattr(pharos, "group_fields", lambda snap, g: ["UniProt"])
    entries = pharos.pull(["diseases", "dto"], snap=FakeSnap([], ["UniProt"]), log=lambda *_: None)
    assert [e["group"] for e in entries] == ["dto"]



def test_no_split_repair_when_id_is_not_the_target_id():
    class Tiga(FakeSnap):
        def post(self, source, url, body):
            if "sqlOnly:true" in body["query"]:
                return FakeResponse({"data": {"download": {"sql": "select tiga.id as `id`, ..."}}})
            return super().post(source, url, body)
    table = [{"id": i, "UniProt": "P1" if i < 4 else "P2", "Associated Disease": f"d{i}"} for i in range(7)]
    snap = Tiga(table, ["UniProt", "Associated Disease"])
    _, rows = pharos.pull_group(snap, "diseases", page=3, log=lambda *_: None)
    assert rows == table and not any("batch:" in q for q in snap.queries)


def test_per_target_rows_fetches_each_target_once_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(pharos.time, "sleep", lambda s: None)
    fields = ["UniProt", "Associated Disease"]
    accs = ["P1", "P2", "P3"]
    progress = tmp_path / "ppi.progress.jsonl"

    class Crash(FakeSnap):
        """Fails on the second target, as a dropped connection would."""
        def post(self, source, url, body):
            if 'batch:"P2"' in body["query"] and not getattr(self, "fixed", False):
                raise RuntimeError("connection dropped")
            return super().post(source, url, body)

    snap = Crash(TABLE, fields)
    with pytest.raises(RuntimeError):
        list(pharos.per_target_rows(snap, "diseases", accs, fields, progress, log=lambda *_: None))
    assert [pharos.json.loads(l)["uniprot"] for l in progress.read_text().splitlines()] == ["P1"]

    with open(progress, "a") as f:
        f.write('{"uniprot": "P2", "ro')          # a line cut short by the crash
    snap.fixed = True
    rows = list(pharos.per_target_rows(snap, "diseases", accs, fields, progress, log=lambda *_: None))
    assert rows == TABLE                         # every target once, in order, none lost or doubled
    batches = [q.split('batch:"')[1].split('"')[0] for q in snap.queries if 'batch:"' in q]
    assert batches.count("P1") == 1              # P1 was saved before the crash: not fetched again


def test_target_accessions_lists_each_uniprot_once(monkeypatch):
    monkeypatch.setattr(pharos, "iter_group", lambda *a, **k: iter(
        [{"UniProt": "P1"}, {"UniProt": "P2"}, {"UniProt": "P1"}, {"UniProt": None}]))
    monkeypatch.setattr(pharos, "group_fields", lambda snap, g: ["UniProt"])
    assert pharos.target_accessions(object(), log=lambda *_: None) == ["P1", "P2"]


def test_large_groups_are_fetched_per_target():
    import pharos
    assert {"ppi", "tinx"} <= pharos.PER_TARGET_GROUPS


def test_reason_keeps_the_cause_not_the_query():
    e = RuntimeError("POST https://pharos-api.ncats.io/graphql {'query': '{ download(...) }'} failed after retries: HTTP 502")
    assert pharos.reason(e) == "HTTP 502"
    assert pharos.reason(pharos.PharosError("download failed: x")) == "download failed: x"


def test_query_retries_a_transport_failure(monkeypatch):
    """The snapshot gives up after ~15 s of quick retries; query() waits longer and asks again."""
    sleeps = []
    monkeypatch.setattr(pharos.time, "sleep", sleeps.append)
    answers = iter([RuntimeError("POST url {} failed after retries: HTTP 504"), RuntimeError("x failed after retries: HTTP 502"),
                    FakeResponse({"data": {"dbVersion": "pharos319"}})])

    class Snap(FakeSnap):
        def post(self, source, url, body):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a
    assert pharos.query(Snap([], []), "{ dbVersion }", wait=30, log=lambda *_: None) == {"dbVersion": "pharos319"}
    assert sleeps == [30, 60]


def test_query_raises_unavailable_after_every_attempt(monkeypatch):
    monkeypatch.setattr(pharos.time, "sleep", lambda s: None)
    calls = []

    class Down(FakeSnap):
        def post(self, source, url, body):
            calls.append(1)
            raise RuntimeError("POST url {} failed after retries: HTTP 502")
    with pytest.raises(pharos.PharosUnavailable, match="HTTP 502"):
        pharos.query(Down([], []), "{ dbVersion }", attempts=3, log=lambda *_: None)
    assert len(calls) == 3
    assert issubclass(pharos.PharosUnavailable, pharos.PharosError)   # pull() still catches it per group


def test_query_does_not_retry_offline_or_client_errors(monkeypatch):
    from snapshot import MissingFromSnapshot
    monkeypatch.setattr(pharos.time, "sleep", lambda s: None)
    for exc in (MissingFromSnapshot("not in snapshot"), PermissionError("HTTP 403")):
        calls = []

        class Snap(FakeSnap):
            def post(self, source, url, body):
                calls.append(1)
                raise exc
        with pytest.raises(type(exc)):
            pharos.query(Snap([], []), "{ dbVersion }", log=lambda *_: None)
        assert len(calls) == 1


def test_per_target_rows_pauses_through_an_outage_and_carries_on(tmp_path, monkeypatch):
    """Pharos is down for a while in the middle of target P2: the pull pauses, then resumes at P2 - nothing lost."""
    sleeps, logs = [], []
    monkeypatch.setattr(pharos.time, "sleep", sleeps.append)
    fields = ["UniProt", "Associated Disease"]
    failures = iter(range(7))                     # query() tries 5 times, then one pause, then 2 more failures

    class Outage(FakeSnap):
        def post(self, source, url, body):
            if 'batch:"P2"' in body["query"] and next(failures, None) is not None:
                raise RuntimeError("POST url {} failed after retries: HTTP 503")
            return super().post(source, url, body)
    snap = Outage(TABLE, fields)
    rows = list(pharos.per_target_rows(snap, "diseases", ["P1", "P2", "P3"], fields,
                                       tmp_path / "tinx.progress.jsonl", log=logs.append))
    assert rows == TABLE
    assert sleeps.count(pharos.OUTAGE_PAUSE) == 1 and any("pausing" in m for m in logs)
    batches = [q.split('batch:"')[1].split('"')[0] for q in snap.queries if 'batch:"' in q]
    assert batches.count("P1") == 1 and batches.count("P3") == 1


def test_per_target_rows_gives_up_after_outage_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(pharos.time, "sleep", lambda s: None)
    monkeypatch.setattr(pharos, "OUTAGE_LIMIT", 2)
    fields = ["UniProt", "Associated Disease"]

    class Down(FakeSnap):
        def post(self, source, url, body):
            if 'batch:"P2"' in body["query"]:
                self.queries.append(body["query"])
                raise RuntimeError("POST url {} failed after retries: HTTP 503")
            return super().post(source, url, body)
    snap = Down(TABLE, fields)
    progress = tmp_path / "tinx.progress.jsonl"
    with pytest.raises(pharos.PharosUnavailable, match="re-run to resume at P2"):
        list(pharos.per_target_rows(snap, "diseases", ["P1", "P2", "P3"], fields, progress, log=lambda *_: None))
    assert [pharos.json.loads(l)["uniprot"] for l in progress.read_text().splitlines()] == ["P1"]
    assert sum('batch:"P2"' in q for q in snap.queries) == 5 * 3     # 5 attempts x (1 + OUTAGE_LIMIT) rounds


def test_pull_logs_the_cause_of_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(pharos, "TABLE_DIR", tmp_path)
    monkeypatch.setattr(pharos, "write_manifest", lambda e, path=None: None)
    monkeypatch.setattr(pharos, "group_fields", lambda snap, g: ["UniProt"])

    def fake_iter(snap, g, fields, log=print):
        raise RuntimeError("POST https://pharos-api.ncats.io/graphql {'query': '{ download(" + "x" * 300
                           + ") }'} failed after retries: HTTP 502")
        yield
    monkeypatch.setattr(pharos, "iter_group", fake_iter)
    logs = []
    pharos.pull(["dto"], snap=FakeSnap([], ["UniProt"]), log=logs.append)
    assert logs == ["dto: FAILED (HTTP 502) - re-run `python pipelines/pharos.py pull dto` later"]


def test_fail_fast_still_retries_error_answers_but_not_transport(monkeypatch):
    monkeypatch.setattr(pharos.time, "sleep", lambda s: None)
    answers = iter([FakeResponse({"errors": [{"message": "pool is full"}]}), FakeResponse({"data": {"x": 1}})])

    class Snap(FakeSnap):
        def post(self, source, url, body):
            return next(answers)
    assert pharos.query(Snap([], []), "{ x }", log=lambda *_: None, fail_fast=True) == {"x": 1}
    calls = []

    class Down(FakeSnap):
        def post(self, source, url, body):
            calls.append(1)
            raise RuntimeError("x failed after retries: HTTP 504")
    with pytest.raises(pharos.PharosUnavailable):
        pharos.query(Down([], []), "{ x }", log=lambda *_: None, fail_fast=True)
    assert len(calls) == 1
