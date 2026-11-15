"""PK3 (12 Nov): the front page in the owner's chosen direction (docs/ux/mockups_2026-11-11/CHOICE.md) with findings U3 and U6.

Static: the design tokens, the top navigation of the brief, the one answer component, the Numbers order, no graph codes or run
tags in what a person reads. Behaviour (node + jsdom with stubbed API answers taken from committed files, else skipped):
  - the first screen states the result with its number and "5 of 5 folds higher", read from the committed headline (U3);
  - "See a worked answer" opens the stored TULIP-1 report as one answer: tiles, blocks in the brief's order, graph, no summary
    block without `summary_text`, a summary block with row/claim links when it is there;
  - a long answer shows rows 1 to 12 and the rest behind one disclosure;
  - on a wide window "why this link" opens in the place of the answer's graph and closes back to it (Esc);
  - Numbers opens with the three-row table (+0.1235 / +0.1219, 39,513 pairs), pruning in the AC-05 order, every run under
    "For judges", and no graph code or run tag anywhere in its text.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
PAGE = ROOT / "pipelines" / "app_page_v3.html"
T = PAGE.read_text(encoding="utf-8")
JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node")
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(), reason="node + jsdom not available")


def test_design_tokens_and_navigation():
    for token in ("--s1:4px", "--s6:32px", "--t1:14px", "--t4:28px", "--prose:72ch", "#050a14", "#0a111f", "#0f1a2e", "#e6ecf5", "#9ca3af",
                  "#4ca6a8", "#b8a0c9", "#f9ab00", "#e3b68a", "#d98fa8", "#f7f8fb", "#0f172a", '"Saira"', '"Inter"'):
        assert token in T, token
    nav = re.search(r'<nav class="topnav" id="topnav".*?</nav>', T, re.S).group(0)
    assert re.findall(r">([A-Za-z ]+)</button>", nav) == ["Ask", "Examples", "Graph", "Numbers", "Downloads", "For judges", "About"]
    for shape in ("circle", "M6 .5 11.5 6 6 11.5 .5 6Z", "M6 1.5 11 10.5H1Z", 'stroke-dasharray="3 2"'):   # a shape with every strength
        assert shape in T


def test_first_screen_reading_order():
    order = [T.index(x) for x in ('id="hero"', 'id="seeworked"', 'id="asksec"', 'id="xsec"', 'id="tqsec"', 'id="numsec"', 'id="safarinote"', 'id="personaCards"')]
    assert order == sorted(order)
    assert ">See a worked answer</button>" in T and ">Ask your own question</button>" in T
    assert "Roles are files in" not in T                       # no file paths on the first screen (U3)


def test_answer_component_order_and_summary_label():
    assert '["question", "Question"], ["bottomline", "Bottom line"], ["summary", "Summary"], ["evidence", "Evidence"], ["graph", "Graph"]' in T
    assert "Summary written by a language model from the rows below" in T and "r.summary_text" in T
    for tile in ('["Answer"', '["Strength"', '["Sources"', '["What is not known"'):
        assert T.count(tile) == 2, tile                        # graph answers and trial reports
    assert "const FIRST = 12" in T and "Rows ${FIRST + 1} to ${n}" in T


def test_numbers_order_in_the_page():
    body = re.search(r"\nasync function showNumbers\(\) \{\n(.*?)\n\}\n", T, re.S).group(1)
    last = body[body.index('$("#numbers").innerHTML'):]
    seq = ["${lead}${meaning}${verdicts}${gradient}${spot}${limits}", "Standards: the schema with IRIs", "<h2>Downloads</h2>", "For judges: every run"]
    assert [last.index(s) for s in seq] == sorted(last.index(s) for s in seq)
    assert "EXPLORATORY unless marked" not in body


# ------------------------------------------------------------------ behaviour
RUNNER = r"""
const { JSDOM, VirtualConsole } = require("jsdom");
const fs = require("fs");
const [pagePath, url, stubsPath, actsJson, width] = process.argv.slice(2);
const STUBS = JSON.parse(fs.readFileSync(stubsPath, "utf8")), errors = [];
const vc = new VirtualConsole(); vc.on("jsdomError", e => errors.push(String(e.message || e))); vc.on("error", (...a) => errors.push(a.join(" ")));
const dom = new JSDOM(fs.readFileSync(pagePath, "utf8"), { url, runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
  Object.defineProperty(w, "innerWidth", { value: +width, configurable: true });
  w.fetch = async (u) => { const p = new URL(u, url).pathname;
    const key = Object.keys(STUBS).sort((a, b) => b.length - a.length).find(k => p === k || (k.endsWith("/") && p.startsWith(k)));
    const body = key ? STUBS[key] : {detail: "not stubbed"};
    return { ok: !!key, status: key ? 200 : 404, headers: { get: () => null }, json: async () => JSON.parse(JSON.stringify(body)) }; };
  w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  w.Element.prototype.scrollIntoView = function () {}; w.scrollTo = () => {};
  w.Element.prototype.getBoundingClientRect = function () { return this.classList && this.classList.contains("graphblk") ? { left: 980, top: 76, width: 440, height: 600, right: 1420, bottom: 676 } : { left: 0, top: 0, width: 0, height: 0, right: 0, bottom: 0 }; };
  w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.EventSource = class { constructor(u) {} addEventListener() {} close() {} };
  w.URL.createObjectURL = () => "blob:x";
  w.addEventListener("error", e => errors.push("window.error: " + e.message)); } });
const w = dom.window, d = w.document, sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  await sleep(600);
  const out = {};
  for (const a of JSON.parse(actsJson)) {
    if (a.click) { const el = d.querySelector(a.click); if (!el) errors.push("no element " + a.click); else el.click(); }
    if (a.key) d.dispatchEvent(new w.KeyboardEvent("keydown", { key: a.key, bubbles: true }));
    await sleep(a.wait || 300);
    if (a.eval) out[a.name] = w.eval(a.eval);
  }
  console.log(JSON.stringify({ errors, out })); process.exit(0);
})();
"""


def run_page(tmp_path, stubs, acts, url="http://x.test/", width=1024, examples=True):
    import persona_examples as PE
    import personas
    from kg_query import KGIndex
    html = T
    if examples:
        html = PE.inject(T, PE.build(KGIndex(), personas.load_personas()))
    (tmp_path / "page.html").write_text(html)
    (tmp_path / "s.json").write_text(json.dumps(stubs))
    (tmp_path / "run.js").write_text(RUNNER)
    r = subprocess.run([NODE, str(tmp_path / "run.js"), str(tmp_path / "page.html"), url, str(tmp_path / "s.json"), json.dumps(acts), str(width)],
                       capture_output=True, text=True, timeout=120, env=dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules")), cwd=str(JSDOM_DIR))
    assert r.returncode == 0, r.stderr[-2000:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["errors"] == [], res["errors"]
    return res["out"]


def base_stubs():
    import personas
    import headline
    from fastapi.testclient import TestClient
    import app as appmod
    c = TestClient(appmod.app)
    h = headline.headline(ROOT)
    return {"/api/personas": {"personas": personas.load_personas()}, "/api/wording": c.get("/api/wording").json(),
            "/api/landing": {"chips": [], "model": None, "backends": [], "backend_default": "local",
                             "headline": dict(text=h["text"], sentences=h["sentences"], files=h["files"])},
            "/api/graph_numbers": c.get("/api/graph_numbers").json(), "/api/attribution": c.get("/api/attribution").json()}


@needs_jsdom
def test_first_screen_states_the_result_from_the_headline(tmp_path):
    import headline
    g2 = headline.comparisons(ROOT)["paths_g2"]
    out = run_page(tmp_path, base_stubs(), [{"wait": 300, "name": "hero", "eval": "[$('#herogain').textContent, $('#heroline').textContent, $('#heroalso').textContent, $('[data-how=\"seed\"]').textContent].join('|')"}])
    gain, line, also, seed = out["hero"].split("|")
    assert gain == f"{g2['mean']:+.3f} AUROC · {g2['wins']} of {g2['n']} folds higher"
    assert line.startswith("On the held-out curated Pharos gene-disease pairs") and line.endswith(f"{g2['wins']} of {g2['n']} folds higher).")
    assert also.startswith("Also: ") and "pruned version" in also
    assert seed.startswith("26,462,802 Pharos links")


def stored(name):
    return json.loads((ROOT / "outputs" / "examples" / f"{name}.json").read_text())


@needs_jsdom
def test_worked_answer_is_one_component_with_tiles_and_an_optional_summary(tmp_path):
    x = stored("x05")
    meta = {k: x[k] for k in ("id", "question", "kind", "endpoint", "recorded", "backend", "model")}
    stubs = dict(base_stubs(), **{"/api/stored_answers": {"answers": [meta]}, "/api/stored_answer/": {"answer": x["answer"]}})
    acts = [{"click": "#seeworked", "wait": 1200, "name": "a", "eval": "JSON.stringify({stages: [...document.querySelectorAll('#out [data-stage]')].map(e => e.dataset.stage), "
             "tiles: [...document.querySelectorAll('#out .tiles4 a')].map(a => [a.querySelector('h4').textContent, !!document.getElementById(a.dataset.jump)]), "
             "summary: !!document.querySelector('#out .summaryblk'), q: document.querySelector('#out .msg.user .q').textContent, dock: document.querySelector('#out .ans').className, "
             "sections: document.querySelectorAll('#out section.card[data-role]').length, graph: document.querySelectorAll('#out svg[data-ansgraph] line.e').length})"}]
    a = json.loads(run_page(tmp_path, stubs, acts)["a"])
    assert a["stages"] == ["question", "bottomline", "graph", "evidence", "limits", "downloads"] and "TULIP-1" in a["q"]
    assert [t[0] for t in a["tiles"]] == ["Answer", "Strength", "Sources", "What is not known"] and all(t[1] for t in a["tiles"])
    assert a["summary"] is False and "hasdock" in a["dock"] and a["sections"] == 4 and a["graph"] > 0
    ans = dict(x["answer"], summary_text="The primary endpoint was missed [1] and the later trial was positive [3].")
    stubs["/api/stored_answer/"] = {"answer": ans}
    acts[0]["eval"] = ("JSON.stringify({label: document.querySelector('#out .summaryblk .by').textContent, refs: [...document.querySelectorAll('#out .summaryblk .ref')].map(b => b.dataset.claim), "
                       "stages: [...document.querySelectorAll('#out [data-stage]')].map(e => e.dataset.stage)})")
    s = json.loads(run_page(tmp_path, stubs, acts)["a"])
    assert s["label"] == "Summary written by a language model from the rows below" and s["refs"] == ["1.1", "1.3"]
    assert s["stages"] == ["question", "bottomline", "graph", "summary", "evidence", "limits", "downloads"]


def demo_answer(n_rows):
    import answer_summary as S
    facts, rows = {}, []
    for i in range(n_rows):
        eid = f"e{i:016x}"
        facts[eid] = dict(id=eid, subject="NCBIGene:5743", subject_label="PTGS2", predicate="biolink:gene_associated_with_condition", phrase="is associated with",
                          object=f"MONDO:{i:07d}", object_label=f"disease {i}", source="CTD", source_record="r", layer="pharos", source_version="v",
                          knowledge_level="knowledge_assertion", strength="curated", kind="curated", activity=None, activity_type=None, evidence_type=None,
                          verdict="pruned" if i == 0 else "kept", confidence=0.6, reasons="plausibility=0.2", reasons_words="plausibility 0.20")
        rows.append({"disease": f"disease {i}", "evidence": eid})
    d = dict(id="d1", question="Which diseases?", why="one hop", passed=True, note="", columns=["disease", "evidence"], rows=rows,
             evidence=list(facts), cypher="MATCH (g:Gene {name:'PTGS2'})-[r]->(d:Disease) RETURN d.name AS disease, r.id AS evidence")
    d["summary"] = S.pharos_summary(d, facts)
    return d


@needs_jsdom
def test_long_answer_folds_after_row_12_and_why_this_link_docks_on_a_wide_window(tmp_path):
    demo = demo_answer(20)
    explain = dict(id="e0000000000000000", statement="s", subject=dict(id="NCBIGene:5743", name="PTGS2"), object=dict(id="MONDO:0000000", name="disease 0"),
                   predicate="biolink:gene_associated_with_condition", provenance=dict(source="CTD", knowledge_level="knowledge_assertion"),
                   properties={}, verdict=dict(verdict="prune", confidence="0.2", reasons="plausibility=0.2"), corroboration=[], timeline=dict(events=[], summary=None))
    stubs = dict(base_stubs(), **{"/api/pharos/demo/": demo, "/api/pharos/demo_questions": [dict(id="d1", question="Which diseases?", why="one hop")],
                                  "/api/pharos/explain/": explain, "/api/pharos/status": {"nodes": 2, "edges": 1, "layers": ["pharos"], "verdicts": {"keep": 1, "prune": 0}, "predictions": None}})
    acts = [{"click": "#tqlist [data-tq]", "wait": 900, "name": "rows", "eval": "JSON.stringify({first: document.querySelectorAll('#pansw table[data-answer] tbody:not(.more) tr').length, "
             "more: document.querySelectorAll('#pansw tbody.more tr').length, hidden: document.querySelector('#pansw tbody.more').hidden, btn: document.querySelector('[data-morerows]').textContent})"},
            {"click": "[data-morerows]", "wait": 200, "name": "open", "eval": "document.querySelector('#pansw tbody.more').hidden"},
            {"click": "#pansw table[data-answer] a.chip", "wait": 600, "name": "docked", "eval": "JSON.stringify({dock: document.body.classList.contains('dockmode'), left: $('#drawer').style.left, "
             "width: $('#drawer').style.width, open: !$('#drawer').hidden, overlay: $('#overlay').hidden, lead: $('#pexplain .lead').textContent})"},
            {"key": "Escape", "wait": 300, "name": "closed", "eval": "JSON.stringify({dock: document.body.classList.contains('dockmode'), open: !$('#drawer').hidden})"}]
    out = run_page(tmp_path, stubs, acts, width=1440)
    rows = json.loads(out["rows"])
    assert rows == {"first": 12, "more": 8, "hidden": True, "btn": "Rows 13 to 20"} and out["open"] is False
    dock = json.loads(out["docked"])
    assert dock["dock"] and dock["open"] and dock["overlay"] and dock["left"] == "980px" and dock["width"] == "440px" and "PTGS2" in dock["lead"]
    assert json.loads(out["closed"]) == {"dock": False, "open": False}
    narrow = run_page(tmp_path, stubs, acts[:1] + acts[2:3], width=1024)          # a narrower window keeps the drawer with its overlay
    d2 = json.loads(narrow["docked"])
    assert d2["dock"] is False and d2["open"] and d2["overlay"] is False


@needs_jsdom
def test_numbers_lead_table_order_and_words(tmp_path):
    from fastapi.testclient import TestClient
    import app as appmod
    stubs = dict(base_stubs(), **{"/api/results": TestClient(appmod.app).get("/api/results").json()})
    acts = [{"click": "#navNumbers", "wait": 1200, "name": "n", "eval": "JSON.stringify({h2: [...document.querySelectorAll('#numbers h2')].map(h => h.textContent), "
             "rows: [...document.querySelectorAll('#num-lead tbody tr')].map(r => [...r.cells].map(c => c.textContent.trim()).join(' ').replace(/\\s+/g, ' ')), text: $('#numbers').textContent, "
             "judges_open: $('#num-judges').open, current: $('#navNumbers').getAttribute('aria-current')})"}]
    n = json.loads(run_page(tmp_path, stubs, acts)["n"])
    h2 = n["h2"]
    want = ["The result in three rows", "What it does and does not mean", "Pruning: a verdict on every link", "Pruning: does the confidence mean anything?",
            "Pruning: blinded spot check", "Limits", "Standards: the schema with IRIs", "Downloads"]
    assert [h for h in h2 if h in want] == want and h2.index("Limits") < h2.index("The result in one paragraph")
    assert n["rows"][0].startswith("Pharos alone 0.6614") and "+0.1235" in n["rows"][1] and "+0.1219" in n["rows"][2] and "5 of 5" in n["rows"][2]
    assert "39,513 held-out curated gene-disease pairs" in n["text"] and n["judges_open"] is False and n["current"] == "page"
    assert "AUPRC 0.005" in n["text"] and n["text"].index("Pruning: blinded spot check") < n["text"].index("AUPRC 0.005")      # AC-05: the limit comes last
    for bad in (r"\bG[0-4]R?\b", r"@frozen", r"\bfrozen_a\b", r"\bdiag_", r"\bcs9\b", r"EXPLORATORY", r"outputs/"):
        assert not re.search(bad, n["text"]), bad


def test_every_inventory_control_is_listed_and_exists():
    """The PK3 deliverable: every control of the 9 Nov click inventory still exists on the front page, with its new place."""
    sys.path.insert(0, str(ROOT / "tests"))
    import test_click_inventory as CI
    doc = (ROOT / "docs" / "ux" / "PK3_CONTROLS.md").read_text(encoding="utf-8")
    rows = [r for r in CI.rows() if r[0] in ("/ + /v3", "/v3")]
    listed = [l for l in doc.splitlines() if re.match(r"\| \d+ \|", l)]
    assert len(listed) == len(rows) >= 50 and "**NO**" not in doc
    for (page, sel, *_), line in zip(rows, listed):
        assert f"| {sel} |" in line
        for i in re.findall(r"#([A-Za-z][\w-]*)", sel):
            assert f'id="{i}"' in T or f"#{i}" in T, (sel, i)
    for new in ('id="seeworked"', 'id="askown"', 'id="tqlist"', 'id="xalso"', 'id="topnav"', 'id="themesw"', "data-morerows", "data-ansjson", "data-gsec"):
        assert new in T, new
