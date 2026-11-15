"""FIX2 (Gate 2, 14 Nov), the rendered parts (node + jsdom + the real app over bridge.py, on the Pharos test fixture; skipped
without jsdom):
  F2: the Numbers tab draws the spot-check FAIL with its counts, the separate machine audit under it, and links named
      "the result" and "the rule" (not "the pre-registered rule" on the result file);
  F3: under every independent-check judgement in the drawer, one muted line on how often the automated raters agreed with the
      scientist (both numbers read from the committed result file by the server, none typed into the page).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import numbers_page as N  # noqa: E402
import routes_audit as ra  # noqa: E402
import test_routes_audit as TA  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402
from test_page_pk6 import CHOOSE, needs_jsdom, run_view  # noqa: E402

PAGE = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return build(tmp_path_factory.mktemp("fix2"))


def test_static_the_spot_check_links_are_named_for_what_they_open():
    assert '"the pre-registered rule"' not in PAGE
    assert '"the result"' in PAGE and '"the rule"' in PAGE and 'id="num-audit"' in PAGE and "data-ratersnote" in PAGE


@needs_jsdom
def test_numbers_tab_draws_the_fail_and_the_audit_sentence(fx):
    sc, au = N.spot_check([ROOT]), N.audit_sample([ROOT])
    if not sc or not sc["result"] or not au:
        pytest.skip("the scored result files are not in this checkout")
    probe = {"eval": r"""JSON.stringify({spot: (document.querySelector('#num-spot p') || {textContent: ''}).textContent,
      audit: (document.querySelector('#num-audit') || {textContent: ''}).textContent,
      links: [...document.querySelectorAll('#num-spot a')].map(a => a.textContent)})"""}
    evals, errors = run_view(fx, [CHOOSE, {"click": "#tabNumbers", "wait": 3000}, probe])
    assert errors == []
    d = json.loads(evals[0])
    assert ": FAIL." in d["spot"] and f"she agreed with them on {sc['owner_agree']}," in d["spot"]
    assert f"{au['pruned_unsupported']:,} of the {au['pruned_n']:,} pruned links" in d["audit"]
    assert "Within protein-protein rows from STRING" in d["audit"]
    assert d["links"][:2] == ["the result", "the rule"] and "the audit result" in d["links"]


@needs_jsdom
def test_drawer_judgement_carries_the_raters_agreement_line(fx):
    note = ra.raters_note([ROOT])
    if not note:
        pytest.skip("no spot-check result in this checkout")
    TA.write_stored(fx / "data" / "audit" / "edges", "e0000000000000001")
    line = {"eval": "(document.querySelector('#pexplain section.audit [data-ratersnote]') || {textContent: 'none'}).textContent"}
    evals, errors = run_view(fx, [CHOOSE, {"type": ["#pq", "PTGS2"]}, {"submit": "#pf"}, {"waitFor": "#pcardsec a.why", "timeout": 20000, "wait": 800},
                                  {"click": '#pcardsec a.why[data-eid="e0000000000000001"]', "wait": 1500},
                                  {"click": "#pexplain [data-auditgo]", "wait": 1500}, line])
    assert errors == [] and evals[0] == note
    assert note.startswith("Automated raters. On the ") and "rated blind, they agreed with her on" in note
