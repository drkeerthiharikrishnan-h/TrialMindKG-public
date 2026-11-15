"""CC-31g (review finding m5): a question that names nothing the graph knows is answered without a model call and in plain
words, with "did you mean" (PTSG2 -> PTGS2); a name read as a shorter one is said ('severe inflammatory response' read as
'inflammatory response'); a failed query does not show validator text as the answer. Fixture graph only."""
import json
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "pipelines")
import answer_summary  # noqa: E402
import app as appmod  # noqa: E402
import pharos_app  # noqa: E402
from nim import NIM  # noqa: E402
from pharos_app import PharosGraph  # noqa: E402
from pharos_index import CardIndex, build_index  # noqa: E402
from fixtures.pharos_fixture import build, GENE, PROT  # noqa: E402


@pytest.fixture(scope="module")
def graphs(tmp_path_factory):
    root = build(tmp_path_factory.mktemp("ab"))
    return root, PharosGraph(root=root), CardIndex(build_index(root, chunk=3, log=lambda *_: None)["path"])


class NoCall(NIM):
    """A NIM that fails the test if the model is called."""
    def __init__(self):
        super().__init__(backend="offline")

    def chat(self, *a, **k):
        raise AssertionError("the model must not be called")


def test_close_matches_catches_a_letter_swap():
    got = pharos_app.close_matches("PTSG2", [("ptgs2", "G"), ("ptk2", "K"), ("sod1", "S")])
    assert [x[1] for x in got] == ["G"] and got[0][0] == pytest.approx(0.8)
    assert pharos_app.close_matches("zzzz", [("ptgs2", "G")]) == []


def test_suggest_is_the_same_on_the_live_graph_and_the_index(graphs):
    _, g, ci = graphs
    live, fast = g.suggest("PTSG2"), ci.suggest("PTSG2")
    assert {s["id"] for s in live} == {GENE, PROT} and live == fast and all(s["did_you_mean"] for s in live)
    assert ci.graph().suggest("PTSG2") == fast and g.suggest("") == []


def test_search_offers_did_you_mean_only_when_asked(graphs, monkeypatch):
    root, _, ci = graphs
    monkeypatch.setenv("TMKG_PHAROS_INDEX", str(ci.path))
    monkeypatch.setenv("TMKG_PHAROS_ROOT", str(root))
    appmod._pharos.clear()
    try:
        c = TestClient(appmod.app)
        assert c.get("/api/pharos/search", params={"q": "PTSG2"}).json() == []          # the old pages keep their behaviour
        s = c.get("/api/pharos/search", params={"q": "PTSG2", "suggest": 1}).json()
        assert s and all(h["did_you_mean"] for h in s) and GENE in {h["id"] for h in s}
        assert not any(h.get("did_you_mean") for h in c.get("/api/pharos/search", params={"q": "PTGS2", "suggest": 1}).json())
    finally:
        appmod._pharos.clear()


@pytest.mark.parametrize("which", [1, 2])
def test_a_question_without_a_known_entity_abstains_before_the_model(graphs, which):
    g = graphs[1] if which == 1 else graphs[2].graph()
    res = pharos_app.ask(g, NoCall(), "Which diseases is PTSG2 linked to?")
    assert res["abstained"] and res["rows"] == [] and res["attempts"] == [] and res["problems"] == [pharos_app.NO_ENTITY]
    assert GENE in {s["id"] for s in res["suggestions"]} and res["suggestions"][0]["word"] == "PTSG2"
    s = answer_summary.pharos_summary(res)
    assert s["abstained"] and s["points"][0]["text"] == f"Here is why: {pharos_app.NO_ENTITY.rstrip('.')}."
    assert s["points"][1]["text"].startswith("Did you mean: PTGS2")
    assert not any("No entity in the question was recognised" in p["text"] for p in s["points"])


def test_a_shorter_name_read_in_place_of_a_longer_one_is_said(graphs):
    _, g, _ = graphs
    q = "Which genes are linked to severe inflammatory response?"
    notes = pharos_app.substitution_notes(g, q, g.find_entities(q))
    assert notes == [dict(phrase="severe inflammatory response", used="inflammatory response", used_id="GO:0006954", found=[])]
    assert pharos_app.substitution_sentence(notes[0]) == ("'severe inflammatory response' is not a name in the graph; the question "
                                                          "was read with 'inflammatory response' (GO:0006954).")
    for d in pharos_app.DEMO_QUESTIONS:                         # no false alarm on the tested questions
        assert pharos_app.substitution_notes(g, d["question"], g.find_entities(d["question"])) == [], d["id"]


def test_possessive_names_are_recognised(graphs):
    _, g, ci = graphs
    q = "Which compounds are linked to ibuprofen's protein?"
    assert "CHEMBL.COMPOUND:CHEMBL521" in g.find_entities(q) and g.find_entities(q) == ci.graph().find_entities(q)


def test_a_failed_query_is_explained_in_plain_words():
    res = dict(question="q", entities=[GENE], columns=[], rows=[], evidence=[], cypher="MATCH (n) RETURN n LIMIT 1",
               problems=["return or compare properties (e.g. n.name), not the whole variable n"],
               attempts=[dict(cypher="x"), dict(cypher="y"), dict(cypher="z")])
    s = answer_summary.pharos_summary(res)
    assert s["points"][0]["text"] == ("Here is why: the model did not write a valid query for this question in 3 attempts "
                                      "(the validator's reasons are under checks).")
    assert any("not the whole variable" in c["text"] for c in s["checks"])           # the detail is kept, under checks
