"""CC-31d (review finding M7): a condition the model wrote in an earlier attempt and left out of the query that ran is recorded
(querier.dropped_constraints) and shown in the plan and the bottom line, so a question is never narrowed silently."""
import json
import sys

import pytest

sys.path.insert(0, "pipelines")
import querier  # noqa: E402
import answer_summary  # noqa: E402
from nim import NIM  # noqa: E402
from snapshot import Snapshot  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402

A1 = ("MATCH (g:Gene {name:'PTGS2'})-[r:gene_associated_with_condition]->(d:Disease) WHERE d.approved = true AND r.source = 'CTD' "
      "RETURN d.name AS disease, r.id AS evidence LIMIT 10")
A2 = ("MATCH (g:Gene {name:'PTGS2'})-[r:gene_associated_with_condition]->(d:Disease) WHERE r.source = 'CTD' "
      "RETURN d.name AS disease, r.id AS evidence LIMIT 10")


def test_constraints_name_classes_not_variables():
    c = querier.constraints(A1)
    assert c == ["Gene.name = 'PTGS2'", "relationship gene_associated_with_condition", "Disease.approved = True",
                 "gene_associated_with_condition.source = 'CTD'"]
    renamed = A2.replace("(d:Disease)", "(x:Disease)").replace("d.name", "x.name")
    assert set(querier.constraints(renamed)) <= set(c) and querier.constraints("not cypher") == []


def test_dropped_constraints_lists_what_the_final_query_left_out():
    att = [dict(cypher=A1), dict(cypher=A2)]
    assert querier.dropped_constraints(att, A2) == [dict(text="Disease.approved = True", attempt=1)]
    assert querier.dropped_constraints([dict(cypher=A2)], A2) == []
    assert querier.dropped_constraints([dict(cypher=""), dict(cypher=A2)], A2) == []


def stub(replies, tmp_path):
    """A hosted NIM whose endpoint answers the n-th request with replies[n]."""
    snap = Snapshot(tmp_path, mode="live", sleep=0)
    it = iter(replies)
    snap._http = lambda m, u, p, b, headers=None, store_client_errors=True, **kw: {
        "choices": [{"message": {"content": json.dumps({"cypher": next(it), "explanation": "stub"})}}], "usage": {}}
    n = NIM(backend="nim", snap=snap)
    n.key = "nvapi-stub"
    return n


def test_ask_records_the_dropped_condition_and_the_summary_says_so(tmp_path):
    g = PharosGraph(root=build(tmp_path / "repo"))
    out = querier.Querier(stub([A1, A2], tmp_path / "llm"), g, examples=[], pattern_examples=[], evidence_first=True).ask(
        "Which approved diseases is PTGS2 linked to by CTD?", entities=[])
    assert len(out["attempts"]) == 2 and "unknown property d.approved" in out["attempts"][0]["problems"][0]
    assert out["rows"] and out["dropped_constraints"] == [dict(text="Disease.approved = True", attempt=1)]
    assert out["explanation"] == "stub"
    s = answer_summary.pharos_summary(out)
    assert s["plan"]["dropped"] == out["dropped_constraints"]
    assert s["caveats"][0] == ("The query that ran leaves out a condition an earlier attempt had (Disease.approved = True); "
                               "the rows are not limited by it.")
    assert any(c["name"] == "conditions kept" and not c["ok"] for c in s["checks"])
