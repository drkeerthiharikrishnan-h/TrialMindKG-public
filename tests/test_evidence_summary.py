"""PK4: the model-written summary of an answer's evidence lines (pipelines/evidence_summary.py, routes_summary.py).

No network: the hosted model is a stand-in transport (Snapshot._http replaced) that answers the writer and the sentence
checker on command, times out, or is never reached. The Pharos side runs on the fixture graph, the stored answers are the
committed ones in outputs/examples."""
import json
import os
import sys
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import app as appmod  # noqa: E402
import evidence_summary as ES  # noqa: E402
import nim as nimmod  # noqa: E402
import routes_summary as RS  # noqa: E402
import stored_answers  # noqa: E402
from nim import NIM  # noqa: E402
from pharos_index import build_index  # noqa: E402
from snapshot import Snapshot  # noqa: E402

from fixtures.pharos_fixture import build, GENE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TESTED = "Which diseases is the gene PTGS2 associated with, and which source says so?"
INTERNAL = ("outputs/", "data/", ".json", ".tsv", "G0", "G2", "G3", "PK4", "stand-in", "fallback", "backend", "snapshot")


# ------------------------------------------------------------------------------------------------ the stand-in model
class Model:
    """A stand-in hosted model. writer: the summary text (or an exception to raise); verdicts: {sentence fragment: bool}
    for the checker (default True), or check_error to raise on every check. calls collects (role, body) per request."""
    def __init__(self, writer="", verdicts=None, check_error=None, check_reply=None):
        self.writer, self.verdicts, self.check_error, self.check_reply = writer, verdicts or {}, check_error, check_reply
        self.calls = []

    def http(self, method, url, params, body, headers=None, store_client_errors=True, budget=None):
        checking = "response_format" in body or "Sentence:" in body["messages"][-1]["content"] or any(
            "Sentence:" in m["content"] for m in body["messages"])
        self.calls.append(("check" if checking else "write", body))
        if checking:
            if self.check_error is not None:
                raise self.check_error
            if self.check_reply is not None:
                return {"choices": [{"message": {"content": self.check_reply}}], "usage": {}}
            sentence = next(m["content"] for m in body["messages"] if "Sentence:" in m["content"]).split("Sentence:", 1)[1]
            ok = all(v for k, v in self.verdicts.items() if k in sentence.split("\n\n")[0])
            return {"choices": [{"message": {"content": json.dumps({"supported": ok, "reason": "stand-in"})}}], "usage": {}}
        if isinstance(self.writer, Exception):
            raise self.writer
        return {"choices": [{"message": {"content": self.writer}}], "usage": {}}

    def n(self, kind):
        return sum(1 for k, _ in self.calls if k == kind)


def hosted(tmp_path, model):
    """A NIM on the stand-in transport, with fallback on (as in the app)."""
    snap = Snapshot(tmp_path / "llm", mode="live", sleep=0)
    snap._http = model.http
    n = NIM(backend="nim", snap=snap, fallback="offline")
    n.key = "nvapi-stub"
    return n


def client_of(tmp_path, model):
    """The writer / checker client of one request: nim.web_client on the stand-in base (30 s per call, one retry)."""
    return nimmod.web_client(ES.DEADLINE, base=hosted(tmp_path, model))


GOOD = ("PTGS2 links to Alzheimer disease through curated DisGeNET and Open Targets records [1]. A text-mined record for the "
        "same pair was pruned [1]. Type 2 diabetes rests on one curated CTD record [2]. The lines show no statistical or "
        "predicted links, and they say nothing about direction of effect or tissue.")


def lines_for(n=2):
    return [dict(n=i, id=f"row:e{i:016x}", kind="row", ref=str(i), text=f"line {i} text.") for i in range(1, n + 1)]


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    """Every test writes summaries into its own folder and starts with no limits and an empty memory cache."""
    monkeypatch.setenv("TMKG_SUMMARY_DIR", str(tmp_path / "summaries"))
    monkeypatch.setattr(ES, "FOLDER", tmp_path / "committed")
    # FIX1 F2 (gate 1, G1-SEC-03): NIM() reads the repo's .env into os.environ (nim.load_env). On the owner's Mac that file can
    # name the real card index (TMKG_PHAROS_INDEX), and the fixture tests then answered from the 26-million-edge graph: 4 to 5
    # failures that never showed where there is no .env. The tests read no .env, and the card index is pinned per client below.
    monkeypatch.setattr(nimmod.load_env, "__defaults__", (tmp_path / "no.env",))
    for var in ("TMKG_SUMMARY", "TMKG_ASK_LIMIT_PER_HOUR", "TMKG_ASK_LIMIT_PER_DAY", "TMKG_MODEL_CONCURRENCY",
                "TMKG_PHAROS_INDEX", "TMKG_GRAPH_BACKEND", "NEPTUNE_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(appmod, "_ask_log", {})
    monkeypatch.setattr(appmod, "_day_log", type(appmod._day_log)())
    ES._memory.clear()
    yield
    ES._memory.clear()
    appmod._pharos.clear()
    appmod._reports.clear()
    appmod._state.update(nim=NIM(backend="offline"))


# ------------------------------------------------------------------------------------------------ the three spec tests
def test_success_with_a_fake_transport(tmp_path):
    m = Model(writer=GOOD)
    out = ES.summarise("Which diseases?", "PTGS2 is associated with ...", lines_for(2),
                       client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m))
    assert out is not None and set(out) >= {"text", "model", "created"}
    assert out["model"] == ES.writer_model() and out["checked_by"] == hosted(tmp_path, m).model("verifier")
    assert out["text"] == GOOD and out["sentences"] == 4 and out["dropped"] == []
    assert m.n("write") == 1 and m.n("check") == 4                       # one writer call, one check per sentence
    assert out["created"].endswith("Z") and len(out["created"]) == 20


def test_timeout_gives_none(tmp_path):
    m = Model(writer=requests.exceptions.ReadTimeout("slow"))
    assert ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m)) is None
    assert m.n("check") == 0


def test_reference_outside_the_lines_gives_none(tmp_path):
    m = Model(writer=GOOD.replace("[2]", "[3]"))
    assert ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m)) is None
    m0 = Model(writer=GOOD.replace("[1]", "[0]", 1))
    assert ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m0), check_client=client_of(tmp_path / "d", m0)) is None


# ------------------------------------------------------------------------------------------------ the other refusals
@pytest.mark.parametrize("reply", ["", "   ", " ".join(["word"] * 181) + " [1]."])
def test_empty_or_too_long_reply_gives_none(tmp_path, reply):
    m = Model(writer=reply)
    assert ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m)) is None


def test_offline_backend_writes_nothing(tmp_path):
    m = Model(writer=GOOD)
    off = NIM(backend="offline")
    assert ES.summarise("Q?", "", lines_for(2), client=off) is None
    assert ES.summarise("Q?", "", [], client=client_of(tmp_path, m)) is None    # no lines, no call
    assert m.calls == []


def test_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("TMKG_SUMMARY", "off")
    m = Model(writer=GOOD)
    assert ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m)) is None and m.calls == []


# ------------------------------------------------------------------------------------------------ (a) the sentence check
def test_an_unsupported_sentence_is_dropped(tmp_path):
    m = Model(writer=GOOD, verdicts={"Type 2 diabetes": False})
    out = ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m))
    assert out is not None and "Type 2 diabetes" not in out["text"] and out["sentences"] == 3
    assert [d["sentence"][:15] for d in out["dropped"]] == ["Type 2 diabetes"] and out["raw"] == GOOD
    keys = ES.response_keys(dict(out, key="0" * 16), lines_for(2))
    assert "1 sentence it did not find supported was removed" in keys["summary_meta"]["label"]
    assert keys["summary_meta"]["removed"] == 1


def test_fewer_than_two_sentences_left_gives_none(tmp_path):
    m = Model(writer=GOOD, verdicts={"PTGS2 links": False, "Type 2": False, "text-mined": False})
    assert ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m)) is None


@pytest.mark.parametrize("kw", [dict(check_error=requests.exceptions.ReadTimeout("slow")), dict(check_reply="yes, supported")])
def test_a_check_that_cannot_run_gives_none(tmp_path, kw):
    """No verdict (timeout; a reply that is not the JSON asked for) is not a pass: the summary is not shown."""
    m = Model(writer=GOOD, **kw)
    assert ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m)) is None


def test_each_sentence_is_checked_with_the_lines_it_cites(tmp_path):
    lines = lines_for(3)
    m = Model(writer="Alpha rests on the first record [1]. Beta rests on the third [3]. Nothing shows gamma.")
    out = ES.summarise("Q?", "", lines, client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m))
    assert out is not None
    checks = [b["messages"][0]["content"] for k, b in m.calls if k == "check"]
    assert "[1] line 1" in checks[0] and "[3] line 3" not in checks[0]
    assert "[3] line 3" in checks[1] and "[1] line 1" not in checks[1]
    assert all(f"[{i}] line {i}" in checks[2] for i in (1, 2, 3))           # cites nothing: checked against every line


def test_the_checker_is_the_verifier_model_and_the_writer_the_querier_model(tmp_path):
    m = Model(writer=GOOD)
    base = hosted(tmp_path, m)
    ES.summarise("Q?", "", lines_for(2), client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m))
    models = {(k, b["model"]) for k, b in m.calls}
    assert models == {("write", base.model("querier")), ("check", base.model("verifier"))}


# ------------------------------------------------------------------------------------------------ text helpers
def test_references_and_sentences():
    assert ES.references("a [1] b [2-4] c [1, 6] d [Ca2+] e [lines 7 and 8]") == [1, 2, 3, 4, 1, 6, 7, 8]
    assert ES.split_sentences("One rests on [1]. Two, e.g. TP53, on [2]. [3]. Three? Yes.") == [
        "One rests on [1].", "Two, e.g. TP53, on [2]. [3].", "Three?", "Yes."]
    assert ES.clean_reply("<think>x</think> **Summary:** A [1].\n\nB [2].") == "A [1]. B [2]."


def test_citations_stay_as_written_the_page_maps_n_to_row_n_or_claim_n():
    lines = [dict(n=1, id="a", kind="claim", ref="1.1", text="x"), dict(n=2, id="b", kind="claim", ref="2.3", text="y")]
    keys = ES.response_keys(dict(text="A [1]. B [2].", key="0" * 16), lines)
    assert keys["summary_text"] == "A [1]. B [2]."
    assert keys["summary_meta"]["lines"][1] == dict(n=2, kind="claim", ref="2.3")


def test_label_is_plain_words_computed_from_the_record():
    rec = dict(text="A [1]. B [2].", model="m", checked_by="v", dropped=[], key="0" * 16)
    meta = ES.response_keys(rec, lines_for(2))["summary_meta"]
    assert meta["label"] == ("Summary written by a language model from the rows below (2 rows read); a second model checked "
                             "each sentence against the rows it cites.")
    assert not any(w in meta["label"] for w in INTERNAL)
    assert meta["lines"] == [dict(n=1, kind="row", ref="1"), dict(n=2, kind="row", ref="2")]
    assert ES.response_keys(None, lines_for(2)) == {} and ES.attach({"a": 1, "summary_text": "old"}, None, []) == {"a": 1}


# ------------------------------------------------------------------------------------------------ the cache
def test_cache_key_reads_question_line_ids_and_model():
    a = lines_for(2)
    k = ES.cache_key("Which diseases?", a, "m1")
    assert k == ES.cache_key("  which   DISEASES ", a, "m1")                          # the typed form of a pill
    assert k != ES.cache_key("Which diseases?", a, "m2")
    assert k != ES.cache_key("Which diseases?", a[::-1], "m1")
    assert k == ES.cache_key("Which diseases?", [dict(x, text="other words") for x in a], "m1")   # ids, not wording


def test_a_cached_summary_is_served_without_a_model_call(tmp_path):
    m = Model(writer=GOOD)
    folders = ES.folders_for(None)
    first = ES.summary_for("Q?", "bl", lines_for(2), folders, client=client_of(tmp_path, m), check_client=client_of(tmp_path / "c", m))
    assert first is not None and first["from_cache"] is False
    path = Path(os.environ["TMKG_SUMMARY_DIR"]) / f"{first['key']}.json"
    rec = json.loads(path.read_text())
    assert rec["lines"] == ["[1] line 1 text.", "[2] line 2 text."] and rec["question"] == "Q?" and rec["bottom_line"] == "bl"
    broken = Model(writer=requests.exceptions.ReadTimeout("down"))
    again = ES.summary_for("Q?", "bl", lines_for(2), folders, client=client_of(tmp_path / "b", broken))
    assert again["text"] == first["text"] and again["from_cache"] is True and broken.calls == []
    assert ES.summary_for("Other?", "bl", lines_for(2), folders, call=False) is None


def test_a_read_only_folder_keeps_the_summary_in_memory(tmp_path, monkeypatch):
    ro = tmp_path / "ro"
    ro.mkdir()
    monkeypatch.setattr(ES.tempfile, "mkstemp", lambda **k: (_ for _ in ()).throw(PermissionError("read-only")))
    rec = dict(key="a" * 16, text="A [1]. B [2].")
    assert ES.store(rec, ro) is None and ES.cached("a" * 16, [ro])["text"] == rec["text"]
    assert ES.cached("../../etc/passwd", [ro]) is None


def test_the_committed_folder_is_read_after_the_data_root(tmp_path):
    f = ES.folders_for(tmp_path / "root")
    assert f == [Path(os.environ["TMKG_SUMMARY_DIR"]), tmp_path / "root" / "outputs" / "summaries", ES.FOLDER]


# ------------------------------------------------------------------------------------------------ the lines
@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return build(tmp_path_factory.mktemp("repo"))


@pytest.fixture(scope="module")
def index_file(repo):
    return build_index(repo, chunk=3, log=lambda *_: None)["path"]


@pytest.fixture
def pharos_client(repo, index_file, monkeypatch):
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    monkeypatch.setenv("TMKG_PHAROS_INDEX", str(index_file))      # the fixture's index, whatever the shell or a .env says
    appmod._pharos.clear()
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    return TestClient(appmod.app)


def test_graph_lines_number_the_rows_as_the_page_does(pharos_client):
    one = pharos_client.get("/api/pharos/demo/pharos_demo_1").json()
    lines = RS.graph_answer_lines(one)
    assert [ln["n"] for ln in lines] == list(range(1, len(one["rows"]) + 1)) and [ln["ref"] for ln in lines] == ["1", "2"]
    assert lines[0]["id"] == "row:" + ",".join(one["rows"][0]["evidence"])
    t = lines[0]["text"]
    assert "Alzheimer disease" in t and "DisGeNET (curated, kept by the pruning check, confidence 0.90)" in t
    assert "JensenLab Text Mining (text-mined, pruned by the pruning check, confidence 0.10)" in t
    assert "Row support: curated or measured support" in t and "e0000" not in t and "knowledge_assertion" not in t
    two = pharos_client.get("/api/pharos/demo/pharos_demo_2").json()
    assert "IC50 6.2 (pChEMBL scale; 1 uM or stronger)" in RS.graph_answer_lines(two)[0]["text"]


def test_graph_lines_keep_one_line_per_row_and_read_at_most_thirty():
    rows = [dict(name=f"n{i}", evidence=f"e{i:016x}") for i in range(40)] + [dict(evidence="e" + "f" * 16)]
    lines = ES.graph_lines(dict(columns=["name", "evidence"], rows=rows))
    assert len(lines) == 30 and lines[-1]["n"] == 30 and lines[0]["text"] == "name: n0."
    empty = ES.graph_lines(dict(columns=["evidence"], rows=[dict(evidence="e" + "f" * 16)]))
    assert empty[0]["n"] == 1 and empty[0]["text"]                       # a row is never skipped (line n = row n)


def test_claim_lines_of_a_stored_trial_report():
    d = stored_answers.load(stored_answers.FOLDER, "x01")
    lines = RS.stored_lines(d)
    claims = [c for s in d["answer"]["sections"] for c in s["claims"]]
    assert len(lines) == min(30, len(claims)) and lines[0]["ref"] == "1.1" and lines[0]["kind"] == "claim"
    page_refs = [f"{si + 1}.{i + 1}" for si, s in enumerate(d["answer"]["sections"]) for i, _ in enumerate(s["claims"])]
    assert [ln["ref"] for ln in lines] == page_refs[:30]                      # [n] = the page's n-th claim
    assert "sources: ClinicalTrials.gov" in lines[0]["text"] and "evidence status: registry record" in lines[0]["text"]
    assert not any(w in ln["text"] for ln in lines for w in ("data/snapshots", ".json", "raw_response"))
    assert ES.bottom_line(d["answer"]).startswith("Mepolizumab")


def test_card_lines_read_every_kind_and_label_predictions(pharos_client):
    card = pharos_client.get(f"/api/pharos/card/{GENE}").json()
    lines = ES.card_lines(card)
    kinds = [s["kind"] for s in card["known"] if s["rows"]]
    assert [ln["text"].split("evidence kind: ")[1].split(";")[0] for ln in lines[:len(kinds)]] == kinds   # round robin
    assert len(lines) == sum(len(s["rows"]) for s in card["known"]) and all(ln["kind"] == "link" for ln in lines)
    fake = dict(card, predicted=[dict(subject=GENE, object="MONDO:1", other_label="X disease", score=0.91, rank=1)])
    pl = ES.card_lines(fake)[-1]
    assert pl["kind"] == "prediction" and "not a source statement" in pl["text"] and "score 0.910" in pl["text"]
    assert ES.card_question(card) == "What does the graph hold about PTGS2 (gene)?"
    assert ES.card_bottom_line(card) == "PTGS2 is a gene. The graph holds 6 links of 4 kinds; pruning kept 4 and pruned 1."


def test_edge_lines_of_the_drawer(pharos_client):
    x = pharos_client.get("/api/pharos/explain/e0000000000000001").json()
    lines = ES.edge_lines(x)
    assert lines[0]["text"].startswith("PTGS2 is associated with Alzheimer disease. (source: DisGeNET; curated; kept")
    assert lines[1]["text"] == "Values the source gives for this link: disgenet score 0.4."
    assert any("JensenLab Text Mining; text-mined; pruned" in ln["text"] for ln in lines)
    assert lines[-1]["text"].startswith("Timeline of the records: first seen 2024-02; last confirmed 2026-09")
    assert ES.edge_bottom_line(x) == ("PTGS2 is associated with Alzheimer disease: DisGeNET, curated, kept by the pruning check, "
                                      "confidence 0.90.")
    no_dates = dict(x, timeline=dict(summary=dict(first_seen=None, trend="unknown")))
    assert not any(ln["kind"] == "timeline" for ln in ES.edge_lines(no_dates))     # no empty field is written


# ------------------------------------------------------------------------------------------------ the endpoints
def seed(question, lines, text="Alpha rests on [1]. Beta rests on [2]."):
    """Writes a summary record for (question, lines) into the test's summary folder, as the precompute script would."""
    key = ES.cache_key(question, lines, ES.writer_model())
    ES.store(dict(key=key, text=text, model=ES.writer_model(), created="2026-11-12T00:00:00Z", checked_by="v", dropped=[],
                  sentences=2), Path(os.environ["TMKG_SUMMARY_DIR"]))
    return key


def test_tested_question_carries_the_stored_summary_only(pharos_client):
    one = pharos_client.get("/api/pharos/demo/pharos_demo_1").json()
    assert "summary_text" not in one and "summary_meta" not in one                     # nothing stored: keys absent
    seed(one["question"], RS.graph_answer_lines(one))
    two = pharos_client.get("/api/pharos/demo/pharos_demo_1").json()
    assert two["summary_text"] == "Alpha rests on [1]. Beta rests on [2]." and two["summary_meta"]["stored"] is True
    assert two["rows"] == one["rows"] and two["summary"] == one["summary"]             # the answer itself is unchanged


def test_typed_question_writes_one_summary_inside_the_same_request(pharos_client, tmp_path):
    m = Model(writer=GOOD)
    appmod._state.update(nim=hosted(tmp_path, m))
    r = pharos_client.post("/api/pharos/ask", json={"question": TESTED})
    out = r.json()
    assert r.status_code == 200 and out["summary_text"] == GOOD and out["summary_meta"]["stored"] is False
    assert m.n("write") == 1 and out["summary_meta"]["model"] == ES.writer_model()
    again = pharos_client.post("/api/pharos/ask", json={"question": TESTED.lower()}).json()
    assert again["summary_text"] == GOOD and again["summary_meta"]["stored"] is True and m.n("write") == 1
    pill = pharos_client.get("/api/pharos/demo/pharos_demo_1").json()                  # the pill shares the summary
    assert pill["summary_text"] == GOOD


def test_typed_question_without_a_hosted_model_has_no_summary_keys(pharos_client):
    out = pharos_client.post("/api/pharos/ask", json={"question": TESTED}).json()
    assert out["rows"] and "summary_text" not in out and "summary_meta" not in out


def test_a_busy_demo_answers_without_a_summary(pharos_client, tmp_path, monkeypatch):
    m = Model(writer=GOOD)
    appmod._state.update(nim=hosted(tmp_path, m))
    monkeypatch.setattr(RS, "SLOT_WAIT", 0.0)
    monkeypatch.setenv("TMKG_MODEL_CONCURRENCY", "1")
    with appmod.model_slot():                                                            # someone else holds the only slot
        out = RS.pharos_answer(dict(question=TESTED, rows=[dict(name="x")], columns=["name"]), appmod)
    assert "summary_text" not in out and m.calls == []


def test_stored_answer_carries_its_summary_on_the_answer(pharos_client):
    d = pharos_client.get("/api/stored_answer/x01").json()
    assert "summary_text" not in d and "summary_text" not in d["answer"]
    raw = stored_answers.load(stored_answers.FOLDER, "x01")
    seed(raw["question"], RS.stored_lines(raw), text="Claim one [1]. Claim two [2].")
    d2 = pharos_client.get("/api/stored_answer/x01").json()
    assert d2["answer"]["summary_text"] == "Claim one [1]. Claim two [2]." == d2["summary_text"]
    assert d2["answer"]["summary_meta"]["lines"][0] == dict(n=1, kind="claim", ref="1.1")


def test_trial_report_writes_a_summary(trial_client_with_model):
    client, m = trial_client_with_model
    out = client.post("/api/ask", json={"question": "Why did mepolizumab fail in METREO for COPD?"}).json()
    assert out["summary_text"].startswith("PTGS2 links") and m.n("write") == 1
    assert out["summary_meta"]["label"].startswith("Summary written by a language model from the claims below")


@pytest.fixture
def trial_client_with_model(tmp_path, monkeypatch):
    m = Model(writer=GOOD)
    appmod._state.update(kg=None, nim=hosted(tmp_path, m))
    report = stored_answers.load(stored_answers.FOLDER, "x01")["answer"]
    monkeypatch.setattr(appmod, "run_report", lambda q: dict(report, question=q))
    return TestClient(appmod.app), m


def test_card_and_drawer_summaries_on_demand(pharos_client, tmp_path):
    m = Model(writer=GOOD.replace("[2]", "[5]"))
    appmod._state.update(nim=hosted(tmp_path, m))
    assert "summary_text" not in pharos_client.get(f"/api/pharos/card/{GENE}").json()
    r = pharos_client.post("/api/summary", json={"kind": "card", "id": GENE})
    assert r.status_code == 200 and r.json()["summary_text"].startswith("PTGS2 links") and m.n("write") == 1
    assert r.json()["summary_meta"]["lines"][0]["kind"] == "link"
    card = pharos_client.get(f"/api/pharos/card/{GENE}").json()                       # the same cache serves the card
    assert card["summary_text"] == r.json()["summary_text"] and card["summary_meta"]["stored"] is True
    again = pharos_client.post("/api/summary", json={"kind": "card", "id": GENE}).json()
    assert again["summary_text"] == card["summary_text"] and m.n("write") == 1
    e = pharos_client.post("/api/summary", json={"kind": "edge", "id": "e0000000000000001"}).json()
    assert e["summary_text"] and m.n("write") == 2
    assert pharos_client.get("/api/pharos/explain/e0000000000000001").json()["summary_text"] == e["summary_text"]


def test_on_demand_summary_refusals(pharos_client, tmp_path):
    assert pharos_client.post("/api/summary", json={"kind": "trial", "id": GENE}).status_code == 400
    assert pharos_client.post("/api/summary", json={"kind": "card", "id": "NCBIGene:0"}).status_code == 404
    assert pharos_client.post("/api/summary", json={"kind": "card", "id": GENE}).json() == dict(
        kind="card", id=GENE, available=False)                                         # offline: nothing written
    m = Model(writer=GOOD.replace("[2]", "[99]"))
    appmod._state.update(nim=hosted(tmp_path, m))
    assert pharos_client.post("/api/summary", json={"kind": "edge", "id": "e0000000000000001"}).json()["available"] is False


def test_on_demand_summary_is_counted_as_a_question(pharos_client, monkeypatch):
    assert RS.PATH in appmod.ASK_PATHS
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "1")
    assert pharos_client.post("/api/summary", json={"kind": "card", "id": GENE}).status_code == 200
    assert pharos_client.post("/api/summary", json={"kind": "card", "id": GENE}).status_code == 429
    assert pharos_client.get(f"/api/pharos/card/{GENE}").status_code == 200            # cards are never limited


def test_switched_off_everywhere(pharos_client, monkeypatch):
    one = pharos_client.get("/api/pharos/demo/pharos_demo_1").json()
    seed(one["question"], RS.graph_answer_lines(one))
    monkeypatch.setenv("TMKG_SUMMARY", "off")
    assert "summary_text" not in pharos_client.get("/api/pharos/demo/pharos_demo_1").json()


def test_a_failing_summary_step_never_breaks_the_answer(pharos_client, monkeypatch):
    monkeypatch.setattr(ES, "summary_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = pharos_client.get("/api/pharos/demo/pharos_demo_1")
    assert r.status_code == 200 and r.json()["rows"] and "summary_text" not in r.json()


# ------------------------------------------------------------------------------------------------ the precompute script
def test_precompute_summaries_targets_and_run(pharos_client, tmp_path, monkeypatch):
    import precompute_examples as P
    targets = P.summary_targets(appmod, stored_folder=stored_answers.FOLDER)
    kinds = {t["kind"] for t in targets}
    assert {"stored", "tested"} <= kinds
    assert {t["id"] for t in targets if t["kind"] == "tested"} == {f"pharos_demo_{i}" for i in range(1, 6)}
    assert {t["id"] for t in targets if t["kind"] == "stored"} == {p.stem for p in stored_answers.FOLDER.glob("*.json")}
    m = Model(writer=GOOD)
    appmod._state.update(nim=hosted(tmp_path, m))
    out = tmp_path / "out"
    tested = [t for t in targets if t["kind"] == "tested"][:1]
    done, skipped = P.run_summaries(appmod, tested, out)
    assert done == ["pharos_demo_1"] and skipped == [] and len(list(out.glob("*.json"))) == 1
    proof = (out / "PROOFREAD.md").read_text()
    assert "pharos_demo_1" in proof and GOOD in proof and "[1] disease: Alzheimer disease" in proof
    monkeypatch.setenv("TMKG_SUMMARY_DIR", str(out))
    assert pharos_client.get("/api/pharos/demo/pharos_demo_1").json()["summary_text"] == GOOD   # the app reads what it wrote


# ------------------------------------------------------------------------------------------------ FIX1 F2: isolation
def test_a_dot_env_naming_another_card_index_does_not_redirect_the_fixture(tmp_path, monkeypatch, pharos_client):
    """Gate 1 G1-SEC-03: NIM() reads .env, which on the owner's Mac may name the real card index; inside these tests it reads
    nothing, and the client answers from the fixture's index even when the variable leaked in earlier (set before the client)."""
    nimmod.load_env()                                   # the default path is a missing file inside these tests
    assert os.environ.get("TMKG_PHAROS_INDEX", "").endswith(".sqlite")              # pinned by pharos_client
    NIM(backend="offline")                              # what every client fixture builds: it must not change the pin
    x = pharos_client.get("/api/pharos/explain/e0000000000000001")
    assert x.status_code == 200 and x.json()


# ------------------------------------------------------------------------------------------------ FIX3 (Gate 3): summary truth
# The three false sentences of outputs/review/S13_summary_repro_2026-11-14.json, each refused by a fixed rule (no model call).
def _egfr_answer():
    """A fake typed answer like the Gate 3 EGFR case: one row whose three links come from Open Targets 26.09 (statistical,
    confidence 0.65), DisGeNET (0.48) and TIN-X (text-mined, 0.49); the question named non-small cell lung carcinoma and the
    query read it as lung carcinoma (MONDO:0005138)."""
    def fact(i, source, level, conf, kind=None):
        return dict(id=i, subject="NCBIGene:1956", subject_label="EGFR", predicate="biolink:gene_associated_with_condition",
                    phrase="is associated with", object="MONDO:0005138", object_label="lung carcinoma", source=source,
                    knowledge_level=level, verdict="kept", confidence=conf, **({"kind": kind} if kind else {}))
    ids = ["e" + "a" * 16, "e" + "b" * 16, "e" + "c" * 16]
    facts = {ids[0]: fact(ids[0], "Open Targets 26.09 overall direct", "statistical_association", 0.65),
             ids[1]: fact(ids[1], "DisGeNET", "not_provided", 0.48),
             ids[2]: fact(ids[2], "TIN-X", "text_mining", 0.49, kind="text mining")}
    return dict(question="What links EGFR to non-small cell lung carcinoma?", columns=["gene", "disease", "evidence"],
                rows=[dict(gene="EGFR", disease="lung carcinoma", evidence=ids)],
                summary=dict(headline="Read as lung carcinoma: EGFR is associated with lung carcinoma (Open Targets 26.09 "
                                      "overall direct).", refs=dict(edges=facts, row_class={"1": "statistical"})),
                read_as=[dict(phrase="non-small cell lung carcinoma", id="MONDO:0005138", label="lung carcinoma")])


def test_fix3_f1_a_summary_never_calls_source_links_predictions():
    lines = [dict(n=i, id=f"row:e{i:016x}", kind="row", ref=str(i), sources=["CTD"],
                  text=f"disease: d{i}. Sources: CTD (curated, kept by the pruning check, confidence 0.74). Row support: "
                       "text-mined or predicted links only.") for i in range(1, 23)]
    seen = ("The model predicts new BACE1-associated diseases primarily through text-mined or statistical links rather "
            "than curated evidence [15].")
    third = ("The lines do not demonstrate causality, clinical validation, or experimental confirmation for any predicted "
             "disease.")
    kept, dropped = ES.rule_check([seen, third, "These diseases are predicted by the graph.",
                                   "The lines do not show causality for any predicted disease [2]."], lines)
    assert kept == [] and [d["rule"] for d in dropped] == ["prediction"] * 4             # cited and uncited: no prediction line
    said_of_nothing = ["The lines show no statistical or predicted links [1].", "None of the lines is a prediction.",
                       "No link here is predicted [2]."]
    assert ES.rule_check(said_of_nothing, lines) == (said_of_nothing, [])
    listed = ["The lines do not show statistical, text-mined, or predicted associations [1].",
              "Lines do not show whether the result is text-mined or predicted [2]."]
    assert ES.rule_check(listed, lines) == (listed, [])
    for bad in ("No curated links, but predicted ones [1].", "The lines do not show causality and the model predicts new "
                "diseases [1].", "No line is curated; all are predicted [1]."):
        assert ES.rule_check([bad], lines)[0] == [], bad
    quote = "Rows [3] and [4] have text-mined or predicted links only."                   # the lines' own words are not a claim
    assert ES.rule_check([quote], lines) == ([quote], [])
    card = lines[:2] + [dict(n=3, id="pred:a|b", kind="prediction", ref=None, sources=[],
                             text="Predicted link between BACE1 and X (a prediction of the link-prediction model, not a "
                                  "source statement; score 0.912).")]
    ok = "The model predicts a new link to X [3]."
    assert ES.rule_check([ok], card) == ([ok], [])                                       # a prediction line is cited: kept
    assert ES.rule_check(["The model predicts a new link to X [1]."], card)[0] == []     # cites a source line: dropped
    assert ES.rule_check(["One link is predicted."], card)[0] == ["One link is predicted."]   # cites none; a prediction exists


def test_fix3_f2_a_release_number_in_a_source_name_is_never_read_as_a_score():
    lines = ES.graph_lines(_egfr_answer())
    assert "Open Targets 26.09 overall direct (statistical, kept by the pruning check, confidence 0.65)" in lines[0]["text"]
    assert lines[0]["sources"] == ["Open Targets 26.09 overall direct", "DisGeNET", "TIN-X"]
    seen = ("EGFR is linked to lung carcinoma through statistical associations reported by Open Targets (overall direct "
            "score 26.09, confidence 0.65), DisGeNET (confidence 0.48), and TIN-X (text-mined, confidence 0.49) [1].")
    kept, dropped = ES.rule_check([seen], lines)
    assert kept == [] and dropped[0]["rule"] == "source_number" and "26.09" in dropped[0]["reason"]
    fine = ["Open Targets gives a statistical link to lung carcinoma that pruning kept with confidence 0.65 [1].",
            "Open Targets 26.09 overall direct reports a statistical link [1].",                # names the source: no score
            "Open Targets 26.09 calls it statistical [1]."]
    assert ES.rule_check(fine, lines) == (fine, [])
    assert ES.rule_check(["The score is 26.09."], lines)[0] == []                          # cites nothing: all lines are read


def test_fix3_f3_the_summary_names_the_term_the_answer_used(tmp_path):
    """A fake resolution (non-small cell lung carcinoma read as lung carcinoma) and a fake writer through the typed-answer
    path: the writer's prompt carries the resolved term, the sentence that names the user's phrase is dropped, the key is
    the one of the unchanged question."""
    out = _egfr_answer()
    wrong = "EGFR is linked to non-small cell lung carcinoma through statistical associations [1]."
    right = ["Open Targets gives a statistical link to lung carcinoma that pruning kept with confidence 0.65 [1].",
             "The question named non-small cell lung carcinoma; the graph read it as lung carcinoma [1].",
             "TIN-X adds a text-mined record for the same pair [1]."]
    m = Model(writer=" ".join([wrong] + right))

    class Host:
        Busy = appmod.Busy

        @staticmethod
        def _data_root():
            return tmp_path / "root"

        @staticmethod
        def model_client(seconds):
            return client_of(tmp_path, m)

        model_slot = staticmethod(appmod.model_slot)

    done = RS.pharos_answer(out, Host, call=True)
    body = next(b for k, b in m.calls if k == "write")["messages"][-1]["content"]
    assert "Read as lung carcinoma (the question named non-small cell lung carcinoma)." in body
    assert "Question: What links EGFR to non-small cell lung carcinoma?" in body                  # the question is unchanged
    assert done["summary_text"] == " ".join(right) and done["summary_meta"]["removed"] == 1
    assert m.n("check") == 3                                                                     # the rule needs no model call
    lines = RS.graph_answer_lines(out)
    stored = ES.cached(ES.cache_key(out["question"], lines, ES.writer_model()), ES.folders_for(tmp_path / "root"))
    assert stored["dropped"][0]["rule"] == "read_as" and stored["question"] == out["question"]
    assert RS.writer_bottom_line(dict(out, read_as=[])) == out["summary"]["headline"]          # nothing read as: unchanged
    assert ES.read_as_note([dict(phrase="EGFR", label="egfr")]) == ""
    links = dict(phrase="links EGFR", id="NCBIGene:1956", label="EGFR")                    # 'What links EGFR to ...'
    assert ES.read_as_note(out["read_as"] + [links]) == ES.read_as_note(out["read_as"])
    assert ES.rule_check(["Line [1] links EGFR to lung carcinoma."], lines, [links])[0] == ["Line [1] links EGFR to lung carcinoma."]
    named = dict(n=1, id="row:x", kind="row", ref="1", sources=[], text="disease: non-small cell lung carcinoma.")
    assert ES.rule_check([wrong], [named], out["read_as"])[0] == [wrong]                       # the lines name it: kept
    assert ES.rule_check(["EGFR and non small cell lung carcinoma [1]."], lines, out["read_as"])[0] == []   # hyphen or not
    both = "EGFR is linked to non-small cell lung carcinoma through its association with lung carcinoma [1]."
    kept, dropped = ES.rule_check([both], lines, out["read_as"])                  # names the phrase and the label: dropped
    assert kept == [] and dropped[0]["rule"] == "read_as"
    only_named = "The question named non-small cell lung carcinoma; the graph read it as lung carcinoma [1]."
    assert ES.rule_check([only_named], lines, out["read_as"])[0] == [only_named]  # phrase only as 'the question named': kept


def test_fix3_rules_also_run_when_the_model_check_is_off(tmp_path):
    lines = ES.graph_lines(_egfr_answer())
    reply = ("Open Targets reports a score of 26.09 [1]. Open Targets gives a statistical link kept at confidence 0.65 [1]. "
             "DisGeNET adds a record with confidence 0.48 [1].")
    out = ES.summarise("Q?", "", lines, client=client_of(tmp_path, Model(writer=reply)), check=False)
    assert out["sentences"] == 2 and "26.09" not in out["text"] and out["dropped"][0]["rule"] == "source_number"


def test_every_stored_summary_is_still_found_by_its_key(pharos_client, monkeypatch):
    """FIX3 keeps every stored summary valid. Every file under outputs/summaries is found by the key its record names, in
    the folder the serving path reads last (folders_for), written by the configured writer under the current prompt
    version. Where the answer it summarises is committed here (a stored answer), the endpoint itself serves the record:
    the key the serving path computes from the question and the lines today is the record's key."""
    committed = ROOT / "outputs" / "summaries"
    monkeypatch.setattr(ES, "FOLDER", committed)                     # the committed folder, as the app reads it
    files = sorted(committed.glob("*.json"))
    assert len(files) >= 25
    records = {}
    for f in files:
        rec = json.loads(f.read_text(encoding="utf-8"))
        assert f.name == f"{rec['key']}.json" and rec["prompt_version"] == ES.PROMPT_VERSION
        assert rec["model"] == ES.writer_model() and rec.get("text")
        assert ES.cached(rec["key"], ES.folders_for(None))["text"] == rec["text"]
        records[rec["key"]] = rec
    served = set()
    for f in sorted(stored_answers.FOLDER.glob("*.json")):
        d = stored_answers.load(stored_answers.FOLDER, f.stem)
        if not isinstance(d, dict) or not isinstance(d.get("answer"), dict):
            continue
        key = ES.cache_key(d.get("question") or d["answer"].get("question") or "", RS.stored_lines(d), ES.writer_model())
        if key in records:
            got = pharos_client.get(f"/api/stored_answer/{f.stem}").json()
            assert got["summary_text"] == records[key]["text"] and got["summary_meta"]["stored"] is True, f.stem
            served.add(key)
    assert len(served) >= 13                                           # the 13 stored answers with a summary on 14 Nov
