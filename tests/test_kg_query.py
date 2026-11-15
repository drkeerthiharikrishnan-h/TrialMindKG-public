"""Tests for the answer harness (pipelines/kg_query.py) on the committed graph."""
import pytest
import kg_query as q


@pytest.fixture(scope="module")
def kg():
    return q.KGIndex()


def test_golden_questions_all_pass(kg):
    failed = [r for r in q.run_golden(kg) if not r["passed"]]
    assert not failed, failed


def test_answers_are_deterministic_and_cite_real_edges(kg):
    a, b = kg.program_target_indication("P01"), kg.program_target_indication("P01")
    assert a == b and a["rows"]
    assert all(e in kg.edges for e in a["evidence"])
    assert all(r["evidence"] for r in a["rows"])


def test_verify_answer(kg):
    ev = kg.trial_outcomes("NCT02446912")["rows"][0]["evidence"][0]
    assert q.verify_answer(f"TULIP-1 missed its primary endpoint at Week 52 (p=0.412) [{ev}].", kg)["ok"]
    bad = q.verify_answer(f"TULIP-1 missed its primary endpoint (p=0.03) [{ev}].", kg)
    assert not bad["ok"] and bad["unsupported_numbers"] == ["0.03"]
    assert not q.verify_answer("No citation at all.", kg)["ok"]
    assert q.verify_answer("x [e0000000000000000]", kg)["unknown_citations"] == ["e0000000000000000"]
    assert q.verify_answer(f"p=0.412 [{ev}]", kg, allowed=[])["not_allowed"] == [ev]


def test_provenance_points_to_raw_response(kg):
    ev = kg.trial_outcomes("NCT02446912")["rows"][0]["evidence"][0]
    row = kg.provenance(ev)["rows"][0]
    # registry statements point to the frozen AACT record (default source) or the API response (fallback)
    assert row["raw_response"].startswith(("data/snapshots/aact/", "data/snapshots/ctgov/"))
    from snapshot import ROOT
    assert (ROOT / row["raw_response"]).exists()


def test_numbers_are_compared_by_value(kg):
    ev = kg.trial_outcomes("NCT02446912")["rows"][0]["evidence"][0]
    from kg_query import verify_answer
    assert verify_answer(f"95% CI, p = 0.412 [{ev}]", kg)["unsupported_numbers"] == []      # data has 95.0 / 0.412
    assert verify_answer(f"p = 0.0412 [{ev}]", kg)["unsupported_numbers"] == ["0.0412"]


def test_numbers_reads_ranges_and_never_truncates():
    from kg_query import numbers
    assert numbers("HR 0.88 (95% CI 0.75-1.03, p=0.12).") == ["0.88", "95", "0.75", "1.03", "0.12"]
    assert numbers("CI 6.7 - 26.2; change -4.2 in TULIP-1 and IL-5") == ["6.7", "26.2", "-4.2"]


def test_trial_adverse_events_and_design():
    from kg_query import KGIndex
    kg = KGIndex()
    ae = kg.trial_adverse_events("NCT02446912", term="pneumonia", kind="serious")
    assert ae["rows"] and all("pneumonia" in r["term"].lower() for r in ae["rows"])
    arms = {a["group"]: a for a in ae["rows"][0]["arms"]}
    assert arms["EG000"]["at_risk"] == 93                     # as posted (Anifrolumab 150 mg arm)
    assert ae["evidence"] and all(e in kg.edges for e in ae["evidence"])
    d = kg.trial_design("NCT02446912")
    parts = {r["part"] for r in d["rows"]}
    assert parts == {"population", "arm", "endpoint"}
    pop = next(r for r in d["rows"] if r["part"] == "population")
    assert "Inclusion Criteria" in pop["eligibility_criteria"]
    arm = next(r for r in d["rows"] if r["part"] == "arm" and "higher" in r["name"])
    assert arm["drugs"] == ["ANIFROLUMAB"] and arm["arm_type"] == "EXPERIMENTAL"
