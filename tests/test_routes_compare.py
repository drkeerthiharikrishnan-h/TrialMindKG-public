"""Tests for pipelines/routes_compare.py: Pharos alone versus the agent-built graph on the five tested questions, one entity
card, the marked graph payload and the recomputed bottom line. Fixture graph only (tests/fixtures/pharos_fixture.py plus a few
enrichment links written here; no real data, no network)."""
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "pipelines")
import app as appmod  # noqa: E402
import pharos_app  # noqa: E402
import routes_compare as rc  # noqa: E402
from nim import NIM  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402

from fixtures.pharos_fixture import build, write, edge, GENE, PROT, DIS1, DIS2, CMP  # noqa: E402

SOD1, CANCER, ASPIRIN, BP2 = "NCBIGene:6647", "MONDO:0004992", "CHEMBL.COMPOUND:CHEMBL25", "GO:0008217"
OT = ("Open Targets 26.09 overall direct", "opentargets_layer", "26.09")
E = lambda i: f"e{i:016x}"                       # noqa: E731  (the fixture's edge ids)
# What a user must never read (packet rule): file paths, run tags, graph codes, packet ids, layer file names.
FORBIDDEN = re.compile(r"\bG[0-4]\b|\bPK\d|\bCC-\d|_layer\b|opentargets|chembl37|\.py\b|\.json\b|\.tsv|outputs/|data/|@frozen|kfold")


def enrich(root):
    """Adds enrichment links to the fixture so that every tested question has a row that Pharos alone lacks:
    PTGS2 - cancer (question 1), aspirin - PTGS2 protein with an activity (2), SOD1 - Alzheimer and SOD1 - diabetes (3),
    PTGS2 protein - a second process (4), SOD1 - Alzheimer genetics (5). The two links of the base fixture stay (101, 102)."""
    nodes = pd.DataFrame([dict(id=GENE, category="biolink:Gene", name="PTGS2"),
                          dict(id=CANCER, category="biolink:Disease", name="cancer"),
                          dict(id=ASPIRIN, category="biolink:SmallMolecule", name="ASPIRIN"),
                          dict(id=BP2, category="biolink:BiologicalProcess", name="regulation of blood pressure")])
    ot = pd.DataFrame([
        edge(101, GENE, "biolink:gene_associated_with_condition", DIS1, *OT, ot_score="0.55"),
        edge(102, CMP, "biolink:directly_physically_interacts_with", PROT, "ChEMBL 37 activity", "opentargets_layer", "26.09",
             first_year="1991"),
        edge(103, GENE, "biolink:gene_associated_with_condition", CANCER, *OT, ot_score="0.3"),
        edge(104, ASPIRIN, "biolink:directly_physically_interacts_with", PROT, "ChEMBL 37 activity", "opentargets_layer", "26.09",
             activity="5.1", activity_type="IC50", ligand_name="ASPIRIN"),
        edge(105, SOD1, "biolink:gene_associated_with_condition", DIS1, *OT, ot_score="0.4"),
        edge(106, SOD1, "biolink:gene_associated_with_condition", DIS2, *OT, ot_score="0.2"),
        edge(107, PROT, "biolink:actively_involved_in", BP2, "GO annotations", "opentargets_layer", "26.09"),
        edge(108, SOD1, "biolink:genetically_associated_with", DIS1, "Open Targets 26.09 genetics", "opentargets_layer", "26.09",
             study_count="2", mean_rank_score="0.9")])
    cols = pharos_app.KGX + ["ot_score", "first_year", "activity", "activity_type", "ligand_name", "study_count", "mean_rank_score"]
    write(root / "data" / "kg" / "opentargets", "nodes", nodes)
    write(root / "data" / "kg" / "opentargets", "edges", ot.reindex(columns=cols).fillna(""))
    return root


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    return enrich(build(tmp_path_factory.mktemp("repo")))


@pytest.fixture(scope="module")
def g(repo):
    return PharosGraph(root=repo)


@pytest.fixture(scope="module")
def demos(g):
    return {d["id"]: d for d in g.demo()}


def texts(block):
    """Every sentence a compare block can put in front of a user."""
    out = list(block.get("sentences") or [block.get("sentence", "")])
    out += [s.get("sentence", "") for s in block.get("sections") or []]
    out.append((block.get("predicted") or {}).get("sentence", ""))
    alone = block.get("pharos_alone") or {}
    out.append(alone.get("sentence", ""))
    summary = alone.get("summary") or {}
    if "headline" in summary:
        import answer_summary
        out.append(answer_summary.summary_text(summary))
    return [t for t in out if t]


# ------------------------------------------------------------------------------------------------ sides
def test_sides_come_from_the_graph_recipes():
    assert rc.pharos_layers() == {"pharos"}
    assert rc.side_of("pharos") == "pharos" and rc.side_of("opentargets_layer") == "enrichment" and rc.side_of("dto") == "enrichment"
    assert rc.side_of("") is None and rc.side_of(None) is None
    assert rc.support_of(["pharos", "pharos"]) == "pharos_only" and rc.support_of(["enrichment"]) == "enrichment_only"
    assert rc.support_of(["pharos", "enrichment", None]) == "both" and rc.support_of([None]) == "unknown" and rc.support_of([]) == "unknown"
    assert rc.prediction_in_pharos("G0") is True and rc.prediction_in_pharos("G3") is False and rc.prediction_in_pharos("") is None


# ------------------------------------------------------------------------------------------------ the five tested questions
# id -> (row marks in row order as (support, in_pharos_alone), rows left in Pharos alone as numbers of the full answer)
EXPECTED = {
    "pharos_demo_1": ([("both", True), ("pharos_only", True), ("enrichment_only", False)], [1, 2]),
    "pharos_demo_2": ([("pharos_only", True), ("both", False)], [1]),            # aspirin: a Pharos step, an enrichment step
    "pharos_demo_3": ([("both", True), ("enrichment_only", False)], [1]),
    "pharos_demo_4": ([("both", True), ("both", False)], [1]),
    "pharos_demo_5": ([("enrichment_only", False), ("pharos_only", True)], [2]),
}


@pytest.mark.parametrize("demo_id", sorted(EXPECTED))
def test_tested_question_rows_are_marked_and_counted(demos, g, demo_id):
    d = demos[demo_id]
    assert d["passed"], d["note"]
    c = rc.answer_compare(d, g)
    marks, stays = EXPECTED[demo_id]
    assert [(m["support"], m["in_pharos_alone"]) for m in c["marks"]] == marks
    assert [m["row"] for m in c["marks"]] == list(range(1, len(d["rows"]) + 1))
    for key in ("pharos_only", "enrichment_only", "both"):
        assert c[key] == sum(1 for s, _ in marks if s == key)
    assert c["rows"] == len(d["rows"]) == c["pharos_only"] + c["enrichment_only"] + c["both"] and c["unknown"] == 0
    assert c["in_pharos_alone"] == len(stays) and c["missing_from_pharos_alone"] == len(marks) - len(stays) and c["undetermined"] == 0
    assert c["sentence"] == f"1 of {len(marks)} rows exists only because of the enrichment." and c["sentences"][0] == c["sentence"]
    assert [r["row"] for r in c["pharos_alone"]["rows"]] == stays
    assert c["applicable"] is True and c["added_by"] and all(not FORBIDDEN.search(t) for t in texts(c)), texts(c)


def test_a_row_with_a_pharos_step_and_an_enrichment_step_is_missing_from_pharos_alone(demos, g):
    c = rc.answer_compare(demos["pharos_demo_2"], g)                 # gene -> protein (Pharos) -> aspirin (enrichment)
    aspirin = c["marks"][1]
    assert aspirin["support"] == "both" and aspirin["in_pharos_alone"] is False
    assert aspirin["links"] == {"pharos": 1, "enrichment": 1, "unknown": 0} and aspirin["added_by"] == ["ChEMBL 37 activity"]
    assert c["links"] == {"pharos": 2, "enrichment": 1, "unknown": 0}


def test_pharos_alone_rows_lose_the_enrichment_links_and_their_values(demos, g):
    c = rc.answer_compare(demos["pharos_demo_1"], g)
    full = demos["pharos_demo_1"]["rows"][0]                          # Alzheimer: DisGeNET, text mining and Open Targets, merged
    assert full["evidence"] == [E(1), E(3), E(101)] and "Open Targets 26.09 overall direct" in full["source"]
    alone = c["pharos_alone"]["rows"][0]["values"]
    assert alone["evidence"] == [E(1), E(3)] and alone["source"] == ["DisGeNET", "JensenLab Text Mining"]
    assert alone["disease"] == "Alzheimer disease" and alone["knowledge_level"] == full["knowledge_level"]
    assert c["pharos_alone"]["evidence"] == [E(1), E(2), E(3)] and c["pharos_alone"]["columns"] == demos["pharos_demo_1"]["columns"]
    four = rc.answer_compare(demos["pharos_demo_4"], g)["pharos_alone"]["rows"][0]["values"]
    assert four["e1"] == E(5) and four["e2"] == E(6)                 # the ChEMBL 37 link for the same pair is gone, the row stays


def test_bottom_line_is_recomputed_for_pharos_alone_with_the_existing_summary_code(demos, g):
    import answer_summary
    d = demos["pharos_demo_1"]
    full = appmod.graph_summary_safe(d, g)
    c = rc.answer_compare(d, g)
    s = c["pharos_alone"]["summary"]
    assert full["counts"]["rows"] == 3 and full["counts"]["links"] == 5
    assert s["counts"]["rows"] == 2 and s["counts"]["links"] == 3 and s["counts"]["pruned"] == 1      # the text-mining link is Pharos
    assert s["headline"] != full["headline"] and set(s["refs"]["edges"]) == {E(1), E(2), E(3)}
    assert "Open Targets" not in answer_summary.summary_text(s) and "Open Targets" in json.dumps(full["refs"])


@pytest.mark.parametrize("demo_id", sorted(EXPECTED))
def test_pharos_alone_bottom_line_equals_the_summary_code_on_its_rows(demos, g, demo_id):
    # all five tested questions: the bottom line of the Pharos-alone view is exactly what the existing summary function
    # returns for the rows of that view, the links those rows cite and the same entity labels
    import answer_summary
    d = demos[demo_id]
    c = rc.answer_compare(d, g)
    alone = c["pharos_alone"]
    s = alone["summary"]
    assert s["view"] == "pharos_alone" and s["abstained"] is False and "error" not in s
    assert [r["row"] for r in alone["rows"]] == EXPECTED[demo_id][1]
    assert s["counts"]["rows"] == len(alone["rows"]) < len(d["rows"])
    cited = {i for r in alone["rows"] for ids in rc._cells(r["values"], d["columns"]).values() for i in ids}
    assert cited == set(alone["evidence"]) and s["counts"]["links"] == len(cited)
    assert all(rc.side_of(g.edges[i]["layer"]) == "pharos" for i in cited)          # no enrichment link behind the bottom line
    same = dict(d, rows=[r["values"] for r in alone["rows"]], evidence=alone["evidence"])
    for k in ("passed", "note", "why", "ordering", "summary", "compare", "trials_panel"):
        same.pop(k, None)
    if alone["ordering"]:
        same["ordering"] = alone["ordering"]
    facts = answer_summary.edge_facts(g, alone["evidence"])
    direct = answer_summary.pharos_summary(same, facts, {e: g.label(e) for e in d.get("entities") or []})
    assert {k: v for k, v in s.items() if k != "view"} == direct      # exactly the existing function's output on these rows


# ---- finding B: a cut full answer. Six answers per tested question (1 to 3) on a small graph; the full answer stops at six
# rows (its LIMIT), five of them rest on Pharos links, so the Pharos-alone bottom line must not call five the total.
def cut_answer(demo_id):
    """Takes a tested-question id (1 to 3). Returns (the answer cut at 6 rows, the graph): the question's own query with
    LIMIT 6; rows 1 to 5 rest on Pharos links, row 6 on an enrichment link."""
    cypher = re.sub(r"LIMIT \d+", "LIMIT 6", next(q["cypher"] for q in pharos_app.DEMO_QUESTIONS if q["id"] == demo_id))
    lay = lambda k: "pharos" if k < 6 else "opentargets_layer"      # noqa: E731
    es, rows = [], []
    for k in range(1, 7):
        if demo_id == "pharos_demo_1":
            es.append(fe(k, "g", f"d{k}", lay(k), "statistical_association"))
            rows.append(dict(disease=f"d{k}", source=f"source {k}", knowledge_level="statistical_association", evidence=E(k)))
        elif demo_id == "pharos_demo_2":
            es += [fe(50, "g", "p", "pharos"), fe(k, f"c{k}", "p", lay(k))]
            rows.append(dict(compound=f"c{k}", activity=9.0 - k, activity_type="IC50", e1=E(50), e2=E(k)))
        else:
            es += [fe(k, f"g{k}", "MONDO:0004975", "pharos", "statistical_association"),
                   fe(20 + k, f"g{k}", "MONDO:0005148", lay(k), "statistical_association")]
            rows.append(dict(gene=f"g{k}", alzheimer_source=f"source {k}", diabetes_source=f"source {20 + k}",
                             alzheimer_level="statistical_association", diabetes_level="statistical_association",
                             e1=E(k), e2=E(20 + k)))
    fg = FakeGraph(list({e["id"]: e for e in es}.values()))
    return dict(columns=list(rows[0]), rows=rows, cypher=cypher), fg


@pytest.mark.parametrize("demo_id", ["pharos_demo_1", "pharos_demo_2", "pharos_demo_3"])
def test_pharos_alone_headline_of_a_cut_answer_does_not_claim_a_total(demo_id):
    import answer_summary
    res, fg = cut_answer(demo_id)
    c = rc.answer_compare(res, fg)
    assert c["in_pharos_alone"] == 5 and c["missing_from_pharos_alone"] == 1
    s = c["pharos_alone"]["summary"]
    assert "5 of the 6 rows shown" in s["headline"] and " in all" not in answer_summary.summary_text(s), s["headline"]
    assert any("full answer stops at 6 rows" in x for x in s["caveats"])
    # the same rows when the full answer was NOT cut (a higher LIMIT): the total is a real total and stays
    whole = rc.answer_compare(dict(res, cypher=res["cypher"].replace("LIMIT 6", "LIMIT 60")), fg)["pharos_alone"]["summary"]
    assert " in all" in whole["headline"] and "rows shown" not in whole["headline"], whole["headline"]
    assert whole["headline"].replace(re.search(r"; 5 [a-z ]+ in all", whole["headline"]).group(0), "; 5 of the 6 rows shown") == s["headline"]


def test_cut_headline_rewords_only_the_total():
    h = "PTGS2 is associated with A, B and C (CTD, DrugCentral Indication); 50 diseases in all, 12 text-mined only."
    assert rc.cut_headline(h, 50, 50) == ("PTGS2 is associated with A, B and C (CTD, DrugCentral Indication); "
                                          "50 of the 50 rows shown, 12 text-mined only.")
    assert rc.cut_headline("Top genes: A, B and C; 1,234 genes in all.", 1234, 2000) == "Top genes: A, B and C; 1,234 of the 2,000 rows shown."
    assert rc.cut_headline("Gene: A (CTD).", 1, 1) == "Gene: A (CTD)." and rc.cut_headline(None, 0, 1) == ""
    capped = "Top genes: A, B and C; 20 genes shown, and there may be more."
    assert rc.cut_headline(capped, 20, 20) == capped


def test_a_failing_summary_gives_a_fixed_sentence_not_the_error_text(demos, g, monkeypatch):
    import answer_summary

    def fail(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: '/Users/you/projects/TrialMindKG/data/kg/pharos/edges.parquet'")
    monkeypatch.setattr(answer_summary, "pharos_summary", fail)
    c = rc.answer_compare(demos["pharos_demo_1"], g)
    s = c["pharos_alone"]["summary"]
    assert s == dict(error=rc.SUMMARY_NOT_AVAILABLE) == dict(error="The bottom line for Pharos alone is not available for this answer.")
    text = json.dumps(c)
    assert "/Users/" not in text and "Errno" not in text and "edges.parquet" not in text and not FORBIDDEN.search(s["error"])
    assert c["rows"] == 3 and [r["row"] for r in c["pharos_alone"]["rows"]] == [1, 2]      # the comparison itself is unharmed


def test_an_answer_above_the_row_or_link_limit_says_so(monkeypatch):
    fg = FakeGraph([fe(k, "g", f"d{k}", "pharos" if k % 2 else "opentargets_layer") for k in range(1, 7)])
    res = dict(columns=["disease", "evidence"], cypher="", rows=[dict(disease=f"d{k}", evidence=E(k)) for k in range(1, 7)])
    whole = rc.answer_compare(res, fg)
    assert whole["limits"] == dict(rows_total=6, rows_compared=6, links_total=6, links_looked_up=6)
    assert whole["sentences"] == ["3 of 6 rows exist only because of the enrichment."]
    assert rc.limit_sentences(6, 6, 6, 6) == []
    monkeypatch.setattr(rc, "MAX_ROWS", 4)                           # more rows than are compared
    c = rc.answer_compare(res, fg)
    assert c["rows"] == 4 and len(c["marks"]) == 4 and c["limits"] == dict(rows_total=6, rows_compared=4, links_total=4, links_looked_up=4)
    assert c["sentence"] == "2 of 4 rows exist only because of the enrichment."
    assert c["sentences"][-1] == "This answer has 6 rows; the comparison covers the first 4 only."
    monkeypatch.setattr(rc, "MAX_ROWS", 2000)
    monkeypatch.setattr(rc, "MAX_LINKS", 3)                          # more cited links than are looked up
    c = rc.answer_compare(res, fg)
    assert c["limits"] == dict(rows_total=6, rows_compared=6, links_total=6, links_looked_up=3)
    assert c["undetermined"] == 3 and c["links"] == dict(pharos=2, enrichment=1, unknown=3)
    assert c["sentences"] == ["1 of 3 rows exists only because of the enrichment.",
                              "3 rows could not be decided, because a link they cite was not found in the graph or was not looked up.",
                              "The rows compared cite 6 links; the first 3 were looked up, and a row that rests on one of the "
                              "others is counted as not decided."]
    assert all(not FORBIDDEN.search(t) for t in c["sentences"])


def test_graph_payload_marks_every_node_and_edge(demos, g):
    c = rc.answer_compare(demos["pharos_demo_1"], g)
    gr = c["graph"]
    edges = {e["id"]: e for e in gr["edges"]}
    assert set(edges) == {E(1), E(2), E(3), E(101), E(103)} and gr["rows_drawn"] == gr["rows_total"] == 3
    assert {k: (e["side"], e["support"], e["in_pharos_alone"]) for k, e in edges.items()} == {
        E(1): ("pharos", "both", True), E(3): ("pharos", "both", True), E(101): ("enrichment", "both", False),
        E(2): ("pharos", "pharos_only", True), E(103): ("enrichment", "enrichment_only", False)}
    assert edges[E(101)]["rows"] == [1] and edges[E(103)]["rows"] == [3] and edges[E(3)]["word"] == "pruned"
    for e in gr["edges"]:                                             # drawable by the page's card-picture code
        assert {"id", "source", "target", "predicate", "evidence_type", "predicted", "word"} <= set(e)
    nodes = {n["label"]: n for n in gr["nodes"]}
    assert nodes["cancer"]["in_pharos_alone"] is False and nodes["cancer"]["support"] == "enrichment_only"
    assert nodes["PTGS2"]["in_pharos_alone"] is True and nodes["PTGS2"]["support"] == "both"
    assert nodes["type 2 diabetes mellitus"]["support"] == "pharos_only" and all({"id", "label", "category"} <= set(n) for n in gr["nodes"])
    hidden = {n["id"] for n in gr["nodes"] if n["in_pharos_alone"] is False}
    for e in gr["edges"]:                                             # hiding the marked nodes and edges leaves no dangling line
        if e["in_pharos_alone"]:
            assert e["source"] not in hidden and e["target"] not in hidden


def test_graph_payload_draws_whole_rows_up_to_the_node_cap(demos, g):
    d = demos["pharos_demo_1"]
    cells = [rc._cells(r, d["columns"]) for r in d["rows"]]
    edges = rc._lookup(g, [i for c in cells for ids in c.values() for i in ids])
    small = rc.answer_graph(cells, edges, g, max_nodes=2)             # row 1 has 2 nodes; row 2 would need a third
    assert small["rows_drawn"] == 1 and small["rows_total"] == 3 and len(small["nodes"]) == 2
    assert {e["id"] for e in small["edges"]} == {E(1), E(3), E(101)}


# ------------------------------------------------------------------------------------------------ steps that must meet
class FakeGraph:
    """A graph with only what the compare code reads: edges by id, nodes, label, verdict, same_pair."""

    def __init__(self, edges, verdicts=None):
        self.edges = {e["id"]: e for e in edges}
        self.nodes = {n: dict(id=n, name=n, category="biolink:NamedThing") for e in edges for n in (e["subject"], e["object"])}
        self._verdicts = verdicts or {}

    def label(self, n):
        return n

    def verdict(self, eid):
        return self._verdicts.get(eid)

    def same_pair(self, s, o, exclude=None):
        return [e for e in self.edges.values() if e["subject"] == s and e["object"] == o and e["id"] != exclude]


def fe(i, s, o, layer, level="knowledge_assertion", source=None, predicate="biolink:related_to"):
    return dict(id=E(i), subject=s, predicate=predicate, object=o, layer=layer, knowledge_level=level,
                source=source or f"source {i}")


def test_pharos_links_must_meet_at_the_same_node():
    # process X is reached through protein p1 (Pharos step 1, enrichment step 2) and p2 (enrichment step 1, Pharos step 2):
    # every step has a Pharos link, but no path is Pharos all the way.
    fg = FakeGraph([fe(1, "g", "p1", "pharos"), fe(2, "g", "p2", "chembl37"), fe(3, "p1", "X", "go_hierarchy"), fe(4, "p2", "X", "pharos")])
    cypher = ("MATCH (g:Gene {name:'A'})-[r1:has_gene_product]->(p:Protein)-[r2:actively_involved_in]->(b:BiologicalProcess) "
              "RETURN b.name AS process, r1.id AS e1, r2.id AS e2 LIMIT 50")
    res = dict(columns=["process", "e1", "e2"], rows=[dict(process="X", e1=[E(1), E(2)], e2=[E(3), E(4)])], cypher=cypher)
    c = rc.answer_compare(res, fg)
    assert c["marks"][0]["support"] == "both" and c["marks"][0]["in_pharos_alone"] is False
    assert c["missing_from_pharos_alone"] == 1 and c["pharos_alone"]["rows"] == []
    assert c["sentence"] == "The 1 row exists only because of the enrichment; Pharos alone does not return it."
    s = c["pharos_alone"]["summary"]
    assert s["abstained"] is True and s["headline"] == "Pharos alone returns no rows for this question."
    assert "none of the 1 row of the full answer rests on Pharos links alone" in s["points"][0]["text"]
    fg2 = FakeGraph(list(fg.edges.values()) + [fe(5, "p1", "X", "pharos")])       # now g -> p1 -> X is Pharos all the way
    res2 = dict(res, rows=[dict(process="X", e1=[E(1), E(2)], e2=[E(3), E(4), E(5)])])
    c2 = rc.answer_compare(res2, fg2)
    assert c2["marks"][0]["in_pharos_alone"] is True
    assert c2["pharos_alone"]["rows"][0]["values"] == dict(process="X", e1=E(1), e2=E(5))    # p2 -> X does not meet g -> p1
    # without a readable query the simpler rule applies: a Pharos link per step is enough
    assert rc.answer_compare(dict(res, cypher=""), fg)["marks"][0]["in_pharos_alone"] is True


def test_question_steps_reads_the_query():
    steps, link_cols = rc.question_steps(pharos_app.DEMO_QUESTIONS[2]["cypher"], ["gene", "alzheimer_source", "e1", "e2"])
    assert steps == [("e1", "g", "d1", "out"), ("e2", "g", "d2", "out")]
    assert link_cols["alzheimer_source"] == ("e1", "source") and link_cols["diabetes_level"] == ("e2", "knowledge_level")
    assert rc.question_steps("not a query", ["a"]) == (None, {}) and rc.question_steps(None, []) == (None, {})
    no_id = "MATCH (g:Gene {name:'A'})-[r:related_to]->(d:Disease) RETURN d.name AS disease LIMIT 5"
    assert rc.question_steps(no_id, ["disease"]) == (None, {})


def test_pharos_alone_rows_are_ordered_again_by_their_pharos_evidence():
    # row 1 leads the full answer through a curated enrichment link; its only Pharos link is text-mined, so in Pharos alone
    # the statistical row 2 comes first.
    fg = FakeGraph([fe(1, "g", "d1", "pharos", "text_co_occurrence"), fe(2, "g", "d1", "opentargets_layer"),
                    fe(3, "g", "d2", "pharos", "statistical_association")])
    cypher = ("MATCH (g:Gene {name:'A'})-[r:gene_associated_with_condition]->(d:Disease) "
              "RETURN d.name AS disease, r.source AS source, r.id AS evidence LIMIT 2")
    res = dict(columns=["disease", "source", "evidence"], cypher=cypher, entities=["g"],
               rows=[dict(disease="d1", source=["source 2", "source 1"], evidence=[E(2), E(1)]),
                     dict(disease="d2", source="source 3", evidence=E(3))],
               ordering=dict(by="evidence", n_total=5, n_shown=2, n_more=3, n_more_text_mined=1, collapsed=1, full=True))
    c = rc.answer_compare(res, fg)
    alone = c["pharos_alone"]
    assert [r["row"] for r in alone["rows"]] == [2, 1] and alone["rows"][1]["values"] == dict(disease="d1", source="source 1", evidence=E(1))
    assert alone["ordering"]["by"] == "evidence" and alone["ordering"]["n_more"] == 0 and alone["ordering"]["n_total"] == 2
    assert c["sentences"] == ["All 2 rows are already in Pharos alone.",
                              "The enrichment adds supporting links to 1 row that Pharos alone already has."]
    cut = [x for x in alone["summary"]["caveats"] if "full answer stops at 2 rows" in x]
    assert len(cut) == 1 and "Pharos alone may hold further rows" in cut[0]       # the view is a filter, not a second run
    assert not any("Showing the top" in x for x in alone["summary"]["caveats"])
    assert rc.answer_compare(dict(res, ordering=None, cypher=cypher.replace("LIMIT 2", "LIMIT 9")), fg)["pharos_alone"]["ordering"] is None


def test_links_that_are_not_found_are_unknown_never_guessed():
    fg = FakeGraph([fe(1, "g", "d1", "pharos")])
    res = dict(columns=["disease", "evidence"], cypher="", rows=[dict(disease="d1", evidence=E(1)),
                                                               dict(disease="d2", evidence=E(99)),
                                                               dict(disease="d3", evidence=[E(1), E(98)])])
    c = rc.answer_compare(res, fg)
    assert [(m["support"], m["in_pharos_alone"]) for m in c["marks"]] == [("pharos_only", True), ("unknown", None), ("pharos_only", True)]
    assert c["unknown"] == 1 and c["undetermined"] == 1 and c["missing_from_pharos_alone"] == 0 and c["links"]["unknown"] == 2
    assert c["sentences"][-1] == "1 row could not be decided, because a link it cites was not found in the graph."
    none = rc.answer_compare(dict(res, rows=res["rows"][1:2]), fg)
    assert none["sentence"].startswith("The links behind this row could not be looked up")
    no_layer = FakeGraph([dict(fe(1, "g", "d1", "pharos"), layer="")])              # a graph without layers (the trial graph)
    assert rc.answer_compare(dict(res, rows=res["rows"][:1]), no_layer)["marks"][0]["support"] == "unknown"
    bare = rc.answer_compare(dict(columns=["name"], rows=[dict(name="x")]), fg)     # a row that cites no link at all
    assert bare["marks"][0] == dict(row=1, support="unknown", in_pharos_alone=None, links=dict(pharos=0, enrichment=0, unknown=0),
                                    added_by=[], pharos_sources=[]) and bare["undetermined"] == 1 and bare["pharos_alone"]["rows"] == []
    kept_order = dict(columns=["disease", "evidence"], rows=[dict(disease="d1", evidence=E(1))], ordering=dict(by="evidence", n_more=0))
    assert rc.pharos_alone_view(kept_order, [(1, kept_order["rows"][0], [{"evidence": E(1)}])], fg.edges, None)["ordering"] is None


def test_payloads_that_are_not_graph_answers():
    trial = rc.answer_compare(dict(question="q", sections=[dict(role="r", claims=[])]), None)
    assert trial["applicable"] is False and "trial layer" in trial["sentence"]
    empty = rc.answer_compare(dict(question="q", columns=[], rows=[], problems=["no entity"], abstained=True), None)
    assert empty["applicable"] is True and empty["rows"] == 0 and empty["sentence"] == "There are no rows to compare."
    assert empty["pharos_alone"]["rows"] == [] and empty["pharos_alone"]["summary"]["abstained"] is True
    assert empty["pharos_alone"]["summary"]["headline"] != "Pharos alone returns no rows for this question."   # nothing was lost
    assert rc.answer_compare(None, None)["rows"] == 0 and rc.answer_compare(dict(columns="x", rows=[1, "a"]), None)["rows"] == 0


def test_compare_sentence_wording_follows_the_counts():
    s = rc.compare_sentence
    assert s(50, 12) == ["12 of 50 rows exist only because of the enrichment."]
    assert s(50, 1)[0] == "1 of 50 rows exists only because of the enrichment."
    assert s(3, 0) == ["All 3 rows rest on Pharos links alone; the enrichment adds nothing to this answer."]
    assert s(1, 0) == ["The 1 row rests on Pharos links alone; the enrichment adds nothing to this answer."]
    assert s(3, 0, gains=2, enrichment_links=4) == ["All 3 rows are already in Pharos alone.",
                                                    "The enrichment adds supporting links to 2 rows that Pharos alone already has."]
    assert s(4, 4)[0] == "All 4 rows exist only because of the enrichment; Pharos alone returns none of them."
    assert s(5, 2, undetermined=1)[0] == "2 of 4 rows exist only because of the enrichment."
    assert s(0, 0) == ["There are no rows to compare."]


# ------------------------------------------------------------------------------------------------ the entity card
def test_entity_card_sections_rows_and_picture_are_marked(g):
    card = pharos_app.split_predictions(g.card(GENE))
    c = rc.card_compare(card, g)
    assert c["rows"] == 7 and (c["pharos_only"], c["enrichment_only"], c["both"], c["unknown"]) == (2, 1, 4, 0)
    assert c["hidden_in_pharos_alone"] == 2
    assert c["sentences"] == ["1 of the 7 links shown exists only because of the enrichment.",
                              "1 more link shown comes from the enrichment and restates a pair Pharos already holds."]
    sec = {s["kind"]: s for s in c["sections"]}
    assert [s["kind"] for s in c["sections"]] == [k["kind"] for k in card["known"]]
    cur = sec["curated"]
    assert (cur["rows"], cur["pharos_only"], cur["enrichment_only"], cur["both"], cur["support"]) == (4, 1, 1, 2, "both")
    assert cur["marks"][E(103)] == dict(side="enrichment", support="enrichment_only", in_pharos_alone=False)     # PTGS2 - cancer
    assert cur["marks"][E(101)] == dict(side="enrichment", support="both", in_pharos_alone=False)               # restates e1
    assert cur["marks"][E(1)] == dict(side="pharos", support="both", in_pharos_alone=True)
    assert cur["marks"][E(2)] == dict(side="pharos", support="pharos_only", in_pharos_alone=True)
    assert cur["sentence"] == "1 of the 4 links shown exists only because of the enrichment."
    assert cur["pharos_alone"] == dict(rows=2, kept=2, pruned=0) and cur["hidden_in_pharos_alone"] == 2
    tm = sec["text mining"]
    assert tm["support"] == "pharos_only" and tm["pharos_alone"] == dict(rows=1, kept=0, pruned=1)
    assert tm["sentence"] == "The 1 link shown is a Pharos link; the enrichment adds none here."
    assert set(m for s in c["sections"] for m in s["marks"]) == {r["id"] for k in card["known"] for r in k["rows"]}
    alone = c["pharos_alone"]
    assert alone["summary"] == dict(known_shown=5, kinds=4, kept=4, pruned=1, predicted=0)
    assert alone["sentence"] == ("In Pharos alone this card shows 5 of the 7 links shown here, in 4 kinds of evidence "
                                 "(4 kept, 1 pruned by the pruning check).")
    pic = c["picture"]
    assert [e["id"] for e in pic["edges"]] == [e["id"] for e in card["picture"]["edges"]]
    by = {e["id"]: e for e in pic["edges"]}
    assert (by[E(103)]["side"], by[E(103)]["support"], by[E(103)]["in_pharos_alone"]) == ("enrichment", "enrichment_only", False)
    assert (by[E(101)]["side"], by[E(101)]["support"]) == ("enrichment", "both") and by[E(2)]["support"] == "pharos_only"
    nodes = {n["id"]: n for n in pic["nodes"]}
    assert nodes[CANCER]["in_pharos_alone"] is False and nodes[GENE]["in_pharos_alone"] is True and nodes[DIS1]["in_pharos_alone"] is True
    assert all(not FORBIDDEN.search(t) for t in texts(c)), texts(c)


def test_card_pair_support_reads_the_whole_graph_not_only_the_rows_shown(g):
    card = pharos_app.split_predictions(g.card(GENE, per_kind=1))        # one row per kind: the Open Targets rows are cut
    shown = {r["id"] for k in card["known"] for r in k["rows"]}
    assert E(101) not in shown and E(1) in shown
    with_graph = rc.card_compare(card, g)
    marks = {i: m for s in with_graph["sections"] for i, m in s["marks"].items()}
    assert marks[E(1)]["support"] == "both"                              # the graph still holds the Open Targets link for the pair
    alone = rc.card_compare(card)                                         # no graph: only the rows on the card are known
    assert {i: m for s in alone["sections"] for i, m in s["marks"].items()}[E(1)]["support"] == "pharos_only"


def test_card_predicted_links_and_empty_card():
    card = dict(about=dict(id="n", label="N"), known=[], predicted=[dict(subject="n", object="m", graph="G3")],
                picture=dict(nodes=[dict(id="n"), dict(id="m")],
                             edges=[dict(id="pred:n|m", source="n", target="m", predicted=True)]))
    c = rc.card_compare(card)
    assert c["rows"] == 0 and c["sentence"] == "There are no links to compare."
    assert c["predicted"]["in_pharos_alone"] is False and "agent-built graph" in c["predicted"]["sentence"]
    edge_ = c["picture"]["edges"][0]
    assert (edge_["side"], edge_["in_pharos_alone"]) == ("predicted", False)
    assert {n["id"]: n["in_pharos_alone"] for n in c["picture"]["nodes"]} == {"n": True, "m": False}
    assert c["pharos_alone"]["summary"]["predicted"] == 0 and not FORBIDDEN.search(" ".join(texts(c)))
    on_pharos = rc.card_compare(dict(card, predicted=[dict(subject="n", object="m", graph="G0")]))
    assert on_pharos["predicted"]["in_pharos_alone"] is True and on_pharos["pharos_alone"]["summary"]["predicted"] == 1
    unknown = rc.card_compare(dict(card, predicted=[dict(subject="n", object="m")]))
    assert unknown["predicted"]["in_pharos_alone"] is None and "not recorded" in unknown["predicted"]["sentence"]
    assert rc.card_compare(None)["rows"] == 0 and rc.card_compare({})["predicted"]["sentence"] == ""


# ------------------------------------------------------------------------------------------------ the endpoints
@pytest.fixture
def client(g):
    old = dict(appmod._pharos)
    appmod._pharos.clear()
    appmod._pharos.update(g=g, index=None)                 # the live fixture graph answers
    appmod._state.update(kg=None, nim=NIM(backend="offline"))
    yield TestClient(appmod.app)
    appmod._pharos.clear()
    appmod._pharos.update(old)


@pytest.mark.parametrize("demo_id", sorted(EXPECTED))
def test_demo_endpoint_serves_the_compare_block(client, demos, g, demo_id):
    r = client.get(f"/api/pharos/compare/demo/{demo_id}")
    assert r.status_code == 200
    out = r.json()
    assert out["id"] == demo_id and out["question"] == demos[demo_id]["question"]
    c = out["compare"]
    assert [(m["support"], m["in_pharos_alone"]) for m in c["marks"]] == EXPECTED[demo_id][0]
    assert c == json.loads(json.dumps(appmod.nan_safe(rc.answer_compare(demos[demo_id], g))))
    assert {"pharos_only", "enrichment_only", "both", "sentence", "graph", "pharos_alone"} <= set(c)


def test_answer_and_card_endpoints(client, demos, g):
    assert client.get("/api/pharos/compare/demo/nope").status_code == 404
    answer = client.get("/api/pharos/demo/pharos_demo_3").json()             # the payload the page holds, summary included
    c = client.post("/api/pharos/compare/answer", json={"result": answer}).json()
    assert c["sentence"] == "1 of 2 rows exists only because of the enrichment." and c["pharos_alone"]["summary"]["counts"]["rows"] == 1
    assert "summary" not in c["pharos_alone"] or "compare" not in json.dumps(c["pharos_alone"]["summary"]["plan"])
    asked = client.post("/api/pharos/ask", json={"question": "Which diseases is PTGS2 associated with?"}).json()
    ca = client.post("/api/pharos/compare/answer", json={"result": asked}).json()
    assert ca["applicable"] is True and ca["rows"] == len(asked["rows"]) == len(ca["marks"])
    assert client.post("/api/pharos/compare/answer", json={}).status_code == 422
    card = client.get(f"/api/pharos/compare/card/{GENE}").json()
    assert card["id"] == GENE and card["label"] == "PTGS2"
    assert card["compare"]["sentence"] == "1 of the 7 links shown exists only because of the enrichment."
    assert client.get("/api/pharos/compare/card/nope").status_code == 404


def test_stored_answers_of_the_repository_do_not_break_the_endpoint(client):
    folder = Path(__file__).resolve().parents[1] / "outputs" / "examples"
    seen = set()
    for f in sorted(folder.glob("*.json")):
        answer = json.loads(f.read_text())["answer"]
        r = client.post("/api/pharos/compare/answer", json={"result": answer})
        assert r.status_code == 200, f.name
        c = r.json()
        seen.add(c["applicable"])
        if c["applicable"]:
            assert c["rows"] == len(c["marks"]) and c["rows"] == c["pharos_only"] + c["enrichment_only"] + c["both"] + c["unknown"]
        assert not FORBIDDEN.search(" ".join(c.get("sentences") or [c["sentence"]])), f.name
    assert seen == {True, False}                                       # graph answers and trial reports are both stored


def test_compare_from_the_card_index_equals_the_live_graph(repo, g, demos, monkeypatch):
    stats = build_index(repo, chunk=3, log=lambda *_: None)
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(repo))
    monkeypatch.delenv("TMKG_PHAROS_INDEX", raising=False)
    old = dict(appmod._pharos)
    appmod._pharos.clear()
    appmod._state.update(kg=None, nim=NIM(backend="offline"))

    def boom(*a, **k):
        raise AssertionError("the live graph must not be loaded when a card index exists")
    monkeypatch.setattr(appmod, "pharos", boom)
    try:
        c = TestClient(appmod.app, raise_server_exceptions=True)
        for demo_id in EXPECTED:
            got = c.get(f"/api/pharos/compare/demo/{demo_id}").json()["compare"]
            assert got == json.loads(json.dumps(appmod.nan_safe(rc.answer_compare(demos[demo_id], g)))), demo_id
        live = rc.card_compare(pharos_app.split_predictions(g.card(GENE, n_pred=pharos_app.PRED_POOL)), g)
        got = c.get(f"/api/pharos/compare/card/{GENE}").json()["compare"]
        for key in ("rows", "pharos_only", "enrichment_only", "both", "sentences", "sections", "pharos_alone", "predicted"):
            assert got[key] == json.loads(json.dumps(live[key])), key
        assert CardIndex(stats["path"]).card(GENE) is not None
    finally:
        appmod._pharos.clear()
        appmod._pharos.update(old)


def test_the_app_wires_the_router_in_one_line():
    src = (Path(__file__).resolve().parents[1] / "pipelines" / "app.py").read_text(encoding="utf-8")
    lines = [l for l in src.splitlines() if "routes_compare" in l]
    assert len(lines) == 1 and "include_router" in lines[0]
    paths = set(appmod.app.openapi()["paths"])
    assert {"/api/pharos/compare/answer", "/api/pharos/compare/demo/{demo_id}", "/api/pharos/compare/card/{node_id}"} <= paths
    assert not any(p in appmod.ASK_PATHS for p in paths if "compare" in p)        # reading a comparison never spends a question
