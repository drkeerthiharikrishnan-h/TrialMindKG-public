"""The deterministic bottom line (pipelines/answer_summary.py): same input -> same output, nothing named that is not in the
input, an honest empty answer, pruned-edge counting, trial reports, and the additive 'summary' key on the endpoints."""
import copy
import json
import re
import sys

import pytest

sys.path.insert(0, "pipelines")
import answer_summary as S  # noqa: E402

E = [f"e{i:016x}" for i in range(1, 9)]


def fact(eid, source, level, verdict="kept", conf=0.7, kind=None, subj="GENE1", obj="disease A"):
    return dict(id=eid, subject="NCBIGene:1", subject_label=subj, predicate="biolink:gene_associated_with_condition",
                phrase="is associated with", object="MONDO:1", object_label=obj, source=source, source_record="r",
                layer="pharos", source_version="v", knowledge_level=level, strength=S.strength_word(level, kind), kind=kind,
                activity=None, activity_type=None, evidence_type=None, verdict=verdict, confidence=conf,
                reasons="plausibility=0.5;sources=2", reasons_words=S.reasons_words("plausibility=0.5;sources=2"))


def graph_answer():
    rows = [dict(disease="disease A", source="DisGeNET", evidence=E[0]), dict(disease="disease A", source="CTD", evidence=E[1]),
            dict(disease="disease B", source="TIN-X", evidence=E[2]), dict(disease="disease C", source="TIN-X", evidence=E[3]),
            dict(disease="disease D", source="CTD", evidence=E[4])]
    facts = {E[0]: fact(E[0], "DisGeNET", "knowledge_assertion"), E[1]: fact(E[1], "CTD", "knowledge_assertion"),
             E[2]: fact(E[2], "TIN-X", "text_co_occurrence", verdict="pruned", conf=0.2, obj="disease B"),
             E[3]: fact(E[3], "TIN-X", "text_co_occurrence", conf=0.3, obj="disease C"),
             E[4]: fact(E[4], "CTD", "statistical_association", obj="disease D")}
    res = dict(question="Which diseases is GENE1 associated with?", entities=["NCBIGene:1"], columns=["disease", "source", "evidence"],
               rows=rows, evidence=E[:5], problems=[], label=dict(kind="offline", offline=True, text="Offline stand-in: fixed rules."),
               cypher="MATCH (g:Gene {name:'GENE1'})-[r:gene_associated_with_condition]->(d:Disease) RETURN d.name AS disease, "
                      "r.source AS source, r.id AS evidence ORDER BY disease LIMIT 5")
    return res, facts


def test_deterministic_and_input_untouched():
    res, facts = graph_answer()
    before = copy.deepcopy((res, facts))
    a = S.pharos_summary(res, facts, {"NCBIGene:1": "GENE1"})
    b = S.pharos_summary(res, facts, {"NCBIGene:1": "GENE1"})
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert (res, facts) == before


def test_names_only_what_is_in_the_input():
    res, facts = graph_answer()
    s = S.pharos_summary(res, facts, {"NCBIGene:1": "GENE1"})
    text = S.summary_text(s)
    given_sources = {f["source"] for f in facts.values()}
    for src in ["DisGeNET", "CTD", "TIN-X", "ChEMBL", "Open Targets", "UniProt", "Monarch", "TIGA", "eRAM", "DrugCentral"]:
        if src in text:
            assert src in given_sources, f"{src} named but not in the input"
    names = set(re.findall(r"disease [A-Z]", text))
    assert names and names <= {r["disease"] for r in res["rows"]}
    for n in re.findall(r"rows? (\d+)", text):
        assert 1 <= int(n) <= len(res["rows"])
    for e in re.findall(r"e[0-9a-f]{16}", text):
        assert e in res["evidence"]
    assert s["headline"] == ("GENE1 is associated with disease A and disease D (DisGeNET, CTD); "
                             "4 diseases shown, and there may be more, 1 text-mined only.")


def test_pruned_edges_counted_and_flagged():
    res, facts = graph_answer()
    s = S.pharos_summary(res, facts)
    c = s["counts"]
    assert (c["kept"], c["pruned"], c["unscored"], c["links"], c["links_looked_up"]) == (4, 1, 0, 5, 5)
    assert c["rows_with_pruned"] == 1 and c["rows_text_only"] == 1 and c["rows_strong"] == 2
    assert c["rows_statistical"] == 1 and c["rows_pruned_only"] == 1          # row 3: its only link was pruned
    assert [f["edge"] for f in s["evidence_analysis"]["flagged"]] == [E[2]]
    assert any(x.startswith("Row 3 has no support left after pruning") for x in s["caveats"])
    assert s["refs"]["rows"][3] == [E[2]] and s["refs"]["row_strength"][3] == "pruned" and s["refs"]["row_strength"][4] == "text-mined"
    assert s["refs"]["row_class"] == {1: "curated", 2: "curated", 3: "pruned", 4: "text-mined", 5: "statistical"}
    agree = s["evidence_analysis"]["agreement"]
    assert agree == [dict(value="disease A", sources=["DisGeNET", "CTD"], rows=[1, 2])]


def test_confidence_levels_follow_the_evidence():
    res, facts = graph_answer()
    assert S.pharos_summary(res, facts)["confidence"]["level"] == "moderate"
    strong = {k: dict(v, knowledge_level="knowledge_assertion", strength="curated", verdict="kept") for k, v in facts.items()}
    assert S.pharos_summary(res, strong)["confidence"]["level"] == "high"
    weak = {k: dict(v, knowledge_level="text_co_occurrence", strength="text-mined") for k, v in facts.items()}
    s = S.pharos_summary(res, weak)
    assert s["confidence"]["level"] == "low" and any("text-mined" in x for x in s["caveats"])
    one = dict(res, rows=res["rows"][:1], evidence=[E[0]])
    assert any(x.startswith("Single source: every row cites DisGeNET") for x in S.pharos_summary(one, facts)["caveats"])


def test_limit_reached_is_a_caveat():
    res, facts = graph_answer()
    s = S.pharos_summary(res, facts)
    assert "The query stops at 5 rows; there may be more answers than shown." in s["caveats"]
    assert "LIMIT" not in S.summary_text(s)                       # no query keyword in what a reader sees


def test_empty_answer_says_so_and_why():
    res = dict(question="q", entities=["NCBIGene:5743"], columns=[], rows=[], evidence=[], problems=["no query returned"], cypher="",
               label=dict(kind="offline", text="Offline stand-in: fixed rules."))
    s = S.pharos_summary(res, {}, {"NCBIGene:5743": "PTGS2"})
    assert s["headline"] == "The graph returned no rows for this question."
    assert s["abstained"] and s["confidence"]["level"] == "low"
    assert s["points"][0]["text"] == "Here is why: no query returned."
    assert "PTGS2" in s["points"][1]["text"] and s["counts"]["rows"] == 0
    s2 = S.pharos_summary(dict(res, entities=[], problems=[]), {})
    assert "matched nothing" in s2["points"][0]["text"] and "No entity" in s2["points"][1]["text"]


def test_an_unranked_listing_leads_with_the_best_supported_answers():
    res, facts = graph_answer()                                   # ORDER BY disease = alphabetical, not a ranking
    s = S.pharos_summary(res, facts)
    assert s["headline"].startswith("GENE1 is associated with disease A and disease D")
    assert s["headline_refs"] == dict(rows=[1, 5], edges=[E[0], E[4]])
    assert "disease B" not in s["headline"] and "disease C" not in s["headline"]   # the text-mined rows are not the lead
    ranked = dict(res, cypher=res["cypher"].replace("ORDER BY disease", "ORDER BY activity DESC"))
    s = S.pharos_summary(ranked, facts)
    assert s["headline"].startswith("GENE1 is associated with disease A and disease D") and s["headline_refs"]["rows"] == [1, 5]


def test_rows_without_lookups_use_their_own_level_columns():
    res, _ = graph_answer()
    res = dict(res, columns=["disease", "source", "knowledge_level", "evidence"],
               rows=[dict(r, knowledge_level="text_co_occurrence") for r in res["rows"]])
    s = S.pharos_summary(res, {})
    assert s["counts"]["rows_text_only"] == 5 and s["confidence"]["level"] == "low" and s["counts"]["links_looked_up"] == 0


def test_label_column_skips_identifier_columns():
    rows = [dict(target="CHEMBL:CHEMBL239", target_name="PPAR alpha", e1=E[0])]
    assert S._label_column(["target", "target_name", "e1"], rows) == "target_name"


def test_words_helpers():
    assert S.strength_word("knowledge_assertion", "binding or activity") == "experimental"
    assert S.strength_word("knowledge_assertion", "curated") == "curated"
    assert S.strength_word("text_co_occurrence") == "text-mined" and S.strength_word(None) is None
    assert S.reasons_words("plausibility=0.527;sources=3;reliability=0.5;source_score=0.774") == \
        "plausibility 0.53; 3 sources for the pair; source reliability 0.50; the source's own score 0.77"
    c = ("MATCH (g:Gene {name:'PTGS2'})-[r:gene_associated_with_condition]->(d:Disease) RETURN d.name AS disease, "
         "r.source AS source, r.id AS evidence ORDER BY disease LIMIT 50")
    assert S.cypher_words(c) == ("Find gene named PTGS2 -is associated with-> disease. Return disease, source, evidence, "
                                 "ordered by disease; at most 50 rows.")
    assert S.cypher_words("") == ""


class FakeGraph:
    def __init__(self):
        self.edges = {E[0]: dict(id=E[0], subject="A", predicate="biolink:directly_physically_interacts_with", object="B",
                                 source="Pharos ligand activity", knowledge_level="knowledge_assertion", layer="pharos",
                                 activity="9.1", activity_type="IC50")}
        self.nodes = {"A": dict(name="cmpd"), "B": dict(name="PTGS2 protein")}

    def verdict(self, eid):
        return dict(verdict="prune", confidence="0.31", reasons="plausibility=0.1;sources=1")

    def label(self, nid):
        return self.nodes[nid]["name"]


def test_edge_facts_reads_the_graph_and_skips_unknown_ids():
    f = S.edge_facts(FakeGraph(), [E[0], E[1], E[0]])
    assert list(f) == [E[0]]
    x = f[E[0]]
    assert (x["source"], x["strength"], x["verdict"], x["confidence"], x["subject_label"], x["activity"]) == \
        ("Pharos ligand activity", "experimental", "pruned", 0.31, "cmpd", "9.1")


def report(claims_per_section=(2, 1, 0, 1), rejected=1):
    secs = []
    titles = ["Failure Diagnosis", "Candidate Responder Population", "Related Indications", "Evidence-Supported Redesign Options"]
    roles = ["failure_diagnostician", "population_stratifier", "indication_explorer", "redesign_advisor"]
    for t, r, n in zip(titles, roles, claims_per_section):
        cl = [dict(claim=f"{t} claim {i + 1}. Second sentence.", evidence_status="post_hoc" if (r == "population_stratifier") else "prespecified",
                   support_status="hypothesis_generating" if r == "population_stratifier" else "supported", uncertainty="low",
                   indicators=dict(evidence_strength="strong", mapping_confidence="source_identifier"),
                   evidence=[dict(id=f"E{r}{i}", source="ClinicalTrials.gov", source_record="NCT0", evidence_type="registry")])
              for i in range(n)]
        secs.append(dict(role=r, title=t, claims=cl, note=None if n else "no verified claim: the subgraph lacks the needed evidence",
                         graph=dict(nodes=[dict(id="P19", label="Anifrolumab — TULIP-1", category="tmkg:ClinicalTrialProgram")], edges=[])))
    return dict(question="Why did TULIP-1 fail?", seeds=["P19"], sections=secs, flow_links=[], run=dict(note="Offline."),
                rejected=[dict(role="redesign_advisor", claim="x", reasons=["no evidence cited"])][:rejected],
                label=dict(kind="offline", text="Offline stand-in: fixed rules."))


def test_trial_summary_counts_and_refs():
    r = report()
    s = S.trial_summary(r)
    assert s["headline"] == ("Anifrolumab — TULIP-1. Stated failure: Phase III: SRI-4 primary endpoint missed. "
                             "Diagnosis (claim 1.1, supported): Failure Diagnosis claim 1.")
    assert s["stated_failure"] == [dict(program="Anifrolumab — TULIP-1", what_failed="Phase III: SRI-4 primary endpoint missed.")]
    assert s["headline_refs"] == dict(claims=["1.1"])
    assert s["counts"]["claims"] == 4 and s["counts"]["rejected"] == 1 and s["counts"]["hypothesis_generating"] == 1
    assert s["points"][0]["text"] == "The report keeps 4 verified claims in 3 of 4 sections; the verifier removed 1."
    assert set(s["refs"]["claims"]) == {"1.1", "1.2", "2.1", "4.1"}
    assert s["confidence"]["level"] == "high"            # 1 of 4 hypothesis-generating: the 'at most a quarter' rule allows it
    assert any("2.1" in x for x in s["caveats"])
    assert S.trial_summary(report((2, 3, 0, 1)))["confidence"]["level"] == "moderate"      # 3 of 6
    assert any("Related Indications" in x for x in s["caveats"])
    assert s["plan"]["entities"] == [dict(id="P19", label="Anifrolumab — TULIP-1")]
    text = S.summary_text(s)
    assert "ClinicalTrials.gov" in text and "ChEMBL" not in text and "DisGeNET" not in text
    assert json.dumps(s, sort_keys=True) == json.dumps(S.trial_summary(report()), sort_keys=True)


def test_trial_summary_empty_report_abstains():
    s = S.trial_summary(report((0, 0, 0, 0), rejected=1))
    assert s["abstained"] and s["headline"] == "The report has no verified claim for this question."
    assert "no evidence cited" in S.summary_text(s) and s["confidence"]["level"] == "low"


# ------------------------------------------------------------------ the endpoints carry the summary (fixture graph, offline)
@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from fastapi.testclient import TestClient
    import app as appmod
    from nim import NIM
    from kg_query import KGIndex
    from fixtures.pharos_fixture import build
    from pharos_app import PharosGraph
    g = PharosGraph(root=build(tmp_path_factory.mktemp("repo")))
    appmod._pharos["g"] = g
    appmod._pharos["index"] = None
    appmod._state.update(kg=KGIndex(), nim=NIM(backend="offline"))
    yield TestClient(appmod.app), g
    appmod._pharos.clear()


def test_demo_one_keeps_its_keys_and_adds_a_summary(client):
    c, g = client
    one = c.get("/api/pharos/demo/pharos_demo_1").json()
    expected = json.loads(json.dumps(g.demo(ids={"pharos_demo_1"})[0]))
    assert {k: v for k, v in one.items() if k != "summary"} == expected
    s = one["summary"]
    assert s["counts"]["rows"] == len(expected["rows"]) and expected["rows"][0]["disease"] in s["headline"]
    assert not re.match(r"\d+ rows? answer", s["headline"])                   # U8: the headline names answers, not a count
    for e, f in s["refs"]["edges"].items():
        assert e in expected["evidence"] and f["source"] == g.edges[e].get("source")
    assert c.get("/api/pharos/demo").json() == json.loads(json.dumps(g.demo()))      # the list route is unchanged


def test_ask_and_summarize_routes(client):
    c, g = client
    r = c.post("/api/pharos/ask", json={"question": "Which diseases is PTGS2 associated with?"}).json()
    assert "summary" in r and r["summary"]["counts"]["rows"] == len(r["rows"])
    if not r["rows"]:
        assert r["summary"]["abstained"]
    s = c.post("/api/summarize", json={"kind": "pharos", "result": c.get("/api/pharos/demo/pharos_demo_2").json()}).json()
    assert s["counts"]["rows"] >= 1
    assert c.post("/api/summarize", json={"kind": "nope", "result": {}}).status_code == 400
    t = c.post("/api/ask", json={"question": "Why did TULIP-1 miss its primary endpoint, and what changed later?"}).json()
    assert t["summary"]["counts"]["claims"] == sum(len(x["claims"]) for x in t["sections"])
    assert set(t) >= {"question", "seeds", "sections", "flow_links", "rejected", "run", "markdown", "label", "summary"}
    q = c.post("/api/query", json={"question": "Which failed programs share a drug target?"}).json()
    qs = c.post("/api/summarize", json={"kind": "query", "result": q}).json()
    assert qs["counts"]["rows"] == len(q["rows"]) and "summary" not in q            # /api/query itself is unchanged


# ------------------------------------------------------------------ FIX2 F6: typed answers name what they found
def lrrk2_answer(name=None):
    """The shape of the live LRRK2 answer of 14 Nov: one drug whose name column is empty."""
    e1, e2 = "e00000000000000f1", "e00000000000000f2"
    rows = [dict(drug="CHEMBL.COMPOUND:CHEMBL2103830", drug_name=name, evidence=[e1, e2])]
    facts = {e1: fact(e1, "ChEMBL", "knowledge_assertion", kind="activity", subj="CHEMBL2103830", obj="LRRK2"),
             e2: fact(e2, "DrugCentral", "knowledge_assertion", subj="CHEMBL2103830", obj="LRRK2")}
    res = dict(question="Which drugs target the protein encoded by LRRK2?", entities=["NCBIGene:120892"],
               columns=["drug", "drug_name", "evidence"], rows=rows, evidence=[e1, e2], problems=[],
               label=dict(kind="offline", offline=True, text="Offline stand-in: fixed rules."),
               cypher="MATCH (c:Drug)-[r]->(p:Protein) RETURN c.id AS drug, c.name AS drug_name, r.id AS evidence LIMIT 50")
    return res, facts


def test_an_empty_label_falls_back_to_the_id_as_the_page_names_it():
    res, facts = lrrk2_answer()
    s = S.pharos_summary(res, facts, {})
    assert "unnamed compound (CHEMBL2103830)" in s["headline"], s["headline"]
    assert "row answer this question" not in s["headline"]
    assert S.id_label({"drug": "CHEMBL.COMPOUND:CHEMBL1", "x_id": "MONDO:1"}, ["x_id", "drug", "drug_name"], "drug_name") == "unnamed compound (CHEMBL1)"
    assert S.id_label({"gene": "NCBIGene:5"}, ["gene", "gene_name"], "gene_name") == "NCBIGene:5"
    assert S.id_label({"n": 3}, ["n", "name"], "name") is None
    named, facts = lrrk2_answer("upadacitinib")
    assert "upadacitinib" in S.pharos_summary(named, facts, {})["headline"]


def test_one_row_without_any_name_agrees_in_number():
    res, facts = lrrk2_answer()
    res = dict(res, rows=[dict(res["rows"][0], drug=None)])
    h = S.pharos_summary(res, facts, {})["headline"]
    assert h.startswith("1 row answers this question, resting on 2 links from"), h


def test_zero_rows_offer_the_card_of_what_the_question_named():
    """FIX2 F6 (G2-U-03): the no-evidence answer ended with the generic next step."""
    res = dict(question="Is there any evidence linking MAP3K5 and MASLD?", entities=["NCBIGene:4217", "MONDO:0007915"],
               columns=["e"], rows=[], evidence=[], problems=[], cypher="MATCH (g)-[r]-(d) RETURN r.id AS e")
    s = S.pharos_summary(res, {}, {"NCBIGene:4217": "MAP3K5", "MONDO:0007915": "MASLD"})
    assert s["next_step"] == "Open the MAP3K5 card or the MASLD card."
    bare = S.pharos_summary(dict(res, entities=[]), {}, {})
    assert bare["next_step"].startswith("Try one of the tested questions")


def test_the_page_words_the_no_evidence_confidence():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")
    assert 'label: "confidence that the graph holds no link"' in page
    assert '${esc(c.label || "confidence")}: ${esc(c.level)}' in page and "`confidence: ${" not in page
