"""Checks for the v2 page prototype (pipelines/app_page_v2.html, 8 Nov 2026). Owner decision 11 Nov: the page is no longer
served (the /v2 route is removed); the file stays in the repository and these checks keep it loadable.

Static checks (always run): the page is served-able UTF-8 HTML; every /api/... URL it uses matches a route declared in
pipelines/app.py (with the right HTTP method); the ids the page and the browser harness rely on exist once; no external
http(s) asset is loaded; the shared-link router does not depend on a role.
Behaviour checks (run when node + jsdom are available, else skipped): a first-time visitor opening #pharos/node/<id> or
?node=<id> gets the card without choosing a role, the card's first view is the short one, "Show all" and "Why?" work.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "pipelines" / "app_page_v2.html"
APP = ROOT / "pipelines" / "app.py"


def page_text():
    return PAGE.read_text(encoding="utf-8")


def script_text():
    m = re.search(r"<script>(.*)</script>", page_text(), re.S)
    assert m, "the page has one inline script"
    return m.group(1)


def routes():
    """{(method, path)} declared with @app.get / @app.post in app.py (read as text: importing app.py loads the graph)."""
    return {(m.upper(), p) for m, p in re.findall(r'@app\.(get|post)\("([^"]+)"', APP.read_text(encoding="utf-8"))}


def route_matches(literal, paths):
    """A URL literal from the page (cut at '?', '${' or a quote) matches a route if it equals the route, or equals the
    route's fixed part before its first {parameter} (the page appends the parameter)."""
    for p in paths:
        if literal == p or ("{" in p and literal == p.split("{", 1)[0]):
            return True
        if "{" in p and re.fullmatch(re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(p)), literal):
            return True
    return False


def api_literals(js):
    """Every '/api/...' string the script uses, cut where a variable part starts."""
    out = set()
    for m in re.finditer(r'["`](/api/[^"`$?]*)', js):
        out.add(m.group(1))
    return out


# ------------------------------------------------------------------ static checks
def test_page_is_servable_html():
    text = page_text()
    assert text.lstrip().lower().startswith("<!doctype html>")
    assert text.rstrip().endswith("</html>")
    assert '<meta charset="utf-8">' in text and 'name="viewport"' in text
    responses = pytest.importorskip("fastapi.responses")
    r = responses.HTMLResponse(text)                     # what app.home() returns for the current page
    assert r.status_code == 200 and r.body.decode("utf-8") == text


def test_every_api_url_matches_a_route():
    js, rs = script_text(), routes()
    paths = {p for _, p in rs}
    lits = api_literals(js)
    assert len(lits) >= 15, lits
    missing = sorted(lit for lit in lits if not route_matches(lit, paths))
    assert not missing, f"URLs with no route in app.py: {missing}"


def test_http_methods_match_routes():
    js, rs = script_text(), routes()
    posts = set(re.findall(r'(?:fetch|getJSON)\("(/api/[^"]+)",\s*\{method:\s*"POST"', js))
    assert posts == {"/api/pharos/ask", "/api/pharos/subgraph.zip"}
    for p in posts:
        assert ("POST", p) in rs
    # everything else is read with GET (fetch without a method, EventSource, links, location.href)
    gets = {p for m, p in rs if m == "GET"}
    for lit in api_literals(js) - posts:
        assert route_matches(lit, gets), lit


def test_event_source_uses_the_stream_route():
    js = script_text()
    urls = re.findall(r'new EventSource\("([^"?]+)', js)
    assert urls == ["/api/ask_stream"]
    assert ("GET", "/api/ask_stream") in routes()


def test_only_existing_endpoints_and_nothing_new_on_the_server():
    """The prototype must not need a server change: it uses no route app.py lacks; app.py still serves the first page
    (at /classic since 11 Nov) and no longer names this file."""
    src = APP.read_text(encoding="utf-8")
    assert '"app_page.html"' in src and "app_page_v2" not in src


KEY_IDS = ["hq", "hf", "hchips", "hres", "hstat", "sq", "hsearch", "menuBtn", "menu", "personasel", "techchk",
           "v-home", "v-card", "v-ask", "v-trials", "v-explore", "v-numbers", "v-generalize", "v-judges", "v-build",
           "cardbody", "drawer", "overlay", "pexplain", "dclose", "dback", "aq", "askf", "demoq", "ans", "backendsel",
           "tq", "tf", "tchips", "tout", "xq", "xf", "eg"]


def test_key_ids_exist_once():
    text = page_text()
    ids = re.findall(r'\sid="([^"$]+)"', text)
    for i in KEY_IDS:
        assert ids.count(i) == 1, f"id {i} appears {ids.count(i)} times"
    # the card picture is drawn into svg#cg (the harness reads #cg) and the drawer body is #pexplain
    assert 'id="cg"' in script_text() and "#pexplain" in script_text()


def test_no_external_assets():
    text = page_text()
    assert not re.search(r'\b(?:src|href)\s*=\s*["\']\s*(?:https?:)?//', text, re.I), "an external src/href"
    assert not re.search(r'url\(\s*["\']?\s*(?:https?:)?//', text, re.I), "an external CSS url()"
    assert not re.search(r"@import", text, re.I)
    assert not re.search(r"<script[^>]+\bsrc=", text, re.I)
    assert not re.search(r"<link[^>]+stylesheet", text, re.I)
    assert not re.search(r'fetch\(\s*["`]https?:', script_text())


def test_dark_mode_mobile_and_focus_styles_present():
    text = page_text()
    assert "prefers-color-scheme: dark" in text
    assert "max-width: 760px" in text
    assert ":focus-visible" in text
    assert "min-height:44px" in text and "pointer: coarse" in text
    assert "prefers-reduced-motion" in text


def function_body(js, name):
    m = re.search(r"\nfunction " + re.escape(name) + r"\([^)]*\) \{\n(.*?)\n\}\n", js, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


def test_shared_link_routing_needs_no_role():
    js = script_text()
    body = function_body(js, "route")
    assert '"pharos" && parts[1] === "node"' in body and "openCard(" in body
    assert 'get("node")' in body                         # ?node=<id> links (the old Copy-link format) open too
    assert "PERSONAS" not in body and not re.search(r"\bP\b", body), "route() must not depend on a role"
    boot = function_body(js, "boot")
    assert boot.index("route();") < boot.index('"/api/personas"'), "route() runs before roles are fetched"
    m = re.search(r"\nasync function openCard\(id\) \{\n(.*?)\n\}\n", js, re.S)
    assert m and "PERSONAS" not in m.group(1) and not re.search(r"\bP\b", m.group(1))


# ------------------------------------------------------------------ behaviour (jsdom, optional)
JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(),
                                 reason="node + jsdom not available (set TMKG_JSDOM_DIR)")

EID = "e0123456789abcdef"
ROW = lambda i, word: dict(id=f"e000000000000000{i}", subject="MONDO:0005148", predicate="biolink:gene_associated_with_condition",
                           object=f"NCBIGene:{i}", statement="x", layer="pharos", source="CTD", source_version="v1",
                           knowledge_level="knowledge_assertion", verdict="keep" if word == "kept" else "prune", confidence=0.5,
                           other=f"NCBIGene:{i}", other_label=f"GENE{i}", other_category="biolink:Gene", outgoing=False,
                           phrase="is associated with", sentence=f"GENE{i} is associated with test disease", word=word,
                           level_words="curated statement", kind="curated")
CARD = dict(
    about=dict(id="MONDO:0005148", label="test disease", category="biolink:Disease", type_word="disease", ids=["MONDO:0005148"], properties={}),
    summary=dict(known=3, kinds=1, kept=2, pruned=1, predicted=1, layers=["pharos"]),
    known=[dict(kind="curated", meaning="a curated database statement", color="#1e7b4f", n=3, n_kept=2, n_pruned=1,
                rows=[ROW(1, "kept"), ROW(2, "kept"), ROW(3, "pruned")])],
    predicted=[dict(subject="NCBIGene:9", object="MONDO:0005148", other="NCBIGene:9", other_label="GENE9", other_category="biolink:Gene",
                    score=1.5, rank=1, graph="G3", task="B", known_in=[], model="meta-path model",
                    paths=[dict(via="NCBIGene:1", via_label="GENE1", shape="Gene -x-> Disease",
                                edges=[dict(id=EID, statement="GENE9 -genetically_associated_with-> test disease")])])],
    prediction_note=None,
    picture=dict(nodes=[dict(id="MONDO:0005148", label="test disease", category="biolink:Disease"),
                        dict(id="NCBIGene:1", label="GENE1", category="biolink:Gene")],
                 edges=[dict(id="e0000000000000001", source="NCBIGene:1", target="MONDO:0005148", predicate="biolink:gene_associated_with_condition",
                             evidence_type="curated", predicted=False, word="kept")]),
    mechanism_neighbours=None, served_from="card index", elapsed_ms=1.0)
EXPLAIN = dict(id=EID, statement="GENE9 -genetically_associated_with-> test disease",
               subject=dict(id="NCBIGene:9", name="GENE9"), object=dict(id="MONDO:0005148", name="test disease"),
               predicate="biolink:genetically_associated_with", provenance=dict(source="TIGA", source_record="tiga|x", knowledge_level="statistical_association"),
               properties={}, verdict=dict(verdict="keep", confidence="0.6"), corroboration=[],
               timeline=dict(events=[], summary=None))
REPORT = dict(question="Why did TEST-1 fail?", label=None, run=dict(note="Offline run.", seconds=1.5), seeds=["TEST-1"],
              sections=[dict(role="failure_diagnostician", title="Failure Diagnosis", note="",
                             claims=[dict(claim="The endpoint was missed.", evidence_status="post_hoc", support_status="supported", uncertainty="low",
                                          indicators=dict(evidence_strength="curated", mapping_confidence="high"),
                                          evidence=[dict(id="E1", source="CT.gov", source_record="NCT0")])],
                             subgraph=dict(nodes=2, edges=1),
                             graph=dict(nodes=[dict(id="A", label="A", category="biolink:ClinicalTrial"), dict(id="B", label="B", category="biolink:Drug")],
                                        edges=[dict(id="E1", source="A", target="B", predicate="biolink:related_to", evidence_type="registry_record")])),
                        dict(role="redesign_advisor", title="Evidence-Supported Redesign Options", note="No claim survived.", claims=[],
                             subgraph=dict(nodes=0, edges=0), graph=None)],
              rejected=[dict(role="redesign_advisor", claim="An unsupported idea.", reasons=["no evidence cited"])],
              flow_links=[])
STUBS = {"/api/pharos/card/": CARD, "/api/pharos/explain/": EXPLAIN, "/api/trials_for": {"trials": []},
         "/api/wording": {"predicates": {"genetically_associated_with": "is genetically associated with"}, "knowledge_levels": {},
                          "categories": {"Disease": "disease", "Gene": "gene"}, "evidence_kinds": [], "confidence_words": {"kept": "passed the pruning check"}},
         "/api/landing": {"chips": [{"kind": "card", "text": "test disease", "label": "Open a card"}], "model": None, "backends": [], "backend_default": "local"},
         "/api/personas": {"personas": [{"id": "r1", "name": "Role one", "emoji": "x", "color": "#000", "description": "d",
                                         "show_ids": "never", "questions": []}]},
         "/api/pharos/demo": [dict(id="d1", question="Which diseases?", why="one hop", passed=True, note="", columns=["disease", "evidence"],
                                   rows=[{"disease": "test disease", "evidence": EID}], evidence=[EID], cypher="MATCH ...")],
         "__report__": REPORT,
         "/api/pharos/status": {"nodes": 2, "edges": 1, "layers": ["pharos"], "verdicts": {"keep": 1, "prune": 0}, "predictions": None}}

NODE_SCRIPT = r"""
const { JSDOM, VirtualConsole } = require("jsdom");
const [pagePath, url, stubsPath, actsJson] = process.argv.slice(2);
const html = require("fs").readFileSync(pagePath, "utf8"), STUBS = JSON.parse(require("fs").readFileSync(stubsPath, "utf8"));
const errors = [], calls = [];
const vc = new VirtualConsole(); vc.on("jsdomError", e => errors.push(String(e.message || e))); vc.on("error", (...a) => errors.push(a.join(" ")));
const dom = new JSDOM(html, { url, runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
  w.fetch = async (u) => { const p = new URL(u, url).pathname; calls.push(p);
    const key = Object.keys(STUBS).sort((a, b) => b.length - a.length).find(k => p === k || (k.endsWith("/") && p.startsWith(k)));
    const body = key ? STUBS[key] : {detail: "not stubbed"};
    return { ok: !!key, status: key ? 200 : 404, json: async () => JSON.parse(JSON.stringify(body)) }; };
  w.Element.prototype.scrollIntoView = function () {}; w.scrollTo = () => {};
  w.EventSource = class { constructor(u) { calls.push(new URL(u, url).pathname); } addEventListener(t, cb) { if (t === "report" && STUBS.__report__) setTimeout(() => cb({data: JSON.stringify(STUBS.__report__)}), 50); } close() {} };
  w.addEventListener("error", e => errors.push("window.error: " + e.message));
  w.addEventListener("unhandledrejection", e => errors.push("unhandled: " + e.reason)); } });
const w = dom.window, d = w.document, sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  await sleep(400);
  for (const a of JSON.parse(actsJson)) { const el = d.querySelector(a.click); if (!el) errors.push("no element " + a.click); else el.click(); await sleep(300); }
  const vis = s => { const el = d.querySelector(s); if (!el) return false; for (let e = el; e; e = e.parentElement) if (e.hidden) return false; return true; };
  console.log(JSON.stringify({ errors, calls, card: vis("#v-card"), home: vis("#v-home"), drawer: vis("#drawer"),
    h1: d.querySelector("#v-card h1")?.textContent || null, rows: d.querySelectorAll("#c-ev ul.rows li").length,
    preds: d.querySelectorAll("#c-pred ol.preds li").length, cg: d.querySelector("#cg")?.children.length || 0,
    lead: d.querySelector("#pexplain .lead")?.textContent || null, persona: w.localStorage.getItem("tmkg2_persona"),
    words: d.querySelectorAll("#c-ev .word").length, tout: d.querySelector("#tout")?.textContent || "",
    ans: d.querySelector("#ans")?.textContent || "" }));
  process.exit(0);
})();
"""


def run_page(tmp_path, url, actions=()):
    js = tmp_path / "run.js"
    js.write_text(NODE_SCRIPT)
    stubs = tmp_path / "stubs.json"
    stubs.write_text(json.dumps(STUBS))
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"))
    out = subprocess.run([NODE, str(js), str(PAGE), url, str(stubs), json.dumps(list(actions))], capture_output=True, text=True,
                         timeout=90, env=env, cwd=str(JSDOM_DIR))
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_jsdom
def test_shared_link_opens_card_for_first_time_visitor(tmp_path):
    r = run_page(tmp_path, "http://test.local/#pharos/node/MONDO%3A0005148")
    assert r["errors"] == []
    assert r["card"] and not r["home"]
    assert r["h1"] == "test disease"
    assert r["persona"] is None                           # no role was chosen, quietly or otherwise
    assert "/api/pharos/card/MONDO%3A0005148" in r["calls"]   # the id is URL-encoded in the path
    assert r["cg"] > 0                                     # the picture is drawn into svg#cg


@needs_jsdom
def test_query_node_link_opens_card(tmp_path):
    r = run_page(tmp_path, "http://test.local/?node=MONDO%3A0005148")
    assert r["errors"] == [] and r["card"] and r["h1"] == "test disease"


@needs_jsdom
def test_old_pharos_hash_opens_start_screen(tmp_path):
    r = run_page(tmp_path, "http://test.local/#pharos")
    assert r["errors"] == [] and r["home"] and not r["card"]


@needs_jsdom
def test_card_first_view_is_short_and_show_all_reveals_the_rest(tmp_path):
    r = run_page(tmp_path, "http://test.local/#pharos/node/MONDO%3A0005148")
    assert r["rows"] == 2 and r["preds"] == 1              # 2 rows per kind in the first view (the fixture has 3)
    r = run_page(tmp_path, "http://test.local/#pharos/node/MONDO%3A0005148", [{"click": '[data-tog="k0"]'}])
    assert r["errors"] == [] and r["rows"] == 3            # nothing is dropped: "Show all" brings back the third row


@needs_jsdom
def test_why_opens_the_drawer_with_the_lineage(tmp_path):
    r = run_page(tmp_path, "http://test.local/#pharos/node/MONDO%3A0005148", [{"click": "#c-pred button.why"}])
    assert r["errors"] == [] and r["drawer"]
    assert f"/api/pharos/explain/{EID}" in r["calls"]
    assert r["lead"] and "GENE9" in r["lead"] and "test disease" in r["lead"]


@needs_jsdom
def test_trial_report_renders_from_the_stream(tmp_path):
    r = run_page(tmp_path, "http://test.local/#trials/q/Why%20did%20TEST-1%20fail%3F")
    assert r["errors"] == []
    assert "/api/ask_stream" in r["calls"]
    assert "Failure Diagnosis" in r["tout"] and "The endpoint was missed." in r["tout"]
    assert "An unsupported idea." in r["tout"]              # rejected claims are listed, not hidden
    assert "Download PDF" in r["tout"]


@needs_jsdom
def test_run_all_tested_questions(tmp_path):
    r = run_page(tmp_path, "http://test.local/#ask", [{"click": "#demoall"}])
    assert r["errors"] == [] and "/api/pharos/demo" in r["calls"]
    assert "1 of 1 checks passed" in r["ans"]


def test_v2_is_no_longer_served():
    """Owner decision 11 Nov (after the go of 8 Nov): /v2 is dropped; the first page moved to /classic."""
    from fastapi.testclient import TestClient
    import app as appmod
    c = TestClient(appmod.app)
    assert c.get("/v2").status_code == 404
    assert c.get("/classic").text == (ROOT / "pipelines" / "app_page.html").read_text(encoding="utf-8")
