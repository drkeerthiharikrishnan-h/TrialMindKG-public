"""Card drafter (pipelines/card_drafter.py): the model drafts a source card for a pasted table, the validator decides, a
scientist approves. Every test here is model-free: a function stands in for the model, or the fixed rules draft."""
import gzip, json, re, sys, tempfile, time
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))
import card_drafter as cd  # noqa: E402
import kg_schema  # noqa: E402
from kgbuilder import engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCREEN_HEADER, SCREEN_ROWS = cd.read_example_table("screen")

GOOD = """
edges:
  - name: screen_hit
    filter: {hit: "yes"}
    subject: {column: compound_chembl, prefix: CHEMBL.COMPOUND, category: biolink:SmallMolecule}
    predicate: biolink:directly_physically_interacts_with
    object: {column: target_uniprot, prefix: UniProtKB, category: biolink:Protein}
    record_columns: [compound_chembl, target_uniprot, assay]
    knowledge_level: observation
    agent_type: manual_agent
    properties: {signal_pct: signal_pct, assay: assay}
prune:
  score_columns: {signal_pct: higher}
"""
BAD_PREFIX = GOOD.replace("prefix: UniProtKB", "prefix: UNIPROT")
NOTES = [{"column": "compound_chembl", "note": "ChEMBL compound ids"}, {"column": "assay", "note": "assay label"}]


def scripted(*cards):
    """A model function that answers with the given cards in turn and records the messages of every call."""
    calls = []
    def model(messages, schema):
        calls.append([dict(m) for m in messages])
        return {"card_yaml": cards[min(len(calls), len(cards)) - 1], "decisions": NOTES}
    model.calls = calls
    return model


def tree_state(*dirs):
    """{path: (size, mtime)} of every file under the given folders of the repository."""
    out = {}
    for d in dirs:
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                st = p.stat()
                out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


# ---------------------------------------------------------------- the drafter loop
def test_loop_accepts_a_valid_first_draft():
    model = scripted(GOOD)
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, "compound screen", model=model)
    assert d["ok"] and len(model.calls) == 1
    assert d["report"] == dict(ok=True, rounds=[dict(round=1, drafted_by="model", problems=[])], problems=[],
                               sample=dict(rows=8, nodes=9, edges=6, not_mapped=[], value_notes=[]))
    assert d["drafted_by"]["kind"] == "model" and "approve" in d["message"]
    assert yaml.safe_load(d["card_yaml"]) == d["card"]
    by_col = {x["column"]: x for x in d["decisions"]}
    assert set(SCREEN_HEADER) <= set(by_col)                           # one decision per column of the table
    assert "-> protein (biolink:Protein), identifier written as UniProtKB:<value>" in by_col["target_uniprot"]["text"]
    assert "only rows with 'yes'" in by_col["hit"]["text"] and "higher = more confidence" in by_col["signal_pct"]["text"]
    assert by_col["compound_chembl"]["note"] == "ChEMBL compound ids"   # the model's own note rides along, never replaces
    assert any(x["column"] is None and "directly physically interacts with" in x["text"] for x in d["decisions"])


def test_loop_sends_the_validator_error_back_and_accepts_the_corrected_card():
    model = scripted(BAD_PREFIX, GOOD)
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=model)
    assert d["ok"] and len(model.calls) == 2
    first, second = d["report"]["rounds"]
    assert first["problems"] and "UNIPROT" in first["problems"][0] and second["problems"] == []
    feedback = model.calls[1][-1]["content"]                            # what the model was told before its second try
    assert model.calls[1][-1]["role"] == "user" and "validator refused" in feedback and first["problems"][0] in feedback
    assert model.calls[1][-2]["role"] == "assistant" and "UNIPROT" in model.calls[1][-2]["content"]


def test_three_failures_give_an_honest_message_and_no_accepted_card():
    model = scripted(BAD_PREFIX)
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=model)
    assert not d["ok"] and len(model.calls) == cd.MAX_ROUNDS == 3
    assert [r["round"] for r in d["report"]["rounds"]] == [1, 2, 3] and all(r["problems"] for r in d["report"]["rounds"])
    assert d["report"]["problems"] and not d["report"]["ok"]
    assert "After 3 attempts" in d["message"] and "not accepted" in d["message"] and "nothing can be built" in d["message"]
    assert all(not x["mapped"] for x in d["decisions"])                 # no decision is presented as accepted
    assert not cd.build_small_graph(d["card"], SCREEN_HEADER, SCREEN_ROWS)["ok"]   # and the refused card does not build


def test_a_reply_that_is_not_a_card_counts_as_a_failed_round():
    replies = iter(["just text", {"card_yaml": "- a\n- b", "decisions": []}, {"card_yaml": "edges: [", "decisions": []}])
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=lambda m, s: next(replies))
    assert not d["ok"] and len(d["report"]["rounds"]) == 3
    assert "requested form" in d["report"]["rounds"][0]["problems"][0]
    assert "mapping" in d["report"]["rounds"][1]["problems"][0] and "not valid YAML" in d["report"]["rounds"][2]["problems"][0]


def test_a_card_that_maps_no_sample_row_is_refused_with_the_reasons():
    wrong_filter = GOOD.replace('{hit: "yes"}', '{hit: "maybe"}')
    swapped = GOOD.replace("column: target_uniprot, prefix: UniProtKB", "column: assay, prefix: UniProtKB")
    for card, expect in ((wrong_filter, "the filter keeps no row"), (swapped, "column assay does not look like UniProtKB identifiers: 0 of 8 values fit")):
        d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=scripted(card))
        assert not d["ok"] and expect in " ".join(d["report"]["problems"])


def test_without_a_model_the_fixed_rules_draft_once_and_the_result_says_so():
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, "compound screen", model=cd.rules_only("no language model is configured here"))
    assert d["ok"] and d["drafted_by"]["kind"] == "rules" and d["drafted_by"]["model"] is None
    assert "fixed rules" in d["drafted_by"]["text"] and "no language model is configured here" in d["drafted_by"]["text"]
    assert [r["drafted_by"] for r in d["report"]["rounds"]] == ["rules"]

    def crashes(messages, schema):
        raise RuntimeError("/secret/path/boom")
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=crashes)
    assert d["ok"] and d["drafted_by"]["kind"] == "rules" and "secret" not in json.dumps(d)


def test_model_failing_in_round_two_hands_over_to_the_rules():
    n = []
    def model(messages, schema):
        n.append(1)
        if len(n) == 1:
            return {"card_yaml": BAD_PREFIX, "decisions": []}
        raise cd.ModelUnavailable("the language model did not answer within 30 seconds")
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=model)
    assert d["ok"] and [r["drafted_by"] for r in d["report"]["rounds"]] == ["model", "rules"]
    assert d["drafted_by"]["kind"] == "rules" and "30 seconds" in d["drafted_by"]["reason"]


def test_fixed_rules_stop_after_one_round_when_they_find_nothing():
    d = cd.draft_card(["sample", "comment"], [["s1", "looks fine"], ["s2", "repeat this one"]], model=cd.rules_only("offline"))
    assert not d["ok"] and d["card"] is None and d["card_yaml"] == "" and len(d["report"]["rounds"]) == 1
    assert "no card was drafted" in d["report"]["problems"][0] and "nothing was accepted" in d["message"]


# ---------------------------------------------------------------- the model wrapper (deadline, no stored answers)
class FakeNIM:
    backend, fallback = "nim", "none"
    def __init__(self, seconds=0.0, reply=None, error=None):
        self.seconds, self.reply, self.error, self.snaps, self.roles, self.budgets = seconds, reply, error, [], [], []
    def chat(self, role, messages, schema=None):
        self.roles.append(role)
        self.budgets.append(self.budget)
        self.snaps.append(self.snap.dir)
        time.sleep(self.seconds)
        if self.error:
            raise self.error
        return type("R", (), dict(data=self.reply, model="test-model"))()


def test_call_model_gives_up_at_the_deadline():
    t0 = time.monotonic()
    with pytest.raises(cd.ModelUnavailable, match="did not answer within 0.2 seconds"):
        cd.call_model([], cd.REPLY_SCHEMA, nim=FakeNIM(seconds=1.5), deadline=0.2)
    assert time.monotonic() - t0 < 1.0
    time.sleep(1.6)                                                       # the abandoned call returns its slot
    assert cd._slots.acquire(blocking=False) and cd._slots.acquire(blocking=False)
    cd._slots.release(); cd._slots.release()


def test_call_model_uses_a_throw_away_snapshot_folder_and_plain_reasons():
    nim = FakeNIM(reply={"card_yaml": GOOD, "decisions": []})
    data, model_id = cd.call_model([], cd.REPLY_SCHEMA, nim=nim)
    assert data["card_yaml"] == GOOD and model_id == "test-model"
    assert not hasattr(nim, "snap") and not hasattr(nim, "budget")        # the caller's client is left as it was
    assert nim.roles == ["querier"]                                       # the role the drafter asks for
    assert nim.budgets[0].timeout == 30 and nim.budgets[0].rate_limit_waits == 0   # the web waits, not the batch waits
    folder = Path(nim.snaps[0])
    assert ROOT not in folder.parents and not folder.exists()             # outside the repository and gone
    with pytest.raises(cd.ModelUnavailable, match="could not be reached"):
        cd.call_model([], cd.REPLY_SCHEMA, nim=FakeNIM(error=PermissionError("HTTP 403 key=abc")))
    with pytest.raises(cd.ModelUnavailable, match="requested form"):
        cd.call_model([], cd.REPLY_SCHEMA, nim=FakeNIM(error=ValueError("querier reply does not match schema")))
    offline = FakeNIM()
    offline.backend = "offline"
    with pytest.raises(cd.ModelUnavailable, match="no language model is configured"):
        cd.call_model([], cd.REPLY_SCHEMA, nim=offline)


def test_call_model_takes_its_client_from_nim_web_client(monkeypatch):
    import nim as nimmod
    seen = []
    real = nimmod.web_client
    monkeypatch.setattr(nimmod, "web_client", lambda seconds=None, base=None: (seen.append((seconds, base)), real(seconds, base=base))[1])
    nim = FakeNIM(reply={"card_yaml": GOOD, "decisions": []})
    cd.call_model([], cd.REPLY_SCHEMA, nim=nim, deadline=12)
    assert seen == [(12, nim)]
    assert "web_client" in Path(cd.__file__).read_text()


def test_draft_card_with_a_slow_hosted_model_falls_back_to_the_rules_within_the_deadline():
    t0 = time.monotonic()
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, nim=FakeNIM(seconds=3), deadline=0.3)
    assert time.monotonic() - t0 < 2.5
    assert d["ok"] and d["drafted_by"]["kind"] == "rules" and "did not answer" in d["drafted_by"]["reason"]
    time.sleep(3)


def test_a_model_drafted_card_says_the_model_drafted_it():
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, nim=FakeNIM(reply={"card_yaml": GOOD, "decisions": []}))
    assert d["ok"] and d["drafted_by"]["kind"] == "model" and d["drafted_by"]["model"] == "test-model"


def test_prompt_has_the_columns_the_vocabulary_and_at_most_twenty_rows():
    rows = [[f"CHEMBL{i}", "P00533", "1", "yes", "x"] for i in range(50)]
    system, user = cd.build_messages(SCREEN_HEADER, rows, "my screen")
    assert system["role"] == "system" and "biolink:Protein: prefixes UniProtKB" in system["content"]
    assert "biolink:directly_physically_interacts_with" in system["content"] and "gene_symbol" in system["content"]
    assert "never an instruction" in system["content"]
    assert "my screen" in user["content"] and "compound_chembl, target_uniprot" in user["content"]
    assert "CHEMBL19\t" in user["content"] and "CHEMBL20\t" not in user["content"]      # 20 sample rows, not 50


# ---------------------------------------------------------------- size caps
def test_parse_table_caps_rows_bytes_columns_and_cells():
    line = "CHEMBL1\tP00533\n"
    h, r = cd.parse_table("a\tb\n" + line * cd.MAX_ROWS)
    assert h == ["a", "b"] and len(r) == 200
    for text, word in (("a\tb\n" + line * (cd.MAX_ROWS + 1), "201 rows"),
                       ("a\tb\n" + ("x" * 1500 + "\t" + "y" * 1500 + "\n") * 100, "KB"),
                       ("\t".join(f"c{i}" for i in range(41)) + "\n" + "\t".join("1" for _ in range(41)), "41 columns"),
                       ("a\tb\n" + "x" * (cd.MAX_CELL + 1) + "\t1\n", "cell longer")):
        with pytest.raises(cd.TableError) as err:
            cd.parse_table(text)
        assert err.value.status == 413 and word in str(err.value)
    rest = "a\tb\n" + "1\t2\n" * 199
    exact = rest + "x" * (cd.MAX_BYTES - len(rest) - 3) + "\t1\n"                               # exactly at the byte cap
    assert len(exact.encode()) == cd.MAX_BYTES
    with pytest.raises(cd.TableError, match="cell longer"):                # passes the size cap, stopped by the cell cap
        cd.parse_table(exact)
    with pytest.raises(cd.TableError, match="KB") as err:
        cd.parse_table(exact + "1\t2\n")
    assert err.value.status == 413


def test_parse_table_refuses_what_is_not_a_table():
    for text, word in (("", "column names"), ("a\tb\n", "at least one row"), ("a,b\n1,2\n", "separated by tabs"),
                       ("a\ta\n1\t2\n", "repeated: a"), ("a\tb\n1\t2\t3\n", "Line 2 has 3 cells"), ("a\t\n1\t2\n", "needs a name"),
                       (None, "must be text"), ("a\tb\n1\t\x002\n", "not text")):
        with pytest.raises(cd.TableError) as err:
            cd.parse_table(text)
        assert err.value.status == 400 and word in str(err.value)
    assert cd.parse_table("a\tb\r\n1\t2\r\n\r\n3\n") == (["a", "b"], [["1", "2"], ["3", ""]])   # CRLF, blank line, short row


# ---------------------------------------------------------------- what a card may contain
def finished(text, header=SCREEN_HEADER, rows=SCREEN_ROWS):
    card, problems = cd.load_card_text(text)
    assert not problems, problems
    return cd.finish_card(card, header, rows)


def test_check_draft_accepts_the_good_card_and_names_each_kind_of_problem():
    assert cd.check_draft(finished(GOOD), SCREEN_HEADER) == []
    cases = [
        (GOOD + "lookups: {x: {ref_file: /etc/passwd, key: a, value: b}}\n", "keys not allowed in a drafted card: lookups"),
        (GOOD + "nodes: []\n", "keys not allowed in a drafted card: nodes"),
        (GOOD + "extra_prefixes: {biolink:Protein: [XX]}\n", "extra_prefixes"),
        (GOOD.replace("column: target_uniprot", "column: target"), "column 'target' is not a column of the table"),
        (GOOD.replace("biolink:Protein", "biolink:Cell"), "class 'biolink:Cell' is not in the allowed vocabulary"),
        (GOOD.replace("biolink:Protein", "tmkg:Biomarker"), "is not in the allowed vocabulary"),
        (GOOD.replace("biolink:directly_physically_interacts_with", "biolink:causes"), "predicate 'biolink:causes' is not in the allowed vocabulary"),
        (GOOD.replace("biolink:directly_physically_interacts_with", "biolink:has_gene_product"), "is not a gene"),   # BioLink domain
        (GOOD.replace("prefix: UniProtKB", "prefix: CHEMBL.COMPOUND"), "not an identifier system BioLink lists for biolink:Protein"),
        (GOOD.replace("prefix: UniProtKB, ", ""), "`prefix` is missing"),
        (GOOD.replace("prefix: UniProtKB", "prefix: UniProtKB, replace: {'(a+)+$': ''}"), "keys not allowed here: replace"),
        (GOOD.replace("prefix: UniProtKB", "prefix: UniProtKB, lookup: gene_symbol"), "lookup gene_symbol gives biolink:Gene"),
        (GOOD.replace("prefix: UniProtKB", "lookup: my_table"), "lookup 'my_table' does not exist"),
        (GOOD.replace("properties: {signal_pct: signal_pct, assay: assay}", "properties: {subject: assay}"), "used by the graph itself"),
        (GOOD.replace("assay: assay}", "assay: nope}"), "names 'nope', which is not a column"),
        (GOOD.replace("signal_pct: higher", "signal_pct: bigger"), "direction must be higher, lower or abs"),
        (GOOD.replace("{signal_pct: higher}", "{other: higher}"), "score column 'other' is not one of the card's properties"),
        (GOOD.replace("knowledge_level: observation", "knowledge_level: hearsay"), "knowledge_level 'hearsay' is not one of"),
        (GOOD.replace('filter: {hit: "yes"}', "filter: {flag: \"yes\"}"), "filter column 'flag' is not a column"),
        (GOOD.replace('filter: {hit: "yes"}', "min_value: {signal_pct: high}"), "must be a number"),
        (GOOD.replace('filter: {hit: "yes"}', "explode_json: [assay]"), "keys not allowed: explode_json"),
        (GOOD.replace("column: target_uniprot", "column: compound_chembl"), "same column"),
        ("edges: []\n", "no `edges` list"),
        ("prune: {score_columns: {}}\n", "no `edges` list"),
    ]
    for text, expect in cases:
        problems = cd.check_draft(finished(text), SCREEN_HEADER)
        assert any(expect in p for p in problems), (expect, problems)
    five = yaml.safe_load(GOOD)
    five["edges"] = [dict(five["edges"][0], name=f"r{i}") for i in range(5)]
    assert any("at most 4" in p for p in cd.check_draft(cd.finish_card(five, SCREEN_HEADER, SCREEN_ROWS), SCREEN_HEADER))


def test_check_draft_never_raises_whatever_the_shape():
    junk = [None, 3, "edges", [], {}, {"edges": "x"}, {"edges": [1, "a", None]}, {"edges": [{}]}, {"edges": [{"subject": 1, "object": []}]},
            {"edges": [{"name": "a", "subject": {"column": ["x"], "category": {}}, "object": None, "predicate": 5}], "prune": []},
            {"edges": [{"name": "a", "filter": [1], "properties": [2], "record_columns": {"a": 1}, "min_value": "x", "require": 3}]},
            {"edges": [{"name": "a", "properties": {1: 2, None: None}}], "prune": {"score_columns": [1]}}, {1: 2, None: 3}]
    for card in junk:
        for prepared in (card, cd.finish_card(card, SCREEN_HEADER, SCREEN_ROWS)):
            problems = cd.check_draft(prepared, SCREEN_HEADER)
            assert problems and all(isinstance(p, str) for p in problems), card
            assert not cd.build_small_graph(prepared, SCREEN_HEADER, SCREEN_ROWS)["ok"]


def test_cards_are_read_with_safe_load_only():
    card, problems = cd.load_card_text("edges: !!python/object/apply:os.system ['echo hacked']\n")
    assert card is None and "not valid YAML" in problems[0]
    card, problems = cd.load_card_text("```yaml\n" + GOOD.strip() + "\n```")
    assert not problems and card["edges"][0]["name"] == "screen_hit"         # a code fence around the YAML is tolerated
    assert cd.load_card_text("x" * (cd.MAX_CARD_CHARS + 1))[1] == ["the card is longer than 20,000 characters"]
    assert cd.load_card_text("- a\n- b\n")[0] is None and cd.load_card_text("")[0] is None and cd.load_card_text(None)[0] is None
    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(f"{c}: &{c} [*{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}]\n"
                                                           for p, c in zip("abcdefg", "bcdefgh")) + "edges: *h\n"
    t0 = time.monotonic()
    card, problems = cd.load_card_text(bomb)
    assert card is None and "too large" in problems[0] and time.monotonic() - t0 < 2


def test_server_sets_the_fields_a_draft_must_not_choose():
    text = GOOD.replace("- name: screen_hit", "- name: screen_hit\n    table: ../../data/internal/secret\n    source: trust me\n"
                        "    primary_knowledge_source: infores:chembl\n    layer: L0") + "name: pharos\nversion: '9'\naggregator: infores:pharos\ntables_dir: /etc\n"
    card = finished(text)
    e = card["edges"][0]
    assert (card["name"], card["aggregator"], card["layer"]) == ("pasted_table", "tmkg:pasted-table", "L1")
    assert "tables_dir" not in card and "layer" not in e and e["table"] == cd.TABLE_NAME
    assert e["primary_knowledge_source"] == "tmkg:pasted-table"
    assert e["source"] == "SYNTHETIC pasted table" and card["version"].startswith("SYNTHETIC ")   # the table says synthetic (rule 4)
    assert cd.check_draft(card, SCREEN_HEADER) == []
    plain = cd.finish_card(yaml.safe_load(GOOD), ["compound_chembl", "target_uniprot", "signal_pct", "hit", "assay"],
                           [["CHEMBL939", "P00533", "91", "yes", "kinase panel"]], dict(source="Lab book 12"))
    assert plain["edges"][0]["source"] == "Lab book 12" and "SYNTHETIC" not in plain["version"]


def test_unquoted_yes_in_a_filter_is_restored_to_the_tables_own_spelling():
    card = finished(GOOD.replace('{hit: "yes"}', "{hit: yes}"))             # YAML reads an unquoted yes as True
    assert card["edges"][0]["filter"] == {"hit": "yes"} and cd.check_draft(card, SCREEN_HEADER) == []
    rows = [["CHEMBL939", "P00533", "1", "TRUE", "a"], ["CHEMBL941", "P00519", "2", "true", "a"]]
    card = cd.finish_card(yaml.safe_load(GOOD.replace('{hit: "yes"}', "{hit: true}")), SCREEN_HEADER, rows)
    assert card["edges"][0]["filter"] == {"hit": True}                       # two spellings: not guessed
    assert any("in quotes" in p for p in cd.check_draft(card, SCREEN_HEADER))


# ---------------------------------------------------------------- building: temporary directory only
def test_build_writes_only_into_a_temporary_directory_that_is_gone_afterwards(monkeypatch):
    made = []
    real = tempfile.TemporaryDirectory
    def spy(*a, **k):
        t = real(*a, **k)
        made.append(Path(t.name))
        return t
    monkeypatch.setattr(cd.tempfile, "TemporaryDirectory", spy)
    before = tree_state("data", "outputs", "examples")
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=scripted(GOOD))
    b = cd.build_small_graph(d["card"], SCREEN_HEADER, SCREEN_ROWS)
    assert b["ok"] and len(made) == 2                                      # the trial build and the build
    assert all(ROOT not in p.parents and p != ROOT for p in made) and not any(p.exists() for p in made)
    assert tree_state("data", "outputs", "examples") == before             # nothing written, changed or removed
    assert "lookups" not in d["card"] and "ref_file" not in d["card_yaml"]


def test_build_returns_nodes_and_edges_with_provenance_and_verdicts():
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, SCREEN_ROWS)
    assert b["ok"] and b["problems"] == [] and b["not_mapped"] == []
    assert b["counts"] == dict(rows=8, nodes=9, edges=6, scored=6, keep=b["counts"]["keep"], prune=6 - b["counts"]["keep"], rows_not_mapped=0)
    reference = engine.build(engine.load_card(ROOT / "examples/byo_source/lab_screen.yaml"), tables_dir=ROOT / "examples/byo_source")
    triple = lambda e: (e["subject"], e["predicate"], e["object"])
    assert sorted(map(triple, b["edges"])) == sorted(map(triple, reference.edges.to_dict("records")))   # the documented example
    assert {n["category"] for n in b["nodes"]} == {"biolink:SmallMolecule", "biolink:Protein"}
    for e in b["edges"]:
        assert set(e["provenance"]) == set(cd.PROVENANCE_FIELDS) and all(v not in (None, "") for v in e["provenance"].values())
        assert e["provenance"]["source"].startswith("SYNTHETIC") and e["provenance"]["source_record"].startswith("table|CHEMBL")
        assert e["verdict"]["verdict"] in ("keep", "prune") and 0 <= e["verdict"]["confidence"] <= 1 and "sources=1" in e["verdict"]["reasons"]
        assert e["properties"]["assay"] == "SYNTHETIC-example-screen" and isinstance(e["properties"]["signal_pct"], float)
    assert "All 6 edges come from one source" in b["verdict_note"]
    json.dumps(b, allow_nan=False)                                          # plain JSON: no NaN, no numpy types


def test_too_few_edges_get_no_verdict_and_the_answer_says_so():
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, SCREEN_ROWS[:1])
    assert b["ok"] and b["counts"]["edges"] == 1 and b["edges"][0]["verdict"] is None
    assert b["verdict_note"] == "1 edge is too few for the keep / prune scorer; no verdict was given."
    json.dumps(b, allow_nan=False)


def test_rows_whose_identifier_is_missing_are_reported_not_guessed():
    rows = [list(r) for r in SCREEN_ROWS]
    rows[0][1] = ""                                                          # a hit without a target identifier
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, rows)
    assert b["ok"] and b["counts"]["edges"] == 5 and b["not_mapped"] == [dict(reason="no usable object identifier", rows=1)]


def test_a_prefix_is_never_put_on_text_that_is_not_an_identifier():
    rows = [[f"CHEMBL{i}", "P00533", "50", "yes", "a"] for i in range(1, 20)] + [["CHEMBL99", "see lab book", "50", "yes", "a"]]
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, rows)             # 19 of 20 fit: the odd cell is left out and reported
    assert b["ok"] and b["counts"] == {**b["counts"], "rows": 20, "edges": 19, "rows_not_mapped": 1}
    assert b["value_notes"] == ["1 value in column target_uniprot does not look like UniProtKB identifiers (e.g. 'see lab book') "
                                "and was left out, not guessed"]
    assert not any(" " in n["id"] for n in b["nodes"]) and b["not_mapped"] == [dict(reason="no usable object identifier", rows=1)]
    rows[0][1] = rows[1][1] = "kinase"                                        # 17 of 20: the column is not that kind of identifier
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, rows)
    assert not b["ok"] and b["edges"] == [] and "17 of 20 values fit" in b["problems"][0]


def test_a_prefix_whose_values_cannot_be_checked_is_refused():
    header = ["drug", "target"]
    rows = [["aspirin", "P23219"], ["ibuprofen", "P35354"], ["my-compound-7", "P00533"]]
    card = lambda prefix: finished(f"edges:\n  - name: r\n    subject: {{column: drug, prefix: {prefix}, category: biolink:SmallMolecule}}\n"
                                   "    predicate: biolink:directly_physically_interacts_with\n"
                                   "    object: {column: target, prefix: UniProtKB, category: biolink:Protein}\n", header, rows)
    assert any("cannot be checked here" in p for p in cd.check_draft(card("UNII"), header))             # BioLink lists it, no known format
    for prefix in ("DRUGBANK", "PUBCHEM.COMPOUND", "CHEMBL.COMPOUND"):                                  # known format: names do not fit
        b = cd.build_small_graph(card(prefix), header, rows)
        assert not b["ok"] and b["nodes"] == [] and f"does not look like {prefix} identifiers: 0 of 3" in b["problems"][0]
    rows2 = [[f"CHEBI:banana{i}", "P00533"] for i in range(5)]                                           # a value with another prefix
    assert not cd.build_small_graph(card("DRUGBANK"), header, rows2)["ok"]
    ok = cd.build_small_graph(card("DRUGBANK"), header, [["DB00945", "P23219"], ["DB01050", "P35354"]])
    assert ok["ok"] and {e["subject"] for e in ok["edges"]} == {"DRUGBANK:DB00945", "DRUGBANK:DB01050"}
    assert set(cd.checked_prefixes("biolink:Gene")) == {"NCBIGene", "ENSEMBL", "HGNC"} and cd._format_for("UNII") is None
    classes = {c["category"]: c for c in cd.vocabulary()["classes"]}
    for (cat, prefix) in cd.checked_formats():
        assert cat in classes and prefix in classes[cat]["prefixes"], (cat, prefix)                     # every checked pair is BioLink's


def test_every_edge_carries_a_retrieved_date():
    import datetime
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, SCREEN_ROWS)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    assert {e["provenance"]["retrieved"] for e in b["edges"]} <= {today, (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")}
    assert kg_schema.DATE.match(b["edges"][0]["provenance"]["retrieved"])
    assert {e["provenance"]["retrieved"] for e in cd.stored_run("screen")["build"]["edges"]} == {"2026-11-04"}
    manifest = (ROOT / "data/external/DATA_MANIFEST.tsv").read_text()
    assert "hgnc/hgnc_complete_set.txt" in manifest and "\t2026-10-25T" in manifest                    # the stored date is the download date
    assert {e["provenance"]["retrieved"] for e in cd.stored_run("gene_protein")["build"]["edges"]} == {"2026-10-25"}


def test_the_hint_or_an_earlier_label_keeps_synthetic_on_every_edge():
    header, rows = ["compound", "target"], [["CHEMBL939", "P00533"], ["CHEMBL941", "P00519"]]
    d = cd.draft_card(header, rows, "these are synthetic made-up rows", model=cd.rules_only("offline"))
    assert d["ok"] and d["card"]["version"].startswith("SYNTHETIC") and d["card"]["edges"][0]["source"] == "SYNTHETIC pasted table"
    again = cd.finish_card(yaml.safe_load(d["card_yaml"]), header, rows)                                # the build step: no hint, same label
    assert again["edges"][0]["source"] == "SYNTHETIC pasted table"
    plain = cd.draft_card(header, rows, "kinase panel", model=cd.rules_only("offline"))
    assert "SYNTHETIC" not in plain["card_yaml"] and plain["card"]["edges"][0]["primary_knowledge_source"] == "tmkg:pasted-table"
    notes = {x["column"]: x["note"] for x in plain["decisions"]}
    assert "can also name a target or an assay" in notes["compound"]                                    # the class was assumed: said so


def test_notes_of_a_refused_round_are_not_shown_with_the_accepted_card():
    replies = iter([{"card_yaml": BAD_PREFIX, "decisions": [{"column": "assay", "note": "note about the refused card"}]},
                    {"card_yaml": GOOD, "decisions": []}])
    d = cd.draft_card(SCREEN_HEADER, SCREEN_ROWS, model=lambda m, s: next(replies))
    assert d["ok"] and all(x["note"] != "note about the refused card" for x in d["decisions"])


def test_byte_order_mark_and_stored_runs_writer(tmp_path):
    assert cd.parse_table("\ufeffa\tb\n1\t2\n")[0] == ["a", "b"]
    paths = cd.make_stored_runs(tmp_path, model=cd.rules_only(cd.STORED_WITHOUT_MODEL))
    assert [p.name for p in paths] == ["screen.json", "gene_protein.json"]
    for p in paths:
        assert p.read_text() == (cd.RUNS_DIR / p.name).read_text()                                      # the committed files, to the byte
    assert cd.stored_run("screen", tmp_path)["title"].endswith("(SYNTHETIC example)")


def test_build_stops_at_its_deadline(monkeypatch):
    monkeypatch.setattr(cd, "_build_in", lambda *a, **k: time.sleep(2))
    t0 = time.monotonic()
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, SCREEN_ROWS, deadline=0.2)
    assert not b["ok"] and "longer than 0.2 seconds" in b["problems"][0] and time.monotonic() - t0 < 1.5
    monkeypatch.setattr(cd, "_build_in", lambda *a, **k: (_ for _ in ()).throw(OSError("/tmp/x: disk full")))
    b = cd.build_small_graph(finished(GOOD), SCREEN_HEADER, SCREEN_ROWS)
    assert b["problems"] == ["the table could not be built with this mapping"]   # no internal message leaves


# ---------------------------------------------------------------- vocabulary and the fixed rules
def test_vocabulary_comes_from_kg_schema_the_committed_cards_and_biolink():
    bl = cd.default_biolink()
    v = cd.vocabulary()
    classes = {c["category"]: c for c in v["classes"]}
    preds = {p["predicate"] for p in v["predicates"]}
    assert all(bl.has_class(c) for c in classes) and all(bl.has_predicate(p) for p in preds)
    assert not any(t.startswith("tmkg:") for t in list(classes) + list(preds))
    for table in (kg_schema.CATEGORIES, kg_schema.ADC_CATEGORIES):
        assert {c for c in table if c.startswith("biolink:") and bl.has_class(c)} <= set(classes)   # as the builder reads BioLink
    for table in (kg_schema.PREDICATES, kg_schema.ADC_PREDICATES):
        assert {p for p in table if p.startswith("biolink:")} <= preds
    example = engine.load_card(ROOT / "examples/byo_source/lab_screen.yaml")["edges"][0]
    assert example["predicate"] in preds and example["subject"]["category"] in classes
    for c in classes.values():
        assert c["prefixes"] == list(dict.fromkeys(bl.prefixes(c["category"]))) and set(c["used_here"]) <= set(c["prefixes"])
    assert classes["biolink:Protein"]["used_here"] == ["UniProtKB"] and "MONDO" in classes["biolink:Disease"]["used_here"]
    for r in v["known_relations"]:
        assert bl.check_edge_type(r["subject"], r["predicate"], r["object"]) == []
    assert set(v["knowledge_levels"]) == kg_schema.KNOWLEDGE_LEVELS and set(v["agent_types"]) == kg_schema.AGENT_TYPES
    assert cd.pattern_prefixes(kg_schema.CATEGORIES["biolink:Disease"]) == ["MESH", "MONDO", "EFO"]


def test_identifier_formats_and_lookups_stay_inside_the_vocabulary():
    classes = {c["category"]: c for c in cd.vocabulary()["classes"]}
    for cat, prefix, local, bare in cd.ID_FORMATS:
        re.compile(local)
        assert cat in classes and prefix in classes[cat]["prefixes"] and isinstance(bare, bool), (cat, prefix)
    fits = lambda prefix, v: bool(cd._format_for(prefix).fullmatch(v))
    assert fits("NCBIGene", "1956") and fits("NCBIGene", "NCBIGene:1956") and not fits("NCBIGene", "EGFR")     # the card supplies the prefix
    assert fits("MONDO", "MONDO:0005148") and not fits("MONDO", "lung cancer") and cd._format_for("PR") is None
    for name, lk in cd.LOOKUPS.items():
        table = pd.read_csv(ROOT / lk["ref_file"], sep="\t", comment="#", dtype=str, nrows=5)
        assert {lk["key"], lk["value"]} <= set(table.columns) and lk["category"] in classes
        assert len(cd._lookup_keys(name)) > 1000
    assert cd._lookup_keys("gene_symbol") >= {"EGFR", "BRAF"} and "lung cancer" in cd._lookup_keys("disease_name")


def test_profile_column_recognises_formats_not_meanings():
    kind = lambda vals: cd.profile_column(vals)
    assert kind(["P00533", "Q9Y6K9", "A0A024R161"]) | {} == dict(kind="identifier", filled=3, distinct=3, category="biolink:Protein", prefix="UniProtKB")
    assert kind(["CHEMBL939", "CHEMBL.COMPOUND:CHEMBL1"])["prefix"] == "CHEMBL.COMPOUND"
    assert kind(["MONDO:0005148", "MONDO:0004992", ""])["category"] == "biolink:Disease"
    assert kind(["EGFR", "BRAF", "ERBB2", "ABL1", "notagene"])["lookup"] == "gene_symbol"          # 4 of 5 are approved symbols
    assert kind(["EGFR", "foo", "bar"])["kind"] == "text"
    assert kind(["yes", "no", "Yes"]) == dict(kind="flag", filled=3, distinct=3, keep=["Yes", "yes"])
    assert kind(["yes", "yes"])["kind"] == "constant" and kind(["1.5", "2", "-3e2"])["kind"] == "number"
    assert kind(["1956", "673", "3845"])["kind"] == "number"                                         # bare numbers are not called genes
    assert kind(["NCBIGene:1956", "NCBIGene:673"])["prefix"] == "NCBIGene" and kind(["ENSG00000146648"])["prefix"] == "ENSEMBL"
    assert kind(["", "-", "nan"])["kind"] == "empty" and kind(["a b c", "d e f", "g h i"])["kind"] == "text"
    assert kind(["x"] * 6 + ["y"] * 6)["kind"] == "label"


def test_fixed_rules_reproduce_the_documented_screen_example():
    reply = cd.offline_draft(SCREEN_HEADER, SCREEN_ROWS, "compound screen")
    card = yaml.safe_load(reply["card_yaml"])
    e = card["edges"][0]
    doc = engine.load_card(ROOT / "examples/byo_source/lab_screen.yaml")["edges"][0]
    assert (e["subject"], e["predicate"], e["object"], e["filter"]) == (doc["subject"], doc["predicate"], doc["object"], doc["filter"])
    assert e["knowledge_level"] == "observation" and e["agent_type"] == "not_provided"            # how it was made: only what the hint says
    assert card["prune"] == {"score_columns": {"signal_pct": "higher"}}
    assert [d["column"] for d in reply["decisions"]] == SCREEN_HEADER
    assert cd.offline_draft(SCREEN_HEADER, SCREEN_ROWS, "compound screen") == reply               # the same answer every time


def test_fixed_rules_map_gene_symbols_through_the_lookup_and_report_unknown_ones():
    header = ["gene", "disease", "p_value", "comment"]
    rows = [["EGFR", "MONDO:0005233", "0.001", "strong"], ["BRAF", "MONDO:0005105", "0.02", "seen twice in the cohort"],
            ["KRAS", "MONDO:0005233", "0.04", "borderline"], ["TP53", "MONDO:0004992", "0.5", ""], ["NOTAGENE1", "MONDO:0004992", "0.9", "?"]]
    d = cd.draft_card(header, rows, model=cd.rules_only("offline"))
    assert d["ok"], d["report"]
    e = d["card"]["edges"][0]
    assert e["subject"] == dict(column="gene", lookup="gene_symbol", category="biolink:Gene")
    assert e["object"] == dict(column="disease", prefix="MONDO", category="biolink:Disease")
    assert e["predicate"] == "biolink:gene_associated_with_condition" and d["card"]["prune"] == {"score_columns": {"p_value": "lower"}}
    assert d["report"]["sample"]["not_mapped"] == [dict(reason="no subject identifier", rows=1)]      # NOTAGENE1 is not guessed
    by_col = {x["column"]: x["text"] for x in d["decisions"] if x["column"]}
    assert "identifier found through the lookup gene_symbol" in by_col["gene"] and by_col["comment"] == "column comment not mapped: free text"
    b = cd.build_small_graph(d["card"], header, rows)
    assert b["ok"] and {x["subject"] for x in b["edges"]} == {"NCBIGene:1956", "NCBIGene:673", "NCBIGene:3845", "NCBIGene:7157"}
    hgnc = pd.read_csv(ROOT / "data/reference/hgnc_gene_ids.tsv", sep="\t", dtype=str).drop_duplicates("symbol").set_index("symbol")["ncbigene_id"]
    assert [hgnc[s] for s in ("EGFR", "BRAF", "KRAS", "TP53")] == ["NCBIGene:1956", "NCBIGene:673", "NCBIGene:3845", "NCBIGene:7157"]


def test_fixed_rules_prefer_the_identifier_system_biolink_lists_first():
    header, rows = cd.read_example_table("gene_protein")
    e = yaml.safe_load(cd.offline_draft(header, rows)["card_yaml"])["edges"][0]
    assert e["subject"] == dict(column="ncbigene_id", prefix="NCBIGene", category="biolink:Gene")     # not hgnc_id / ensembl_id / symbol
    assert e["predicate"] == "biolink:has_gene_product" and e["object"]["column"] == "uniprot_id"
    assert e["knowledge_level"] == "not_provided"                                                      # the table does not say


def test_fixed_rules_do_not_guess_a_relation_between_two_columns_of_one_class():
    header = ["protein_a", "protein_b", "score"]
    rows = [["P00533", "P04626", "0.9"], ["P00519", "P15056", "0.4"], ["P04626", "P00519", "0.7"]]
    assert cd.offline_draft(header, rows)["card_yaml"] == ""                                          # which way, which relation: not said
    card = yaml.safe_load(cd.offline_draft(header, rows, "pairs of proteins that physically interact")["card_yaml"])
    e = card["edges"][0]
    assert (e["subject"]["column"], e["predicate"], e["object"]["column"]) == ("protein_a", "biolink:physically_interacts_with", "protein_b")
    d = cd.draft_card(header, rows, "pairs of proteins that physically interact", model=cd.rules_only("offline"))
    assert d["ok"] and d["report"]["sample"]["edges"] == 3


def test_a_bare_number_column_is_accepted_when_the_card_names_the_system_and_the_relation_lists_alternatives():
    header = ["entrez", "disease"]
    rows = [["1956", "MONDO:0005233"], ["673", "MONDO:0005105"], ["3845", "MONDO:0005233"]]
    text = ("edges:\n  - name: r\n    subject: {column: entrez, prefix: NCBIGene, category: biolink:Gene}\n"
            "    predicate: biolink:gene_associated_with_condition\n    object: {column: disease, prefix: MONDO, category: biolink:Disease}\n")
    d = cd.draft_card(header, rows, model=scripted(text))
    assert d["ok"] and cd.build_small_graph(d["card"], header, rows)["edges"][0]["subject"].startswith("NCBIGene:")
    relation = next(x for x in d["decisions"] if x["column"] is None and x["text"].startswith("relation"))
    assert relation["note"] == "other relations used for these two classes: biolink:associated_with, biolink:genetically_associated_with"
    junk = [dict(card, edges=[dict(card["edges"][0], subject=dict(column="entrez", category="biolink:Gene", lookup=["x"]))])
            for card in [d["card"]]]
    assert any("does not exist" in p for p in cd.check_draft(junk[0], header))                          # an odd value, not a crash


# ---------------------------------------------------------------- the two stored runs
def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


@pytest.mark.parametrize("example_id", list(cd.EXAMPLES))
def test_stored_run_is_what_the_code_gives_today(example_id):
    run = cd.stored_run(example_id)
    assert run is not None and run["id"] == example_id and run["draft"]["ok"] and run["build"]["ok"]
    header, rows = cd.read_example_table(example_id)
    assert run["table"] == dict(header=header, rows=rows)
    card = run["draft"]["card"]
    assert cd.check_draft(card, header) == [] and yaml.safe_load(run["draft"]["card_yaml"]) == card
    assert cd.build_small_graph(card, header, rows, retrieved=cd.EXAMPLES[example_id]["provenance"]["retrieved"]) == run["build"]         # the stored edges are the edges of the stored card
    if run["draft"]["drafted_by"]["kind"] == "rules":                       # made without a model: reproducible to the byte
        again = cd.make_stored_run(example_id, model=cd.rules_only(cd.STORED_WITHOUT_MODEL))
        assert json.loads(json.dumps(again)) == run
    assert set(header) <= {d["column"] for d in run["draft"]["decisions"]}
    assert all(e["provenance"]["source"] and e["verdict"] for e in run["build"]["edges"])


def test_stored_screen_run_is_labelled_synthetic_and_matches_the_tutorial():
    run = cd.stored_run("screen")
    assert "SYNTHETIC" in run["label"] and run["build"]["counts"]["nodes"] == 9 and run["build"]["counts"]["edges"] == 6
    assert all("SYNTHETIC" in e["provenance"]["source"] and "SYNTHETIC" in e["provenance"]["source_version"] for e in run["build"]["edges"])
    with gzip.open(ROOT / "examples/byo_source/screen.tsv.gz", "rt") as fh:
        assert cd.parse_table(fh.read()) == (run["table"]["header"], run["table"]["rows"])


def test_second_stored_run_is_a_named_slice_of_a_tracked_public_table():
    run = cd.stored_run("gene_protein")
    source = pd.read_csv(ROOT / "data/reference/hgnc_gene_ids.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert run["table"]["header"] == list(source.columns) and run["table"]["rows"] == source.head(20).values.tolist()
    assert "HGNC" in run["source_named"] and "20 rows" in run["label"] and "SYNTHETIC" not in json.dumps(run)
    assert run["build"]["counts"]["edges"] == 19 and run["build"]["not_mapped"] == [dict(reason="no usable object identifier", rows=1)]
    assert all(e["predicate"] == "biolink:has_gene_product" and e["provenance"]["primary_knowledge_source"] == "infores:hgnc"
               for e in run["build"]["edges"])
    assert {e["subject"]: e["object"] for e in run["build"]["edges"]} == {
        r.ncbigene_id: "UniProtKB:" + r.uniprot_id for r in source.head(20).itertuples() if r.uniprot_id}


@pytest.mark.parametrize("example_id", list(cd.EXAMPLES))
def test_nothing_a_user_sees_in_a_stored_run_names_files_or_internals(example_id):
    banned = re.compile(r"(\.tsv|\.ya?ml|\.json|\.py\b|data/|outputs/|pipelines/|examples/|/tmp|\bG[0-4]\b|\bPK\d|\bNIM\b|stand-in|fallback|snapshot|ref_file)")
    for s in strings(cd.stored_run(example_id)):
        assert not banned.search(s), s
    listed = {x["id"]: x for x in cd.stored_runs_list()}
    assert listed[example_id]["available"] and listed[example_id]["edges"] == cd.stored_run(example_id)["build"]["counts"]["edges"]


def test_stored_run_only_reads_known_ids():
    assert cd.stored_run("../../CLAUDE") is None and cd.stored_run("screen.json") is None and cd.stored_run("") is None
    assert cd.stored_runs_list(runs_dir=ROOT / "no_such_folder")[0]["available"] is False


def test_command_line_draft_prints_the_card(tmp_path, capsys):
    p = tmp_path / "t.tsv"
    p.write_text(cd.table_text(SCREEN_HEADER, SCREEN_ROWS))
    import nim as nimmod
    orig = nimmod.NIM
    nimmod.NIM = lambda **k: type("N", (), dict(backend="offline"))()      # no model call from a test
    try:
        assert cd.main(["draft", str(p), "compound screen"]) == 0
    finally:
        nimmod.NIM = orig
    out = capsys.readouterr().out
    assert "biolink:directly_physically_interacts_with" in out and "column hit decides which rows become edges" in out
    assert cd.main([]) == 2
