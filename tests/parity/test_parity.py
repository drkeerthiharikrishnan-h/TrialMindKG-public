"""The parity suite (tests/parity/parity_suite.py) with no network: the offline stand-in passes all 25 pinned questions; a
stubbed hosted endpoint that writes DIFFERENT but equivalent queries agrees with it; one that drifts (wrong function, fewer
edges, API errors) is caught; the snapshot cache answers a second run without the endpoint and says so; and a failing
endpoint falls back to the stand-in only when asked to, visibly."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parity_suite as ps  # noqa: E402
import nim as nimmod  # noqa: E402
from kg_query import KGIndex  # noqa: E402
from snapshot import Snapshot  # noqa: E402


@pytest.fixture(scope="module")
def kg():
    return KGIndex()


@pytest.fixture(scope="module")
def questions():
    return ps.load_questions()


@pytest.fixture(scope="module")
def offline_rows(kg):
    return ps.run_suite(kg, nimmod.NIM(backend="offline"))


def test_there_are_25_distinct_pinned_questions_on_real_kg_query_functions(kg, questions):
    assert len(questions) == 25 and len({q["id"] for q in questions}) == 25 and len({q["question"] for q in questions}) == 25
    assert {q["relation"] for q in questions} <= set(ps.EVIDENCE_NOTES)
    for q in questions:
        assert callable(getattr(kg, q["function"])), q["id"]
        assert ps.reference(kg, q)["evidence"], f"{q['id']}: the pinned function returns no edge"
    assert {q["function"] for q in questions} == {"program_trials", "trial_outcomes", "program_history", "drug_targets",
                                                  "trial_drugs", "trial_publications", "trial_safety", "shared_targets"}


def test_the_ten_gold_questions_are_included_with_their_functions(questions):
    import csv
    gold = list(csv.DictReader(open("data/curated/cypher_gold.csv", encoding="utf-8")))
    by_q = {q["question"]: q for q in questions}
    for g in gold:
        q = by_q[g["question"]]
        assert q["function"] == g["check"].split()[0] and q["relation"] == g["note"], g["id"]


def test_classify_names_the_function_from_the_relationships():
    assert ps.classify("MATCH (p)-[r:has_trial]->(t) RETURN t.id, r.id LIMIT 5") == "program_trials"
    assert ps.classify("MATCH (p)-[r:has_trial]->(t) RETURN t.ae_groups, r.id LIMIT 5") == "trial_safety"
    assert ps.classify("MATCH (p)-[r:has_post_failure_analysis|has_repurposing_pivot]->(e) RETURN r.id LIMIT 5") == "program_history"
    assert ps.classify("MATCH (a)-[r1:has_intervention]->(d)-[r2:interacts_with]->(t) RETURN r1.id LIMIT 5") == "shared_targets"
    assert ps.classify("MATCH (d)-[r:interacts_with]->(t) RETURN r.id LIMIT 5") == "drug_targets"
    assert ps.classify("MATCH (t)-[r:tests_intervention|has_comparator_drug]->(d) RETURN r.id LIMIT 5") == "trial_drugs"
    assert ps.classify("MATCH (t)-[r:has_publication]->(p) RETURN r.id LIMIT 5") == "trial_publications"
    assert ps.classify("MATCH (t)-[r:has_outcome_measure]->(o) RETURN r.id LIMIT 5") == "trial_outcomes"
    assert ps.classify("") == "other" and ps.classify("MATCH (n) RETURN n LIMIT 1") == "other"


def test_the_offline_stand_in_passes_all_25(offline_rows):
    assert len(offline_rows) == 25
    failed = [(r["id"], r["reason"]) for r in offline_rows if not r["passed"]]
    assert failed == []
    assert {r["backend"] for r in offline_rows} == {"offline-stand-in"} and not any(r["cached"] for r in offline_rows)


# ---- a stubbed hosted endpoint ----
def hosted_nim(tmp_path, kg, rewrite=lambda q, cy: cy, fail=lambda q: False):
    """A NIM whose HTTP layer answers each querier request from the offline stand-in's query, passed through `rewrite`
    (question, cypher) -> cypher; `fail(question)` makes the endpoint raise for that question."""
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    off = nimmod.NIM(backend="offline")
    calls = []

    def fake_http(method, url, params, body, headers=None, store_client_errors=True):
        user = [m for m in body["messages"] if m["role"] == "user"][0]["content"]
        question = user.rsplit("QUESTION: ", 1)[-1].split("\n")[0]
        calls.append(question)
        if fail(question):
            raise RuntimeError("POST failed after retries: HTTP 404 not found for account")
        cy = off.chat("querier", [m for m in body["messages"] if m["role"] != "system"][:1]
                      + [m for m in body["messages"] if m["role"] == "user"][:1], schema=None).text
        import json
        cy = json.loads(cy)["cypher"]
        return {"choices": [{"message": {"content": json.dumps({"cypher": rewrite(question, cy), "explanation": "stub"})}}],
                "usage": {"total_tokens": 1}}
    snap._http = fake_http
    n = nimmod.NIM(backend="nim", snap=snap, fallback="none")
    n.key = "nvapi-stub"
    n.calls_made = calls
    return n


def bump_limit(q, cy):                       # a different text for the same query: LIMIT changed, whitespace added
    return re.sub(r"LIMIT (\d+)", lambda m: f"LIMIT {int(m.group(1)) * 2}", cy).replace(" RETURN ", "  RETURN ")


def test_a_hosted_backend_with_other_query_text_agrees_with_the_stand_in(tmp_path, kg, offline_rows):
    n = hosted_nim(tmp_path, kg, rewrite=bump_limit)
    rows = ps.run_suite(kg, n)
    assert all(r["passed"] for r in rows), [(r["id"], r["reason"]) for r in rows if not r["passed"]]
    assert {r["backend"] for r in rows} == {"nim"} and not any(r["cached"] for r in rows)
    assert all(r["cypher"] != o["cypher"] for r, o in zip(rows, offline_rows))             # the texts differ ...
    agree = ps.agreement(offline_rows, rows)
    assert agree["n"] == 25 and agree["same_function"] == 25 and agree["same_evidence"] == 25 and agree["both_pass"] == 25
    assert agree["disagreements"] == []
    text = ps.report_markdown({"offline": offline_rows, "hosted": rows}, agree)
    assert "25 of 25 questions" in text and "25 of 25 same evidence-id set" in text and "FAIL" not in text


def test_drift_is_caught_wrong_function_missing_edges_and_errors(tmp_path, kg, offline_rows):
    pub = "MATCH (t:ClinicalTrial {id:'NCT:NCT02525939'})-[r:has_publication]->(p:Publication) RETURN p.id AS pmid, r.id AS evidence LIMIT 5"

    def drift(q, cy):
        if q.startswith("Which trials are in the dalcetrapib"):
            return pub                                                   # a different function
        if q.startswith("What were the serious adverse events and deaths per arm in METREO"):
            return cy.replace("LIMIT 10", "LIMIT 1").replace("t:ClinicalTrial {id:'NCT:NCT02105961'}", "t:ClinicalTrial {id:'NCT:NCT01739348'}")
        return cy
    n = hosted_nim(tmp_path, kg, rewrite=drift, fail=lambda q: q.startswith("Which failed programs share"))
    rows = ps.run_suite(kg, n)
    bad = {r["id"]: r for r in rows if not r["passed"]}
    assert set(bad) == {"Q10", "Q07", "Q08"}
    assert "expected 'program_trials'" in bad["Q10"]["reason"] and "evidence differs" in bad["Q07"]["reason"]
    assert bad["Q08"]["function"] == "error" and "404" in bad["Q08"]["problems"][0]
    agree = ps.agreement(offline_rows, rows)
    assert agree["both_pass"] == 22 and {d["id"] for d in agree["disagreements"]} == {"Q10", "Q07", "Q08"}
    assert sum(r["passed"] for r in rows) == 22 and "22 of 25 questions" in ps.report_markdown({"hosted": rows})


def test_a_warmed_question_is_answered_from_the_snapshot_without_the_endpoint_and_says_so(tmp_path, kg):
    n = hosted_nim(tmp_path, kg)
    first = ps.run_suite(kg, n, ps.load_questions()[:3])
    assert not any(r["cached"] for r in first) and len(n.calls_made) == 3
    again = hosted_nim(tmp_path, kg, fail=lambda q: True)                # same folder; the endpoint is now down
    second = ps.run_suite(kg, again, ps.load_questions()[:3])
    assert all(r["passed"] and r["cached"] for r in second) and again.calls_made == []
    assert [r["evidence"] for r in second] == [r["evidence"] for r in first]
    assert "3 answered from the snapshot cache" in ps.report_markdown({"hosted": second})
    unwarmed = ps.run_suite(kg, again, ps.load_questions()[3:4])          # not in the cache, endpoint down: visible failure
    assert not unwarmed[0]["passed"] and unwarmed[0]["function"] == "error"


def test_fallback_to_the_stand_in_is_opt_in_and_labelled(tmp_path, kg):
    import querier as Q
    down = hosted_nim(tmp_path, kg, fail=lambda q: True)
    q = ps.load_questions()[0]
    assert ps.answer(Q.Querier(down, kg), q)["function"] == "error"       # default: the failure shows
    down.fallback = "offline"
    res = Q.Querier(down, kg).ask(q["question"])
    assert res["backend"] == "offline-stand-in" and res["fallback_from"] == "nim" and not res["problems"]
    assert ps.judge(q, ps.answer(Q.Querier(down, kg), q), ps.reference(kg, q))["passed"]
    r = down.chat("querier", [{"role": "user", "content": "QUESTION: x"}])
    assert r.backend == "offline-stand-in" and "404" in r.meta["reason"] and r.meta["fallback_from"] == "nim"


def test_fallback_without_a_stand_in_for_the_role_still_raises(tmp_path, kg):
    down = hosted_nim(tmp_path, kg, fail=lambda q: True)
    down.fallback = "offline"
    with pytest.raises(RuntimeError):
        down.chat("baseline", [{"role": "user", "content": "QUESTION: x"}])
