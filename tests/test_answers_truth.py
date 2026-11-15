"""Packet PK2 (review of 11 Nov): what an answer says must agree with its own evidence.

U1    a merged row is classed by its strongest link per step, pruned links not counted (answer_summary.classify_row)
U5    a question with a tested question's text gets the tested answer; no caveat for a name the query did resolve
U8    the headline names the top answers; a trial headline leads with the program's stated failure, no registry group codes
U11   program names for internal program ids, decimals at 3 significant figures, no stored answer for a tested question
AC-06 the bottom line says how many links behind the answer were pruned
U14   the first-screen numbers say what they count (Pharos links / all layers)

Real committed data where the packet's evidence came from: outputs/overlay/demo_overlay.json (the tested answers) and
outputs/examples (the stored answers); otherwise the small Pharos fixture graph. No network, no model.
"""
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import answer_summary as S  # noqa: E402
import headline  # noqa: E402
import pharos_app as P  # noqa: E402
import routes_answers as R  # noqa: E402
import stored_answers as sa  # noqa: E402

E = [f"e{i:016x}" for i in range(1, 13)]
LEVEL = dict(curated="knowledge_assertion", statistical="statistical_association", text="text_co_occurrence", none="not_provided")


def fact(eid, source, level, verdict="kept", obj="disease A"):
    return dict(id=eid, subject="NCBIGene:1", subject_label="GENE1", predicate="biolink:gene_associated_with_condition",
                phrase="is associated with", object="MONDO:1", object_label=obj, source=source,
                knowledge_level=LEVEL[level], strength=S.strength_word(LEVEL[level]), verdict=verdict, confidence=0.6,
                reasons="plausibility=0.5;sources=2", reasons_words="plausibility 0.50; 2 sources for the pair")


def overlay(demo_id):
    """A tested question with the answer committed in outputs/overlay/demo_overlay.json (rows as the graph returned them)."""
    d = next(x for x in P.DEMO_QUESTIONS if x["id"] == demo_id)
    return dict(d, **json.loads((ROOT / "outputs" / "overlay" / "demo_overlay.json").read_text())["results"][demo_id]["result"])


# ------------------------------------------------------------------ U1: strongest link per step
def test_a_merged_row_with_a_curated_link_is_not_text_mined_only():
    row = dict(disease="disease A", evidence=[E[0], E[1], E[2]])
    facts = {E[0]: fact(E[0], "CTD", "curated"), E[1]: fact(E[1], "DisGeNET", "none"), E[2]: fact(E[2], "TIN-X", "text")}
    c = S.classify_row(row, ["disease", "evidence"], facts)
    assert c["row_class"] == "curated" and c["strength"] == "curated"
    assert c["best_edges"] == [E[0]] and c["best_sources"] == ["CTD"] and c["sources"] == ["CTD", "DisGeNET", "TIN-X"]
    only_text = S.classify_row(dict(disease="d", evidence=[E[2]]), ["disease", "evidence"], facts)
    assert only_text["row_class"] == "text-mined"
    stat = S.classify_row(dict(disease="d", evidence=[E[2], E[3]]), ["disease", "evidence"],
                          dict(facts, **{E[3]: fact(E[3], "TIGA", "statistical")}))
    assert stat["row_class"] == "statistical"                    # text-only needs NO curated / measured / statistical link


def test_a_row_with_several_steps_is_as_strong_as_its_weakest_step():
    cols = ["gene", "e1", "e2"]
    facts = {E[0]: fact(E[0], "CTD", "curated"), E[1]: fact(E[1], "TIN-X", "text"), E[2]: fact(E[2], "TIN-X", "text"),
             E[3]: fact(E[3], "Monarch", "curated")}
    both = S.classify_row(dict(gene="G", e1=[E[0], E[1]], e2=[E[3], E[2]]), cols, facts)
    assert both["row_class"] == "curated" and [s["strength"] for s in both["steps"]] == ["curated", "curated"]
    weak_step = S.classify_row(dict(gene="G", e1=[E[0], E[1]], e2=[E[2]]), cols, facts)
    assert weak_step["row_class"] == "text-mined"               # step 2 has only a text-mined link


def test_a_pruned_link_is_not_counted_as_support():
    cols = ["disease", "evidence"]
    facts = {E[0]: fact(E[0], "CTD", "curated", verdict="pruned"), E[1]: fact(E[1], "TIN-X", "text")}
    assert S.classify_row(dict(disease="d", evidence=[E[0], E[1]]), cols, facts)["row_class"] == "text-mined"
    gone = S.classify_row(dict(disease="d", evidence=[E[0]]), cols, facts)
    assert gone["row_class"] == "pruned" and gone["pruned"] == [E[0]] and gone["best_edges"] == []


def test_the_level_columns_are_read_the_same_way_when_no_link_was_looked_up():
    cols = ["gene", "alzheimer_level", "diabetes_level", "e1", "e2"]
    row = dict(gene="APCS", alzheimer_level=["knowledge_assertion", "text_co_occurrence"],
               diabetes_level=["knowledge_assertion", "text_co_occurrence"], e1=[E[0], E[1]], e2=[E[2], E[3]])
    assert S.classify_row(row, cols, {})["row_class"] == "curated"
    assert S.classify_row(dict(row, diabetes_level="text_co_occurrence"), cols, {})["row_class"] == "text-mined"
    assert S.classify_row(dict(gene="G", e1=E[0]), ["gene", "e1"], {})["row_class"] == "unknown"


@pytest.mark.parametrize("demo_id", ["pharos_demo_1", "pharos_demo_3"])
def test_the_flagship_answers_are_not_called_text_mining_only(demo_id):
    """The review's evidence: demo 1 said '50 of 50 rows rest on text mining or prediction only', confidence low, above rows
    that cite curated sources. On the committed tested answers every shown row has a curated or statistical link per step."""
    s = S.pharos_summary(overlay(demo_id), {}, {})
    c = s["counts"]
    assert c["rows_text_only"] == 0 and c["rows_strong"] >= 1 and c["rows_strong"] + c["rows_statistical"] == c["rows"]
    text = S.summary_text(s)
    assert "rest on text mining or prediction only" not in text and "None of the" not in text
    assert s["confidence"]["level"] != "low" and "text" not in s["confidence"]["reason"].split(".")[0]
    assert set(s["refs"]["row_strength"].values()) <= {"curated", "statistical"}


def test_the_support_sentence_counts_each_class_and_confidence_follows():
    rows = [dict(disease=f"disease {x}", evidence=e) for x, e in zip("ABCD", ([E[0], E[4]], E[1], E[2], E[3]))]
    facts = {E[0]: fact(E[0], "CTD", "curated"), E[4]: fact(E[4], "TIN-X", "text"),
             E[1]: fact(E[1], "TIGA", "statistical", obj="disease B"), E[2]: fact(E[2], "TIN-X", "text", obj="disease C"),
             E[3]: fact(E[3], "TIN-X", "text", obj="disease D")}
    res = dict(question="q", columns=["disease", "evidence"], rows=rows, evidence=E[:5], problems=[], cypher="MATCH (a)-[r:x]->(b) RETURN b.name AS disease, r.id AS evidence")
    s = S.pharos_summary(res, facts)
    assert s["points"][0]["text"] == ("Of the 4 rows, 1 has curated or measured support, 1 has statistical support only and 2 are "
                                      "text-mined only. A row is classed by its strongest link that was not pruned.")
    assert s["counts"]["rows_strong"] == 1 and s["counts"]["rows_statistical"] == 1 and s["counts"]["rows_text_only"] == 2
    assert s["confidence"]["level"] == "moderate"               # 2 of 4 text-only is not more than half
    more_text = dict(res, rows=rows + [dict(disease="disease E", evidence=E[5])], evidence=E[:6])
    s2 = S.pharos_summary(more_text, dict(facts, **{E[5]: fact(E[5], "TIN-X", "text", obj="disease E")}))
    assert s2["confidence"] == dict(level="low", reason="3 of 5 rows rest on text mining or prediction only.")
    curated = {k: dict(v, knowledge_level=LEVEL["curated"], strength="curated") for k, v in facts.items()}
    s3 = S.pharos_summary(res, curated)
    assert s3["confidence"]["level"] == "high" and "4 of 4 rows have curated or measured support" in s3["confidence"]["reason"]


# ------------------------------------------------------------------ U8: the headline is an answer
def test_the_headline_names_the_top_answers_their_source_and_the_total():
    s = S.pharos_summary(overlay("pharos_demo_1"), {}, {})
    assert s["headline"] == ("Best-supported diseases: rheumatoid arthritis, esophageal squamous cell carcinoma and duodenal ulcer; "
                             "3,705 diseases in all, 3,373 text-mined only.")
    assert s["headline_refs"]["rows"] == [1, 2, 3]
    # FIX1 F8 (G1-P5-03): the tested answers are ranked by their evidence, so they say "Best-supported", not "Top"
    for demo_id, start, end in [("pharos_demo_2", "Best-supported compounds: ", "2,008 compounds in all."),
                                ("pharos_demo_3", "Best-supported genes: APCS, APOE and BCL2", "14,416 genes in all, 13,854 text-mined only."),
                                ("pharos_demo_4", "Best-supported proteins: ", "336 proteins in all."),
                                ("pharos_demo_5", "Best-supported traits: low density lipoprotein cholesterol measurement", "8 traits in all.")]:
        h = S.pharos_summary(overlay(demo_id), {}, {})["headline"]
        assert h.startswith(start) and h.endswith(end) and not re.match(r"\d+ rows? answer", h)


def test_with_link_facts_the_headline_is_a_sentence_with_the_strongest_sources():
    d = overlay("pharos_demo_1")
    facts = {}
    for i, r in enumerate(d["rows"][:3]):                        # the facts the graph would return for the first three rows
        for e, src, lvl in zip(r["evidence"], r["source"], ["curated"] + ["text"] * 9):
            facts[e] = dict(fact(e, src, lvl, obj=r["disease"]), subject_label="PTGS2")
    s = S.pharos_summary(d, facts, {})
    assert s["headline"].startswith("PTGS2 is associated with rheumatoid arthritis, esophageal squamous cell carcinoma and duodenal ulcer (CTD")
    assert s["headline"].endswith("3,705 diseases in all, 3,373 text-mined only.")
    assert "TIN-X" not in s["headline"]                          # the source of the strongest link, not of every link


def test_headline_nouns_and_fallback():
    assert S._noun("target_name", ["target", "target_name"]) == "target" and S._noun("name", ["endpoint", "name", "evidence"]) == "endpoint"
    assert [S._plural_word(w) for w in ("disease", "process", "biological process", "entity")] == \
        ["diseases", "processes", "biological processes", "entities"]
    res = dict(question="q", columns=["evidence"], rows=[dict(evidence=E[0])], evidence=[E[0]], problems=[], cypher="MATCH (a) RETURN a")
    assert S.pharos_summary(res, {})["headline"] == "1 row answers this question, resting on 1 link from 0 sources."   # FIX2 F6: verb agreement
    assert S.source_word("curated:failed_clinical_trials_2016_2026.md") == "curated record" and S.source_word("CTD") == "CTD"


def test_strip_arm_codes():
    f = S.strip_arm_codes
    assert f("comparing anifrolumab vs placebo (OG001 vs OG002), with a mean difference of -4.2") == \
        "comparing anifrolumab vs placebo, with a mean difference of -4.2"
    assert f("HR 0.84 (95% CI 0.69 to 1.01; p=0.0657) for groups OG000 vs OG001 from study start") == \
        "HR 0.84 (95% CI 0.69 to 1.01; p=0.0657) from study start"
    assert f("Difference -2.02 (p=0.2385) for OG000 vs OG001; second") == "Difference -2.02 (p=0.2385); second"
    assert f("results were mixed: OG000 vs OG001 showed difference 0.7") == "results were mixed: the compared groups showed difference 0.7"
    assert f("No codes here (95% CI 1 to 2).") == "No codes here (95% CI 1 to 2)."


def _claim(text, status="supported"):
    return dict(claim=text, support_status=status, evidence_status="prespecified", indicators=dict(evidence_strength="strong"),
                evidence=[dict(id="X1", source="ClinicalTrials.gov", source_record="NCT0", evidence_type="registry")])


def _report(claims, seeds=("P19",)):
    return dict(question="q", seeds=list(seeds), rejected=[], flow_links=[], label={},
                sections=[dict(role="failure_diagnostician", title="Failure Diagnosis", claims=claims, graph=dict(nodes=[], edges=[]))])


def test_a_trial_headline_leads_with_the_stated_failure_then_a_diagnosis_with_a_result():
    progs = {"P19": dict(id="P19", name="Drug X — TRIAL-1", what_failed="Phase III: primary endpoint missed.")}
    claims = [_claim("The primary endpoint was response at Week 52."),
              _claim("Drug X vs placebo (OG001 vs OG002): difference -4.2 (95% CI -14.2 to 5.8, p=0.412)."),
              _claim("The trial was stopped.")]
    s = S.trial_summary(_report(claims), progs)
    assert s["headline"] == ("Drug X — TRIAL-1. Stated failure: Phase III: primary endpoint missed. Diagnosis (claim 1.2, supported): "
                             "Drug X vs placebo: difference -4.2 (95% CI -14.2 to 5.8, p=0.412).")
    assert s["headline_refs"] == dict(claims=["1.2"]) and s["next_step"] == "Open the evidence for claim 1.2 (ClinicalTrials.gov, NCT0)."
    no_number = S.trial_summary(_report([claims[0], claims[2]]), progs)             # no result number: the outcome claim
    assert "Diagnosis (claim 1.2, supported): The trial was stopped." in no_number["headline"]
    plain = S.trial_summary(_report([claims[0]]), {})                               # unknown program: the claim alone
    assert plain["headline"] == "Diagnosis (claim 1.1, supported): The primary endpoint was response at Week 52."
    unsup = S.trial_summary(_report([_claim("Maybe.", status="hypothesis_generating")]), progs)
    assert "Diagnosis (claim 1.1, not marked supported): Maybe." in unsup["headline"]


def test_program_facts_come_from_the_trial_graph():
    progs = S.program_facts()
    assert len(progs) == 34 and progs["P19"]["name"] == "Anifrolumab — TULIP-1"
    assert progs["P19"]["what_failed"] == "Phase III: SRI-4 primary endpoint missed."
    assert S.program_facts(ROOT / "no_such_folder") == {}


def test_every_stored_trial_example_opens_with_why_it_failed():
    progs = S.program_facts()
    seen = 0
    for meta in sa.index():
        if meta["kind"] != "trials":
            continue
        seen += 1
        s = sa.load(sa.FOLDER, meta["id"])["answer"]["summary"]
        h = s["headline"]
        assert any(h.startswith(f"{p['name']}. Stated failure: ") for p in progs.values()), h
        assert " Diagnosis (claim 1." in h and not h.startswith("Failure Diagnosis (claim")
        text = S.summary_text(s)
        assert not re.search(r"\b[OBE]G\d{3}\b", text) and "curated:" not in text and ".md" not in text
    assert seen >= 8


# ------------------------------------------------------------------ AC-06: pruned links behind an answer
def test_the_bottom_line_says_how_many_links_were_pruned():
    rows = [dict(disease="disease A", evidence=[E[0], E[1]]), dict(disease="disease B", evidence=[E[2], E[3]])]
    facts = {E[0]: fact(E[0], "CTD", "curated"), E[1]: fact(E[1], "TIN-X", "text", verdict="pruned"),
             E[2]: fact(E[2], "CTD", "curated", obj="disease B"), E[3]: fact(E[3], "eRAM", "none", verdict="pruned", obj="disease B")}
    res = dict(question="q", columns=["disease", "evidence"], rows=rows, evidence=E[:4], problems=[], cypher="MATCH (a) RETURN a")
    s = S.pharos_summary(res, facts)
    assert any(p["text"] == "2 of the 4 links behind this answer were pruned and are left out of its support (rows 1-2); "
                            "the pruning check kept 2." and p["refs"] == dict(rows=[1, 2], edges=[E[1], E[3]]) for p in s["points"])
    assert s["pruned_hidden"] == dict(n=2, of=4, rows=[1, 2], edges=[E[1], E[3]], action="show them",
                                      text="2 links behind this answer were pruned and are hidden")
    assert s["counts"]["pruned"] == 2 and s["counts"]["rows_strong"] == 2 and s["confidence"]["level"] == "moderate"
    kept = {k: dict(v, verdict="kept") for k, v in facts.items()}
    s2 = S.pharos_summary(res, kept)
    assert s2["pruned_hidden"] is None
    assert any(p["text"] == "No link behind this answer was pruned: the pruning check kept 4 of 4." for p in s2["points"])
    one = S.pharos_summary(dict(res, rows=rows[:1], evidence=E[:2]), facts)
    assert one["pruned_hidden"]["text"] == "1 link behind this answer was pruned and is hidden"
    unscored = {k: dict(v, verdict=None) for k, v in facts.items()}
    assert any("carries a pruning verdict" in p["text"] for p in S.pharos_summary(res, unscored)["points"])


def test_every_link_of_the_largest_tested_answer_is_looked_up():
    n = max(len({e for r in overlay(d["id"])["rows"] for e in S.row_edges(r, overlay(d["id"])["columns"])}) for d in P.DEMO_QUESTIONS)
    import inspect
    assert inspect.signature(S.edge_facts).parameters["limit"].default >= n      # else the pruned count would be partial


# ------------------------------------------------------------------ U5: one question, one answer
def test_tested_question_matches_the_text_only():
    q3 = "Which genes are associated with both Alzheimer disease and type 2 diabetes mellitus?"
    assert P.tested_question(q3)["id"] == "pharos_demo_3"
    assert P.tested_question("  which genes are associated with both alzheimer disease  and type 2 diabetes mellitus ")["id"] == "pharos_demo_3"
    assert P.tested_question("Which genes are associated with Alzheimer disease?") is None and P.tested_question("") is None
    assert R.tested(q3) == dict(tested=True, id="pharos_demo_3", question=q3)
    assert R.tested("something else") == dict(tested=False, id=None, question=None)


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    from fixtures.pharos_fixture import build
    return P.PharosGraph(root=build(tmp_path_factory.mktemp("repo")))


def test_asking_a_tested_question_returns_the_tested_answer_without_a_model(graph, monkeypatch):
    import querier
    from nim import NIM

    class NoModel:
        def __init__(self, *a, **k):
            raise AssertionError("a tested question must not reach the model")
    monkeypatch.setattr(querier, "Querier", NoModel)
    for d in P.DEMO_QUESTIONS:
        tested = graph.demo(ids={d["id"]})[0]
        got = P.ask(graph, NIM(backend="offline"), d["question"].lower().rstrip("?") + " ")
        assert got["tested"] == d["id"] and got["cypher"] == d["cypher"] and got["passed"] == tested["passed"]
        assert got["rows"] == tested["rows"] and got["evidence"] == tested["evidence"] and got["columns"] == tested["columns"]
        assert got["entity_sentences"] == [] and got["entity_notes"] == [] and got["problems"] == [] and got["backend"] == ""
    got = P.ask(graph, NIM(backend="offline"), P.DEMO_QUESTIONS[2]["question"])
    assert got["entities"] == ["MONDO:0004975", "MONDO:0005148"]               # the ids the query used, not a broader term
    one = P.ask(graph, NIM(backend="offline"), P.DEMO_QUESTIONS[0]["question"])
    assert "NCBIGene:5743" in one["entities"]                                   # named by name in the query


def test_the_ask_route_gives_the_tested_answer_and_the_same_bottom_line(graph):
    from fastapi.testclient import TestClient
    import app as appmod
    from kg_query import KGIndex
    from nim import NIM
    appmod._pharos["g"], appmod._pharos["index"] = graph, None
    appmod._state.update(kg=KGIndex(), nim=NIM(backend="offline"))
    try:
        c = TestClient(appmod.app)
        q = P.DEMO_QUESTIONS[2]["question"]
        asked = c.post("/api/pharos/ask", json={"question": q}).json()
        demo = c.get("/api/pharos/demo/pharos_demo_3").json()
        assert asked["rows"] == demo["rows"] and asked["cypher"] == demo["cypher"] and asked["rows"]
        assert asked["summary"]["headline"] == demo["summary"]["headline"] and asked["summary"]["caveats"] == demo["summary"]["caveats"]
        assert not any("is not a name in the graph" in x or "was read as" in x for x in asked["summary"]["caveats"])
        assert c.get("/api/pharos/tested", params={"question": q}).json()["id"] == "pharos_demo_3"
    finally:
        appmod._pharos.clear()


# the notes the stored answer q_0ebcc18413 carried (review finding U5), and the two queries in question
NOTES = [dict(phrase="type 2 diabetes mellitus", used="diabetes mellitus", used_id="HP:0000819",
              found=[dict(id="MONDO:0005148", label="type 2 diabetes mellitus"), dict(id="MONDO:1011605", label="type 2 diabetes mellitus, pig")]),
         dict(phrase="Alzheimer disease", used="disease", used_id="MONDO:0000001",
              found=[dict(id="HP:0002511", label="Alzheimer disease"), dict(id="MONDO:0004975", label="Alzheimer disease")])]
CYPHER = ("MATCH (g:Gene)-[r1:gene_associated_with_condition]->(d1:Disease {id:'MONDO:0004975'}), "
          "(g)-[r2:gene_associated_with_condition]->(d2:Disease {id:'DIABETES'}) RETURN g.name AS gene, r1.id AS e1, r2.id AS e2")
LABELS = {"MONDO:0004975": "Alzheimer disease", "MONDO:0005015": "diabetes mellitus", "MONDO:0005148": "type 2 diabetes mellitus"}


def test_no_caveat_for_a_name_the_query_did_resolve():
    label = lambda i: LABELS.get(i, i)
    kept, sentences = P.entity_caveats(NOTES, CYPHER.replace("DIABETES", "MONDO:0005015"), label)
    assert [n["phrase"] for n in kept] == ["type 2 diabetes mellitus"]          # Alzheimer disease WAS resolved (MONDO:0004975)
    assert sentences == ["'type 2 diabetes mellitus' was read as 'diabetes mellitus' (MONDO:0005015), so the rows are about that, "
                         "not about type 2 diabetes mellitus (MONDO:0005148); name the identifier to ask about it."]
    assert P.entity_caveats(NOTES, CYPHER.replace("DIABETES", "MONDO:0005148"), label) == ([], [])    # both resolved
    assert P.entity_caveats(NOTES, CYPHER.replace("DIABETES", "MONDO:0005148")) == ([], [])           # also without labels
    assert P.entity_caveats(NOTES, "MATCH (d:Disease {name:'Alzheimer disease'}) RETURN d.id AS id")[0] == NOTES[:1]
    kept, sentences = P.entity_caveats(NOTES, "")                                # nothing ran: every note stays
    assert kept == NOTES and len(sentences) == 2
    assert P.query_anchors(CYPHER) == (["MONDO:0004975", "DIABETES"], [])


def test_a_name_that_is_not_in_the_graph_still_says_so():
    note = dict(phrase="Nipah virus infection", used="infection", used_id="MONDO:0005550", found=[dict(id="X:1", label="Nipah virus")])
    assert P.substitution_sentence(note) == ("'Nipah virus infection' is not a name in the graph; the question was read with "
                                             "'infection' (MONDO:0005550). The graph's search has: Nipah virus (X:1).")


class TinyGraph:
    nodes = {"MONDO:0004975": dict(name="Alzheimer disease"), "MONDO:0000001": dict(name="disease"), "MONDO:0005015": dict(name="diabetes mellitus")}

    def label(self, nid):
        return self.nodes[nid]["name"]

    def search(self, q, limit=3):
        return [dict(id=i, label=n["name"]) for i, n in self.nodes.items() if q.lower() in n["name"].lower()][:limit]


def test_no_substitution_note_when_the_longer_name_is_itself_recognised():
    g = TinyGraph()
    q = "Which genes are associated with both Alzheimer disease and type 2 diabetes mellitus?"
    notes = P.substitution_notes(g, q, ["MONDO:0000001", "MONDO:0004975", "MONDO:0005015"])
    assert [n["phrase"] for n in notes] == ["type 2 diabetes mellitus"]         # 'Alzheimer disease' is a recognised entity
    assert [n["phrase"] for n in P.substitution_notes(g, q, ["MONDO:0000001", "MONDO:0005015"])] == \
        ["Alzheimer disease", "type 2 diabetes mellitus"]                       # not recognised: the note stays


def test_no_stored_answer_competes_with_a_tested_question(tmp_path):
    stored = {m["question"] for m in sa.index()}
    assert not any(P.tested_question(q) for q in stored)
    for d in P.DEMO_QUESTIONS:
        assert not (sa.FOLDER / f"{sa.question_id(d['question'])}.json").exists()
    q = P.DEMO_QUESTIONS[2]["question"]                                          # a stale file is not offered either
    rec = sa.record(dict(id=sa.question_id(q), kind="pharos", question=q, endpoint="/api/pharos/ask"), {"rows": []}, "nim", "m")
    (tmp_path / f"{rec['id']}.json").write_text(json.dumps(rec))
    other = sa.record(dict(id="q_0123456789", kind="pharos", question="Another question?", endpoint="/api/pharos/ask"), {"rows": []}, "nim", "m")
    (tmp_path / "q_0123456789.json").write_text(json.dumps(other))
    assert [m["id"] for m in sa.index(tmp_path)] == ["q_0123456789"]


# ------------------------------------------------------------------ U11: names, not internal ids; rounded decimals
def test_sig3_and_display_value():
    assert [S.sig3(x) for x in (74.33001924132616, 0.000123456, 1234.567, 9.96, 0.5)] == ["74.3", "0.000123", "1235", "9.96", "0.500"]
    d = S.display_value
    assert d("74.33001924132616") == "74.3" and d("56.09262452797755") == "56.1" and d(74.33001924132616) == 74.3
    assert d("10.7") == "10.7" and d("9.96") == "9.96" and d("3") == "3" and d("2018") == "2018" and d("10.70") == "10.70"
    assert d("1e-9") == "1e-9" and d("0.000012345678") == "0.0000123"
    assert d("e2e7a534c72fd65f4") == "e2e7a534c72fd65f4" and d("NCT:NCT02446912") == "NCT:NCT02446912" and d("1.2.3") == "1.2.3"
    assert d(None) is None and d(True) is True and d(7) == 7 and d(["1.23456", "CTD"]) == ["1.23", "CTD"]
    rows = [dict(a="1.23456", e="e2e7a534c72fd65f4")]
    assert S.display_rows(rows) == [dict(a="1.23", e="e2e7a534c72fd65f4")] and rows[0]["a"] == "1.23456"   # input untouched


def test_the_tested_answers_print_three_significant_figures(graph):
    shown = P.display_result(overlay("pharos_demo_5"))
    assert shown["rows"][0]["rank_score"] == "74.3" and shown["rows"][0]["studies"] == "3"
    assert shown["rows"][0]["evidence"] == overlay("pharos_demo_5")["rows"][0]["evidence"]
    for d in graph.demo():
        for r in d["rows"]:
            for v in r.values():
                for x in (v if isinstance(v, list) else [v]):
                    assert not (isinstance(x, str) and re.fullmatch(r"-?\d+\.\d{4,}", x)), (d["id"], x)


def test_the_index_serves_the_tested_answers_through_the_same_display():
    import inspect
    import index_graph
    assert "pharos_app.display_result(" in inspect.getsource(index_graph.IndexGraph.demo)


def test_shared_target_answer_names_the_programs():
    raw = json.loads((sa.FOLDER / "x15.json").read_text())["answer"]
    assert raw["rows"][0]["program_1"] == "P22"                                  # as recorded: the internal id
    shown = sa.load(sa.FOLDER, "x15")["answer"]
    progs = S.program_facts()
    assert shown["rows"][0]["program_1"] == "Elafibranor — RESOLVE-IT" == progs["P22"]["name"]
    names = {p["name"] for p in progs.values()}
    for r in shown["rows"]:
        assert r["program_1"] in names and r["program_2"] in names
        assert not any(isinstance(v, str) and re.fullmatch(r"P\d{2}", v) for v in r.values())
    assert [r["target"] for r in shown["rows"]] == [r["target"] for r in raw["rows"]]         # public ids and link ids unchanged
    assert [r["e1"] for r in shown["rows"]] == [r["e1"] for r in raw["rows"]]


def test_program_names_only_touches_id_columns():
    progs = {"P01": dict(name="Drug A — TRIAL")}
    cy = "MATCH (p:ClinicalTrialProgram)-[r:x]->(d) RETURN p.id AS program, d.name AS note, r.id AS e1 ORDER BY program LIMIT 5"
    rows = [dict(program="P01", note="P01", e1="e0000000000000001"), dict(program=["P01", "P99"], note="x", e1="e0000000000000002")]
    assert sa.program_names(rows, cy, progs) == [dict(program="Drug A — TRIAL", note="P01", e1="e0000000000000001"),
                                                 dict(program=["Drug A — TRIAL", "P99"], note="x", e1="e0000000000000002")]
    assert sa._id_columns(cy) == ["program", "e1"] and sa.program_names(rows, "", progs) == rows   # no query: nothing is renamed


# ------------------------------------------------------------------ stored bottom lines follow the current rules
def test_stored_bottom_lines_are_the_current_rules_and_rewrite_is_idempotent(tmp_path):
    for p in sa.FOLDER.glob("*.json"):
        shutil.copy(p, tmp_path / p.name)
    assert sa.rewrite(tmp_path) == []                             # the committed files already hold the recomputed bottom lines
    d = json.loads((tmp_path / "x05.json").read_text())
    d["answer"]["summary"]["headline"] = "Failure Diagnosis (claim 1.1): old"
    (tmp_path / "x05.json").write_text(json.dumps(d))
    assert sa.load(tmp_path, "x05")["answer"]["summary"]["headline"].startswith("Anifrolumab — TULIP-1. Stated failure: ")
    assert sa.rewrite(tmp_path) == ["x05"] and sa.rewrite(tmp_path) == []
    q = json.loads((tmp_path / "x08.json").read_text())           # a trial-graph answer has no stored bottom line
    assert "summary" not in q["answer"] and sa.resummarise(q) is None
    assert sa.display(dict(kind="query", answer="not a dict")) == dict(kind="query", answer="not a dict")


def test_no_stored_pharos_bottom_line_calls_curated_rows_text_mined():
    for meta in sa.index():
        if meta["kind"] != "pharos":
            continue
        a = sa.load(sa.FOLDER, meta["id"])["answer"]
        s = a["summary"]
        for i, r in enumerate(a["rows"], 1):
            mine = S.classify_row(r, a["columns"], s["refs"]["edges"])
            assert s["refs"]["row_class"][i] == mine["row_class"]
        assert "pruned_hidden" in s and not re.match(r"\d+ rows? answer", s["headline"])


# ------------------------------------------------------------------ U14: the first-screen numbers say what they count
def test_graph_numbers_label_pharos_links_and_all_layers_separately():
    n, p = R.graph_numbers(), headline.prune_numbers()
    assert n["pharos"]["links"] == p["scored"] and n["pharos"]["pruned"] == p["pruned"]
    assert n["pharos"]["kept"] + n["pharos"]["pruned"] == n["pharos"]["links"]
    assert n["all_layers"]["links"] == p["g2_edges"] and n["all_layers"]["pruned"] == p["g3_removed"] and n["all_layers"]["kept"] == p["g3_edges"]
    assert n["pharos"]["pruned_percent"] == p["pruned_share"]
    first, second = n["sentence"].split(". ", 1)
    assert f"{p['scored']:,} Pharos links" in first and f"{p['pruned']:,} pruned" in first
    assert f"{p['g3_removed']:,}" not in first                    # the all-layer total is not presented as a Pharos verdict count
    assert second.startswith("Across all layers of the graph") and f"{p['g3_removed']:,} pruned" in second and f"{p['g2_edges']:,}" in second
    assert "pharos" not in n["added_layers"] and len(n["added_layers"]) == 6 and "Open Targets" in n["added_layers"]    # Pharos is the seed, not one of its own additions
    assert "files" not in n


def test_graph_numbers_leave_out_what_has_no_file(tmp_path):
    n = R.graph_numbers(tmp_path)
    assert n["pharos"] is None and n["all_layers"] is None and n["sentence"] == "" and n["added_layers"] == []


def test_graph_numbers_route():
    from fastapi.testclient import TestClient
    import app as appmod
    r = TestClient(appmod.app).get("/api/graph_numbers")
    assert r.status_code == 200 and r.json() == json.loads(json.dumps(R.graph_numbers()))


# ------------------------------------------------------------------ nothing internal in what a reader sees
INTERNAL = re.compile(r"\bG[0-4]\b|\bLIMIT\b|curated:|\.md\b|\.json\b|\.csv\b|\bPK\d|\bCC-\d|\b[OBE]G\d{3}\b|outputs/|pipelines/")


def test_bottom_lines_hold_no_internal_vocabulary():
    texts = [S.summary_text(S.pharos_summary(overlay(d["id"]), {}, {})) for d in P.DEMO_QUESTIONS]
    for meta in sa.index():
        a = sa.load(sa.FOLDER, meta["id"])["answer"]
        if "summary" in a:
            s = a["summary"]
            texts.append(" ".join([s["headline"], s["confidence"]["reason"], s["next_step"]] + [p["text"] for p in s["points"]]))
    texts.append(json.dumps(R.graph_numbers()))                  # S03/PK2: the first-screen payload, too (no file names, no layer codes)
    assert len(texts) >= 14
    for t in texts:
        assert not INTERNAL.search(t), INTERNAL.search(t).group(0) + " in: " + t[:200]


# ------------------------------------------------------------------ FIX2 F6 (BB-1, narrow): the resolved node names itself
def test_the_explanation_and_headline_take_the_label_of_the_resolved_node():
    """A fake resolution of the Gate 2 case: 'non-small cell lung carcinoma' was read as lung carcinoma (MONDO:0005138) while
    the explanation printed the user's phrase with that id."""
    import answer_summary
    import pharos_app as pa
    labels = {"MONDO:0005138": "lung carcinoma", "NCBIGene:1956": "EGFR", "HP:0030731": "Carcinoma",
              "MONDO:0005233": "non-small cell lung carcinoma"}
    lab = lambda i: labels.get(i, i)                                                        # noqa: E731
    q = "What links EGFR and non-small cell lung carcinoma, with PubMed ids?"
    cy = "MATCH (g:Gene {id:'NCBIGene:1956'})-[r]-(d:Disease {id:'MONDO:0005138'}) RETURN d.name AS disease, r.id AS e"
    note = dict(phrase="non-small cell lung carcinoma", used="carcinoma", used_id="HP:0030731",
                found=[dict(id="MONDO:0005233", label="non-small cell lung carcinoma")])
    kept, sentences = pa.entity_caveats([note], cy, lab)
    assert kept == [note] and "(MONDO:0005138)" in sentences[0] and "HP:0030731" not in sentences[0]   # the id the query used
    assert pa.read_as(kept, cy, lab) == [dict(phrase="non-small cell lung carcinoma", id="MONDO:0005138", label="lung carcinoma")]
    ex = "Finds links between EGFR (NCBIGene:1956) and non-small cell lung carcinoma (MONDO:0005138)."
    assert pa.explanation_labels(ex, q, cy, lab) == "Finds links between EGFR (NCBIGene:1956) and lung carcinoma (MONDO:0005138)."
    assert pa.explanation_labels("no ids here", q, cy, lab) == "no ids here"
    right = "Finds lung carcinoma (MONDO:0005138)."                                          # already the node's label
    assert pa.explanation_labels(right, q, cy, lab) == right
    res = dict(question=q, entities=["NCBIGene:1956", "MONDO:0005138"], columns=["disease", "e"],
               rows=[dict(disease="lung carcinoma", e="e00000000000000a9")], evidence=["e00000000000000a9"], problems=[],
               cypher=cy, read_as=pa.read_as(kept, cy, lab))
    h = answer_summary.pharos_summary(res, {}, labels)["headline"]
    assert h.startswith("Read as lung carcinoma: "), h
    assert not answer_summary.pharos_summary(dict(res, read_as=[]), {}, labels)["headline"].startswith("Read as")
    same = dict(note, phrase="lung carcinoma")                                               # the label is the phrase: no lead
    assert pa.read_as([same], cy, lab) == []
