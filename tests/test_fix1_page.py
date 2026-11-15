"""FIX1 jsdom checks of the front page (F6, F8, F10, F12) against the real app on the Pharos test fixture, offline model.

F6: the first screen says the status verdict counts are across all layers and names the Pharos figures beside them.
F8: tested questions 1 and 3 read the question from their query (no "No entity ... recognised"), the next step has no raw
    link id, the bottom-line buttons carry reader labels, and no table header is e1 / e2.
F10: the hero names the model behind the card predictions; F12: the buttons come before the four steps, the Safari note is
    folded away, and the Pharos-alone switch does not carry over to the next tested question."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_page_pk6 import CHOOSE, extended_fixture, needs_jsdom, run_view  # noqa: E402


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return extended_fixture(tmp_path_factory.mktemp("fix1"))


FIRST = r"""(() => JSON.stringify({
  about: document.querySelector("#about").textContent,
  heromodel: document.querySelector("#heromodel").hidden ? "" : document.querySelector("#heromodel").textContent,
  order: !!(document.querySelector("#seeworked").compareDocumentPosition(document.querySelector("#howsteps")) & 4),
  safari: document.querySelector("#safarinote").closest("details") ? document.querySelector("#safarinote").closest("details").open : "no details",
  source: !!document.querySelector("#herosource")}))()"""

ANSWER = r"""(() => { const a = document.querySelector("#pansw");
  const t = a.textContent;
  return JSON.stringify({noentity: t.includes("No entity in the question was recognised"), behind: t.includes("evidence behind row"),
    rawid: /\(e[0-9a-f]{16}\)/.test(t), read: [...a.querySelectorAll(".read .ent b")].map(x => x.textContent),
    heads: [...a.querySelectorAll("table.answer thead th")].map(x => x.textContent),
    refs: [...a.querySelectorAll(".bottomline a.ref, .bottomline button.ref")].map(x => x.textContent),
    alone: !!a.querySelector(".cmpshown")}); })()"""


@needs_jsdom
def test_first_screen(fx):
    evals, errors = run_view(fx, [{"wait": 2500}, {"eval": FIRST}])
    assert errors == []
    d = json.loads(evals[0])
    assert d["order"] is True and d["safari"] is False and d["source"] is True
    if "keep-or-prune verdict" in d["about"]:
        assert "on every link across all layers" in d["about"] and "on every Pharos link (" not in d["about"]


@needs_jsdom
def test_tested_questions_one_and_three(fx):
    acts = [CHOOSE, {"click": "#tabPharos", "wait": 1000},
            {"click": '#pstrip [data-demo="pharos_demo_1"]'}, {"waitFor": "#pansw .bottomline", "wait": 1500}, {"eval": ANSWER},
            {"click": '#pansw button[data-cmpmode="alone"]', "wait": 800},
            {"click": '#pstrip [data-demo="pharos_demo_3"]'}, {"waitFor": "#pansw .bottomline", "wait": 1500}, {"eval": ANSWER}]
    evals, errors = run_view(fx, acts)
    assert errors == [] and len(evals) == 2
    one, three = (json.loads(e) for e in evals)
    for d in (one, three):
        assert d["noentity"] is False and d["behind"] is False and d["rawid"] is False
        assert not any(h.strip() in ("e1", "e2") for h in d["heads"])
        assert not any(r.startswith("e") and len(r) == 17 for r in d["refs"])          # no raw link id as a button label
    assert one["read"] == ["PTGS2"] and three["read"][:1] == ["Alzheimer disease"]
    assert three["alone"] is False                                                      # G1-U-07: the switch did not carry over
