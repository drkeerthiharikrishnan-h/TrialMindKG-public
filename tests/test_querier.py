"""Tests for pipelines/querier.py: subset parser/validator, local execution vs kg_query, Neo4j dialect, agent loop."""
import json
import pytest
import querier as Q
from kg_query import KGIndex
from nim import NIM


@pytest.fixture(scope="module")
def kg():
    return KGIndex()


@pytest.fixture(scope="module")
def card(kg):
    return Q.schema_card(kg)


@pytest.mark.parametrize("bad,msg", [
    ("MATCH (n) DETACH DELETE n", "not allowed"),
    ("MATCH (a)-[r:has_trial]->(b) SET a.x = 1 RETURN r.id LIMIT 1", "not allowed"),
    ("MATCH (a)-[r:has_trial*1..3]->(b) RETURN r.id LIMIT 5", "variable-length"),
    ("MATCH (a:ClinicalTrialProgram)-[r:has_trial]->(b) RETURN b.name", "LIMIT is required"),
    ("MATCH (a:Trial)-[r:has_trial]->(b) RETURN r.id LIMIT 5", "unknown label"),
    ("MATCH (a)-[r:cures]->(b) RETURN r.id LIMIT 5", "unknown relationship type"),
    ("MATCH (a:Drug)-[r:interacts_with]->(b) RETURN a.efficacy, r.id LIMIT 5", "unknown property"),
    ("MATCH (a:Drug)-[r:interacts_with]->(b) RETURN a.name LIMIT 5", "relationship id as evidence"),
    ("MATCH (a:Drug)-[r:interacts_with]->(b) RETURN a.name, r.id LIMIT 100000", "LIMIT must be"),
])
def test_validator_rejects(card, bad, msg):
    probs = Q.validate(bad, card)
    assert any(msg in p for p in probs), probs


def test_starts_with_is_not_a_forbidden_with(card):
    assert Q.validate("MATCH (d:Drug)-[r:interacts_with]->(t) WHERE d.name STARTS WITH 'ANIF' "
                      "RETURN d.name, r.id LIMIT 5", card) == []


def test_gold_queries_agree_with_kg_query(kg, card):
    for g in Q.load_gold():
        assert Q.validate(g["cypher"], card) == [], g["id"]
        got = set(Q.run_local(g["cypher"], kg)["evidence"])
        fn, *args = g["check"].split()
        ref = getattr(kg, fn)(**dict(a.split("=", 1) for a in args))
        want = set(ref["evidence"])
        # note: equal = same edges; covers = subset that represents every reference row; subset = part of the answer
        assert got and (got == want if g["note"] == "equal" else got <= want), g["id"]
        if g["note"] == "covers":
            assert all(set(r["evidence"]) & got for r in ref["rows"]), g["id"]


def test_where_filters_aggregates_and_order(kg):
    r = Q.run_local("MATCH (p:ClinicalTrialProgram)-[r:has_trial]->(t:ClinicalTrial) WHERE t.why_stopped IS NOT NULL "
                    "RETURN p.id AS program, count(t.id) AS n, collect(r.id) AS evidence ORDER BY n DESC LIMIT 100", kg)
    assert r["rows"] and all(x["n"] == len(x["evidence"]) for x in r["rows"])
    assert [x["n"] for x in r["rows"]] == sorted((x["n"] for x in r["rows"]), reverse=True)
    stopped = {e["id"] for e in kg.edges.values() if e["predicate"] == "tmkg:has_trial"
               and kg.nodes[e["object"]].get("why_stopped")}
    assert set(r["evidence"]) == stopped


def test_to_neo4j_uppercases_only_relationship_types():
    q = "MATCH (a:Drug {id:'x:has_trial'})-[r:interacts_with|:has_trial]->(b) RETURN r.id LIMIT 1"
    assert Q.to_neo4j(q) == "MATCH (a:Drug {id:'x:has_trial'})-[r:INTERACTS_WITH|:HAS_TRIAL]->(b) RETURN r.id LIMIT 1"


def test_agent_retries_once_with_validator_feedback(kg, monkeypatch):
    replies = iter(['{"cypher": "MATCH (a:Trial)-[r:has_trial]->(b) RETURN r.id LIMIT 5"}',
                    json.dumps({"cypher": Q.load_gold()[0]["cypher"]})])
    seen = []
    def fake(messages, schema):
        seen.append(messages)
        return next(replies)
    monkeypatch.setitem(__import__("nim")._HANDLERS, "querier", fake)
    out = Q.Querier(NIM(backend="offline"), kg).ask("Which trials belong to the TULIP-1 program?")
    assert out["problems"] == [] and len(out["attempts"]) == 2 and out["evidence"]
    assert "unknown label :Trial" in seen[1][-1]["content"]


def test_offline_evaluation_runs(kg):
    r = Q.evaluate(NIM(backend="offline"), kg)
    assert r["n"] == len(Q.load_gold()) and r["valid_rate"] == 1.0


def test_validator_rejects_values_that_do_not_exist(card):
    probs = Q.validate("MATCH (t:ClinicalTrial)-[r:has_outcome_measure]->(o:OutcomeMeasure) WHERE o.kind = 'SAFETY' "
                       "RETURN o.name, r.id LIMIT 5", card)
    assert any("no value 'SAFETY'" in p and "PRIMARY" in p for p in probs)
    assert Q.validate("MATCH (t:ClinicalTrial)-[r:has_outcome_measure]->(o:OutcomeMeasure) WHERE o.kind IN "
                      "['PRIMARY', 'SECONDARY'] RETURN o.name, r.id LIMIT 5", card) == []
    assert "target" in Q.card_text(card) and "OutcomeMeasure.kind: OTHER_PRE_SPECIFIED" in Q.card_text(card)


def test_malformed_reply_counts_as_failed_attempt(kg, monkeypatch):
    replies = iter(['{"cypher": "MATCH", "explanation": unquoted}', json.dumps({"cypher": Q.load_gold()[0]["cypher"]})])
    monkeypatch.setitem(__import__("nim")._HANDLERS, "querier", lambda m, s: next(replies))
    out = Q.Querier(NIM(backend="offline"), kg).ask("Which trials belong to the TULIP-1 program?")
    assert out["problems"] == [] and len(out["attempts"]) == 2 and "JSON" in out["attempts"][0]["problems"][0]


def test_fix_hints():
    import querier
    h = querier.fix_hints(["undefined variable r1", "expected RETURN, found 'MATCH'"])
    assert "Name every relationship" in h and "ONE MATCH" in h
    assert querier.fix_hints(["LIMIT must be between 1 and 500"]) == ""


def test_sql_style_in_list_is_accepted():
    import querier
    from kg_query import KGIndex
    kg = KGIndex()
    cy = ("MATCH (p:ClinicalTrialProgram)-[r:has_trial]->(t:ClinicalTrial) WHERE p.id IN ('P19', 'P20') "
          "RETURN t.id AS trial, r.id AS evidence ORDER BY trial LIMIT 10")
    assert querier.validate(cy, querier.schema_card(kg)) == []
    assert {row["trial"] for row in querier.run_local(cy, kg)["rows"]} == {"NCT:NCT02446912", "NCT:NCT02105961"}


def test_pattern_examples_are_valid_and_not_gold_questions():
    import querier
    from kg_query import KGIndex
    kg = KGIndex()
    card = querier.schema_card(kg)
    gold = {g["question"] for g in querier.load_gold()}
    for ex in querier.PATTERN_EXAMPLES:
        assert querier.validate(ex["cypher"], card) == [], ex["question"]
        assert ex["question"] not in gold and querier.run_local(ex["cypher"], kg)["rows"], ex["question"]
