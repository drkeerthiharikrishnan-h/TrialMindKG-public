"""PK15 B4 / B7: the Amazon Neptune control on the front page (pipelines/app_page_v3.html, the PK15 blocks).

Static (no browser): the page code is in ONE marked HTML block (which replaced the hidden graph backend select) and ONE marked
script block; nothing Neptune-specific outside them; the control has a phone layout rule and live regions.
Rendered (node + jsdom + the real app over bridge.py with NEPTUNE_FAKE, on the Pharos test fixture; skipped without jsdom):
  (a) available: "Run this on Amazon Neptune" -> one line with the counts; the answer keeps its rows; R11 and axe stay clean;
      the "Runs on" line names Amazon Neptune and AWS (and not the model, which is the offline stand-in here);
  (b) asleep: "Wake it (about 4 minutes)" -> waking -> available -> the question runs there by itself;
  (c) a Neptune error: the local answer stays, a plain note, no button, no error text;
  (d) not set up: no control and no "Runs on" line in the DOM;
  (e) the Numbers tab prints the sentence of outputs/neptune/live_parity.json when the server has it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
from fixtures.pharos_fixture import build  # noqa: E402
from test_page_pk6 import R11, R11_JS  # noqa: E402,F401

PAGE = ROOT / "pipelines" / "app_page_v3.html"
T = PAGE.read_text(encoding="utf-8")
H = ROOT / "docs" / "ux" / "harness_v3"
JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node")
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(), reason="node + jsdom not available")
HTML_OPEN, HTML_CLOSE = "<!-- PK15 Neptune -->", "<!-- /PK15 Neptune -->"
JS_OPEN = "// ================================================================ PK15 Neptune (14 Nov)"
JS_CLOSE = "// ================================================================ end of the PK15 Neptune block"


def blocks():
    h0, h1 = T.index(HTML_OPEN), T.index(HTML_CLOSE) + len(HTML_CLOSE)
    j0, j1 = T.index(JS_OPEN), T.index(JS_CLOSE) + len(JS_CLOSE)
    return T[h0:h1], T[j0:j1], T[:h0] + T[h1:j0] + T[j1:]


# ------------------------------------------------------------------------------------------------------------------ static
def test_one_marked_html_block_and_one_marked_script_block():
    for m in (HTML_OPEN, HTML_CLOSE, JS_OPEN, JS_CLOSE):
        assert T.count(m) == 1, m
    assert T.index(HTML_OPEN) < T.index(HTML_CLOSE) < T.index(JS_OPEN) < T.index(JS_CLOSE)
    assert 'id="backendsel"' not in T                                  # the hidden select the packet replaced


def test_nothing_neptune_specific_outside_the_blocks():
    html, js, rest = blocks()
    for name in ("nepAttach", "nepRun", "nepWake", "nepPoll", "nepRunsOn", "nepNumbers", "/api/neptune", "nepctl", "data-nep", "runson",
                 "Run this on Amazon Neptune"):
        assert name not in rest, name
    assert "/api/neptune/status" in js and "/api/neptune/wake" in js and ".nepctl" in html


def test_the_control_is_accessible_and_fits_a_phone():
    html, js, _ = blocks()
    assert 'aria-live="polite"' in js and 'role", "group"' in js and 'type="button"' in js
    assert re.search(r"@media \(max-width:640px\) \{ \.nepctl button \{ flex:1 1 100%; \} \}", html)
    assert "askBegin(\"pharos\", q, btn, box, \"Running on Amazon Neptune\")" in js        # busy state, status line, scrollIntoView
    assert "NEP_POLL_MS = 10000, NEP_POLL_MAX_MS = 15 * 60000" in js                         # every 10 s, at most 15 minutes


def test_the_page_never_prints_identical_by_itself():
    _, js, _ = blocks()
    assert "identical" not in js                                     # the comparison sentence comes from the server's numbers


# ---------------------------------------------------------------------------------------------------------------- rendered
@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return build(tmp_path_factory.mktemp("pk15"))


def run_view(fx_root, acts, fake):
    """Runs the front page in jsdom against the real app (bridge.py) with the fixture graph, the offline model and NEPTUNE_FAKE=fake.
    Returns the parsed run_page_v3.js output."""
    env = dict(os.environ, NODE_USE_ENV_PROXY="1", NODE_PATH=str(JSDOM_DIR / "node_modules"), TMKG_LLM_BACKEND="offline",
               TMKG_PHAROS_ROOT=str(fx_root), TMKG_PHAROS_INDEX="off", TMKG_NIM_PREFLIGHT="off", TMKG_WARM="off", NEPTUNE_FAKE=fake)
    env.pop("NEPTUNE_ENDPOINT", None)
    with tempfile.TemporaryDirectory() as td:     # actions go in a file, not on the command line (see run_page_v3.js)
        acts_file = Path(td) / "acts.json"
        acts_file.write_text(json.dumps(acts), encoding="utf-8")
        r = subprocess.run([NODE, str(H / "run_page_v3.js"), PY, str(H / "bridge.py"), "/", "", "@" + str(acts_file)],
                           capture_output=True, text=True, timeout=400, cwd=str(JSDOM_DIR), env=env)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:                   # node died before printing its JSON: say why instead of a bare decode error
        pytest.fail(f"page runner printed no JSON (exit code {r.returncode}; -9 = killed by the system or a sandbox). "
                    f"node stderr: {r.stderr[-3000:] or '(empty)'} | stdout: {r.stdout[:500] or '(empty)'}")
    assert "harness_error" not in d, d.get("harness_error")
    return d


CHOOSE = {"click": '.persona[data-id="physician_scientist"]', "wait": 1500}
ANSWER = [CHOOSE, {"click": "#tabPharos", "wait": 800}, {"click": '#pstrip [data-demo="pharos_demo_1"]'},
          {"waitFor": "#pansw .bottomline", "timeout": 60000, "wait": 800}]
STATE = {"eval": "JSON.stringify({state: (document.querySelector('#pansw .nepctl') || {dataset: {}}).dataset.state || null,"
                 " control: (document.querySelector('#pansw .nepctl') || {textContent: ''}).textContent.replace(/\\s+/g, ' ').trim(),"
                 " buttons: [...document.querySelectorAll('#pansw .nepctl button')].map(b => b.textContent),"
                 " live: !!document.querySelector('#pansw .nepctl [aria-live=polite]'),"
                 " rows: document.querySelectorAll('#pansw table[data-answer] tr[data-row]').length,"
                 " line: document.querySelector('#paskline').className + ': ' + document.querySelector('#paskline').textContent.replace(/\\s+/g, ' ').trim(),"
                 " runson: (document.querySelector('#runson') || {textContent: null}).textContent,"
                 " err: document.querySelectorAll('#pansw .nepctl .err, #pansw .nepctl .rejected').length})"}
FIRST = {"eval": "(document.querySelector('#runson') || {textContent: 'none'}).textContent"}


def states(d):
    return [json.loads(e) for e in d["evals"] if e.startswith("{")]


@needs_jsdom
def test_a_available_run_on_neptune_prints_the_counts(fx):
    acts = [{"wait": 1200}, FIRST, *ANSWER, {"waitFor": '#pansw .nepctl [data-nep="run"]', "wait": 300}, STATE,
            {"click": '#pansw .nepctl [data-nep="run"]'}, {"waitFor": '#pansw .nepctl[data-state="available"] .nepline:not(:empty)', "timeout": 30000, "wait": 500},
            STATE, R11, {"axe": "neptune_answer"}]
    d = run_view(fx, acts, "available")
    assert d["errors"] == [], d["errors"]
    assert d["evals"][0] == "Runs on · Graph database: Amazon Neptune. Hosted on AWS."      # the model is the stand-in here
    before, after = states(d)
    assert before["state"] == "available" and before["buttons"] == ["Run this on Amazon Neptune"] and before["live"]
    assert re.fullmatch(r"Run this on Amazon NeptuneSame question on Amazon Neptune: \d+ rows? in (?:under )?\d\.\d s; (\d+) of \1 evidence links? identical\.",
                        after["control"]), after["control"]
    assert after["rows"] == before["rows"] > 0                       # the answer on screen stays
    assert after["line"].startswith("askline ok:") and "Same question on Amazon Neptune" in after["line"]
    r11 = json.loads(d["evals"][3])
    assert r11[0] == 0, r11                                           # no developer word, the sponsor names allowed
    assert "POST /api/pharos/ask" in d["api_calls_last"]


@needs_jsdom
def test_b_asleep_wake_then_the_question_runs_by_itself(fx):
    acts = [*ANSWER, {"waitFor": '#pansw .nepctl [data-nep="wake"]', "wait": 300}, STATE,
            {"click": '#pansw .nepctl [data-nep="wake"]', "wait": 800}, STATE,
            {"waitFor": '#pansw .nepctl[data-state="available"] .nepline:not(:empty)', "timeout": 60000, "wait": 500}, STATE, R11]
    d = run_view(fx, acts, "asleep")
    assert d["errors"] == [], d["errors"]
    asleep, waking, done = states(d)
    assert asleep["state"] == "asleep" and asleep["buttons"] == ["Wake it (about 4 minutes)"]
    assert ("This answer comes from this server's copy of the graph. The same question can also run on Amazon Neptune "
            "(asleep to save cost; waking takes about 4 minutes).") in asleep["control"]                      # FIX2 F5 (G2-U-02)
    assert asleep["runson"] == "Runs on · Graph database: Amazon Neptune (the full graph; asleep, woken on request). Hosted on AWS."
    assert waking["state"] == "waking" and waking["buttons"] == [] and "waking up (about 4 minutes)" in waking["control"]
    assert done["state"] == "available" and "Same question on Amazon Neptune" in done["control"]
    assert asleep["rows"] == waking["rows"] == done["rows"] > 0      # the local answer stays on screen the whole time
    calls = d["api_calls_first"] + d["api_calls_last"]
    assert "POST /api/neptune/wake" in calls and calls.count("GET /api/neptune/status") >= 2
    assert json.loads(d["evals"][-1])[0] == 0


@needs_jsdom
def test_c_a_neptune_error_keeps_the_local_answer_with_a_note(fx):
    acts = [*ANSWER, {"waitFor": '#pansw .nepctl [data-nep="run"]', "wait": 300}, STATE, {"click": '#pansw .nepctl [data-nep="run"]'},
            {"waitFor": '#pansw .nepctl[data-state="unavailable"]', "timeout": 30000, "wait": 500}, STATE]
    d = run_view(fx, acts, "error")
    assert d["errors"] == [], d["errors"]
    before, after = states(d)
    assert after["state"] == "unavailable" and after["buttons"] == [] and after["err"] == 0
    assert after["control"] == "Amazon Neptune could not run this question just now, so the answer comes from this server's graph."
    assert "500" not in after["control"] and "error" not in after["control"].lower() and after["rows"] == before["rows"] > 0
    assert after["line"].startswith("askline ok:")


@needs_jsdom
def test_d_not_configured_draws_nothing(fx):
    d = run_view(fx, [{"wait": 1200}, FIRST, *ANSWER, {"wait": 800}, STATE], "not_configured")
    assert d["errors"] == []
    st = states(d)[0]
    assert d["evals"][0] == "none" and st["state"] is None and st["control"] == "" and st["runson"] is None and st["rows"] > 0
    assert not any("/api/neptune" in c for c in d["api_calls_first"] + d["api_calls_last"])     # not even a status read


@needs_jsdom
def test_e_numbers_tab_prints_the_live_load_sentence(fx):
    import routes_neptune as RN
    p = Path(fx) / RN.LIVE_PARITY
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"loaded": {"nodes": 12, "links": 34}, "manifest": {"nodes": 12, "links": 34}, "counts_equal": True,
                             "pinned": [{"id": "F01", "digest_equal": True}, {"id": "F02", "digest_equal": False}]}))
    # FIX2 F7 / S17 item 4, a fixture bug: the bridge also reads this repository as a fallback root, and on the Mac its untracked
    # export manifest (the real counts) disagreed with the fixture's file, so no sentence was drawn. A manifest that matches,
    # in the fixture (the first root), is the one read.
    m = Path(fx) / RN.MANIFEST
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"counts": {"nodes": 12, "edges": 34}}))
    try:
        d = run_view(fx, [CHOOSE, {"click": "#tabNumbers", "wait": 3000},
                          {"eval": "(document.querySelector('#num-neptune') || {textContent: 'none'}).textContent.replace(/\\s+/g, ' ').trim()"}], "not_configured")
    finally:
        p.unlink()
        m.unlink()
    assert d["errors"] == []
    assert d["evals"][0] == ("The graph in Amazon NeptuneThe whole graph is loaded in Amazon Neptune: 12 nodes and 34 links, equal to the "
                             "download. 1 of 2 pinned questions return the same evidence there.")


@needs_jsdom
def test_f_a_wake_the_host_still_reports_stopped_stays_waking(fx):
    """S17 item 2 (the flicker seen live on 14 Nov): the host takes the wake request but still reports "stopped" for two status
    reads. The page must stay on "waking" (no second "Wake it" button) and then run the question there by itself."""
    acts = [*ANSWER, {"waitFor": '#pansw .nepctl [data-nep="wake"]', "wait": 300}, {"click": '#pansw .nepctl [data-nep="wake"]', "wait": 800}, STATE,
            {"wait": 11000}, STATE, {"waitFor": '#pansw .nepctl[data-state="available"] .nepline:not(:empty)', "timeout": 60000, "wait": 500}, STATE]
    d = run_view(fx, acts, "asleep_flicker")
    assert d["errors"] == [], d["errors"]
    first, after_poll, done = states(d)
    assert first["state"] == "waking" and first["buttons"] == []
    assert after_poll["state"] == "waking" and after_poll["buttons"] == []        # the host said "stopped": no flicker back to asleep
    assert done["state"] == "available" and "Same question on Amazon Neptune" in done["control"]
