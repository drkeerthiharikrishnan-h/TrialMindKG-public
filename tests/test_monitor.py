"""Tests for pipelines/monitor.py (graph diff on small in-memory graphs)."""
import monitor


def g(nodes, edges):
    return {n["id"]: n for n in nodes}, {e["id"]: e for e in edges}


OLD = g([dict(id="P19"), dict(id="NCT:NCT02446912", status="COMPLETED"), dict(id="CHEMBL:CHEMBL1")],
        [dict(id="e1", subject="P19", predicate="tmkg:has_trial", object="NCT:NCT02446912"),
         dict(id="e2", subject="P19", predicate="tmkg:has_intervention", object="CHEMBL:CHEMBL1")])


def test_no_change():
    d = monitor.graph_diff(OLD, OLD)
    assert not any(d[k] for k in ("nodes_added", "nodes_removed", "nodes_changed", "edges_added", "edges_removed"))


def test_changes_are_attributed_to_trials_programs_and_questions():
    nodes, edges = dict(OLD[0]), dict(OLD[1])
    nodes["NCT:NCT02446912"] = dict(id="NCT:NCT02446912", status="TERMINATED")
    nodes["OUTCOME:NCT02446912:P1"] = dict(id="OUTCOME:NCT02446912:P1")
    edges["e3"] = dict(id="e3", subject="ENSEMBL:ENSG1", predicate="biolink:associated_with_response_to",
                       object="CHEMBL:CHEMBL1")
    d = monitor.graph_diff(OLD, (nodes, edges))
    assert d["nodes_changed"] == ["NCT:NCT02446912"] and d["nodes_added"] == ["OUTCOME:NCT02446912:P1"]
    assert d["by_trial"] == {"NCT:NCT02446912": 2} and d["by_program"]["P19"] == 3
    qs = [dict(id="B01", program="P19"), dict(id="B02", program="P20")]
    assert monitor.affected_questions(d, qs) == ["B01"]
    assert "B01" in monitor.to_markdown(d, qs)


def test_empty_new_columns_are_not_changes():
    assert monitor._read_csv("id,a,b\nx,1,\n") == {"x": {"id": "x", "a": "1"}}


def test_record_changes_compares_each_table():
    import aact
    from snapshot import Response
    frozen = {t: [] for t in aact.TABLES}
    frozen["studies"] = [dict(nct_id="NCT1", overall_status="Completed")]

    class Snap:
        def call(self, source, request, fn):
            return Response(frozen, "t", "k", source)

    class Src:
        def _query(self, table, ncts):
            if table == "studies":
                return [dict(nct_id="NCT1", overall_status="Terminated")]
            return []
    assert monitor.record_changes(Src(), Snap(), ["NCT1"]) == [dict(nct="NCT1", changed_tables=["studies"])]
