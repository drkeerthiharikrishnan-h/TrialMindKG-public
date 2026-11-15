"""CC-31f (review finding M7): a graph question that mentions trials ("failed", "trial", "phase") also gets the trial layer's
programs and trials for the answer's entities and answer names (app.trial_panel -> /api/pharos/ask 'trials_panel')."""
import sys

import pytest

sys.path.insert(0, "pipelines")
import app as appmod  # noqa: E402
from kg_query import KGIndex  # noqa: E402


class G:
    def label(self, nid):
        return {"X:1": "anifrolumab"}.get(nid, nid)


@pytest.fixture(scope="module")
def kg():
    return KGIndex()


def test_no_trial_words_no_panel(kg):
    assert appmod.trial_panel("Which diseases is PTGS2 associated with?", dict(entities=["X:1"]), G(), kg) is None


def test_trial_words_look_up_the_entities_and_the_answer_names(kg):
    res = dict(entities=["X:1"], columns=["drug", "evidence"], rows=[dict(drug="anifrolumab", evidence="e1"),
                                                                     dict(drug=["nothing-here", "anifrolumab"], evidence="e2")])
    p = appmod.trial_panel("Which drugs failed in phase 3 trials?", res, G(), kg)
    assert p["reason"] == "the question mentions 'failed'" and p["names"] == ["anifrolumab", "nothing-here"]
    assert p["trials"] and any("TULIP" in t["label"] for t in p["trials"])
    assert p["trials"] == appmod.trials_for(kg, ["X:1"], ["anifrolumab", "nothing-here"])


def test_a_name_the_trial_layer_lacks_gives_an_empty_list(kg):
    p = appmod.trial_panel("Any clinical trials?", dict(entities=[], columns=["gene", "e"], rows=[dict(gene="ZZZ9", e="e1")]), G(), kg)
    assert p["names"] == ["ZZZ9"] and p["trials"] == []


def test_the_page_renders_the_panel():
    t = (appmod.ROOT / "pipelines" / "app_page_v3.html").read_text()
    assert "function trialsPanel(tp)" in t and "${trialsPanel(r.trials_panel)}" in t
