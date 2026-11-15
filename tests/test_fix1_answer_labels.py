"""FIX1 F8 (gate 1: G1-U-03, G1-P5-03, G1-U-06): answer labels say what they mean.

- a tested question's "How the question was read" is filled from the ids and names its query ran on, not "No entity ... recognised";
- "Top" only when the rows are ordered by a measure; evidence-ranked rows are "Best-supported", an alphabetical ORDER BY says so;
- no raw link id in the next step, and the per-step evidence columns e1..eN are one column on the page and in the plain-words plan."""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import answer_summary as S  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402

PAGE = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")


def rows_answer(cypher, ordering=None, n=5):
    rows = [dict(interacting_protein=f"protein {chr(97 + i)}", evidence=f"e{i:016x}") for i in range(n)]
    return dict(question="q", columns=["interacting_protein", "evidence"], rows=rows, evidence=[r["evidence"] for r in rows],
                problems=[], cypher=cypher, ordering=ordering or {})


@pytest.mark.parametrize("cypher, ordering, start", [
    ("MATCH (a:Protein)-[r:x]->(b:Protein) RETURN b.name AS interacting_protein, r.id AS evidence ORDER BY interacting_protein LIMIT 3",
     None, "First interacting proteins, alphabetically: "),
    ("MATCH (a:Protein)-[r:x]->(b:Protein) RETURN b.name AS interacting_protein, r.score AS score, r.id AS evidence ORDER BY score DESC LIMIT 3",
     None, "Top interacting proteins: "),
    ("MATCH (a:Protein)-[r:x]->(b:Protein) RETURN b.name AS interacting_protein, r.id AS evidence ORDER BY interacting_protein LIMIT 3",
     {"by": "evidence", "n_total": 5}, "Best-supported interacting proteins: "),
    ("MATCH (a:Protein)-[r:x]->(b:Protein) RETURN b.name AS interacting_protein, r.id AS evidence LIMIT 3",
     None, "Interacting proteins include "),
])
def test_the_word_in_front_of_the_answers_follows_the_ordering(cypher, ordering, start):
    s = S.pharos_summary(rows_answer(cypher, ordering), {}, {})
    assert s["headline"].startswith(start), s["headline"]


def test_order_kind():
    assert S.order_kind("... ORDER BY interacting_protein LIMIT 50") == "alphabetical"
    assert S.order_kind("... ORDER BY n_sources LIMIT 5") == "measure"
    assert S.order_kind("... ORDER BY p.name DESC LIMIT 5") == "measure"
    assert S.order_kind("... ORDER BY score DESC", {"by": "evidence"}) == "evidence"   # the evidence ranking comes first
    assert S.order_kind("MATCH (a) RETURN a.id AS a, r.id AS e LIMIT 5") == "none"


def test_next_step_has_no_raw_link_id():
    s = S.pharos_summary(rows_answer("MATCH (a)-[r:x]->(b) RETURN b.name AS interacting_protein, r.id AS evidence LIMIT 9"), {}, {})
    assert s["next_step"].startswith("Open the evidence for row 1") and "e0000" not in s["next_step"]


def test_step_columns_are_one_phrase_in_the_plain_words_plan():
    words = S.cypher_words("MATCH (g:Gene)-[r1:x]->(d:Disease), (g)-[r2:y]->(e:Disease) "
                           "RETURN g.name AS gene, r1.source AS alzheimer_source, r1.id AS e1, r2.id AS e2 LIMIT 100")
    assert "Return gene, alzheimer source, the evidence of each step" in words and " e1" not in words and " e2" not in words


def test_the_page_merges_step_columns_and_labels_evidence_buttons():
    assert "const STEP_COL = /^e\\d+$/;" in PAGE and '"evidence_of_each_step"' in PAGE
    assert "evidence ${i + 1}" in PAGE and 'title="open the evidence record ${esc(e)}"' in PAGE
    assert "esc(noIds(say(s.next_step)))" in PAGE


@pytest.fixture(scope="module")
def fixture_client(tmp_path_factory):
    repo = build(tmp_path_factory.mktemp("repo"))
    old = {k: os.environ.get(k) for k in ("TMKG_PHAROS_ROOT", "TMKG_PHAROS_INDEX")}
    os.environ.update(TMKG_PHAROS_ROOT=str(repo), TMKG_PHAROS_INDEX="off")
    import app as appmod
    appmod._pharos.clear()
    yield TestClient(appmod.app)
    appmod._pharos.clear()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_tested_questions_are_read_from_the_query_they_ran(fixture_client):
    one = fixture_client.get("/api/pharos/demo/pharos_demo_1").json()["summary"]
    assert one["plan"]["entities"] == [dict(id="NCBIGene:5743", label="PTGS2")]          # {name:'PTGS2'} in the query
    three = fixture_client.get("/api/pharos/demo/pharos_demo_3").json()["summary"]
    assert [e["id"] for e in three["plan"]["entities"]] == ["MONDO:0004975", "MONDO:0005148"]   # {id:'MONDO:...'}
    assert not any("No entity" in p["text"] for p in one["points"] + three["points"])


def test_query_entities_without_anchors_is_empty():
    class G:
        nodes = {}

        def find_entities(self, text):
            return []
    assert S.query_entities("MATCH (a)-[r]->(b) RETURN a.id AS a, r.id AS e LIMIT 5", G()) == []


# ------------------------------------------------------------------------------------------------ F10: the models behind the numbers
def test_lead_rows_carry_their_folds():
    import numbers_page
    L = numbers_page.numbers(ROOT)["benchmark"]["lead"]
    assert [len(r["folds"]) for r in L["rows"]] == [L["folds"]] * 3
    assert all(r["folds"][0]["fold"] == 1 and 0.5 < r["folds"][0]["auroc"] < 1 for r in L["rows"])
    for r in L["rows"]:
        assert abs(sum(f["auroc"] for f in r["folds"]) / len(r["folds"]) - r["auroc"]) < 0.001   # the mean is of these folds


def test_the_first_screen_names_the_model_behind_the_card_predictions():
    assert 'id="heromodel"' in PAGE and "async function heroModelLine()" in PAGE and "/api/pharos/prediction_scale" in PAGE
    assert 'id="herosource"' in PAGE and 'href="#numbers"' in PAGE and 'id="num-lead-folds"' in PAGE
    hero = PAGE[PAGE.index('id="hero"'):PAGE.index('id="howsteps"')]
    assert 'id="seeworked"' in hero                     # F12 (G1-U-05): the buttons come before the four steps
