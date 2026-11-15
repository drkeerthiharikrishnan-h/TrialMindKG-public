"""Checks for the v3 page (pipelines/app_page_v3.html, the front page at / since 11 Nov; docs/ux/V3_DESIGN_2026-11-08.md).

Static: / serves the page with the worked examples injected, /classic serves the first page unchanged, /v2 is gone (the
swap itself: tests/test_front_page_swap.py); every section title
family of the first page has a counterpart in v3 (explicit inventory); the info pages are ported verbatim; every /api URL
matches a route with the right method; the hover card, side panel, slider, bottom line and path panel exist.
Persona examples: every id is in the trial graph, every context field is the graph's own value, and every trial / query example
answers offline (card examples too when the card index is on this machine).
Behaviour (node + jsdom, else skipped): with stubbed answers, a shared card link opens with a hover card on a node and an edge,
the slider hides edges, a tested question shows its bottom line, and a role switch reorders the card.
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
V1, V2, V3 = (ROOT / "pipelines" / f for f in ("app_page.html", "app_page_v2.html", "app_page_v3.html"))
APP = ROOT / "pipelines" / "app.py"
sys.path.insert(0, str(ROOT / "pipelines"))

# Every h2/h3 title family of the first page (app_page.html) and where it lives in v3. Dynamic titles (section titles of a
# report, the card's own name, "1. <step>" of the agent loop) are covered by the code that renders them.
INVENTORY = [
    ("Ask the Pharos graph a question", "Pharos tab"), ("Graph", "Explore tab (#gtitle)"), ("Pharos graph", "Build tab"),
    ("Trial graph construction", "Build tab"), ("Quality", "Build tab"), ("Mapping coverage by entity type", "Build tab"),
    ("Validation failures (", "Build tab"), ("Cross-source checks (", "Build tab"), ("Open review queue (", "Build tab"),
    ("Sources and versions", "Build tab"), ("The numbers behind the four criteria", "Numbers tab"), ("The result in one paragraph", "Numbers tab"),
    ("Link prediction: Pharos alone vs the enriched graph (frozen benchmark)", "Numbers tab"),
    ("Further link-prediction results", "Numbers tab"), ("Pruning", "Numbers tab"), ("Standards: the schema with IRIs", "Numbers tab"),
    ("Downloads", "Numbers tab and For judges"), ("The same machinery on other science", "Generalization tab"),
    ("1. A second graph: failed clinical-trial programs", "Generalization tab"),
    ("2. Bring your own source: a table becomes a graph in five steps", "Generalization tab"),
    ("3. A sponsor's question: planning a pivotal trial", "Generalization tab"),
    ("The agent loop in the brief, step by step", "For judges"), ("What is autonomous and what is not", "For judges"),
    ("Claims removed by the verifier", "trial report"), ("Why this link", "side panel"),
    ("What it is", "card"), ("Known links, by kind of evidence", "card"), ("Predicted links no source states yet", "card (path panel)"),
    ("Failed trials that mention it", "card"), ("Where it comes from", "side panel"), ("Measured values", "side panel"),
    ("The same pair in other sources (", "side panel"), ("Timeline", "side panel"), ("Predicates", "Numbers tab"),
    ("Does the confidence mean something? Agreement with Open Targets, which the pruner never saw", "Numbers tab"),
    ("Each graph minus ", "Numbers tab"), ("Run ", "Numbers tab"),
]
# capabilities of the first page, by the code that provides them
CAPABILITIES = ["loadPersonas", "choose(", "popover(", "quickStart(", "runPill(", "loadLanding", "loadStrip", "#pdemo", "pharosAsk(",
                "pharosCard(", "pharosExplain(", "mechanismHtml(", "timelineSvg(", "downloadSubgraph(", "/api/trials_for", "ask(",
                "new EventSource", "/api/report.pdf", "/api/report_subgraph.zip", "trialmindkg_report.json", "expand(", "showNumbers",
                "showGeneralization", "showJudges", "showBuild", "/api/downloads", "/api/agent-loop", "#pharos/node/", 'get("node")',
                "trail(", "step(", "backend_default", "copy a link to this card", "_card.json"]   # PK6 (U7): no backend switch on the page; the server's default backend


def text(p):
    return p.read_text(encoding="utf-8")


def script(p):
    return re.search(r"<script>(.*)</script>", text(p), re.S).group(1)


def routes():
    """The app's routes: @app.get/post in app.py and @router.get/post in its router modules (pipelines/routes_*.py)."""
    found = {(m.upper(), p) for m, p in re.findall(r'@app\.(get|post)\("([^"]+)"', text(APP))}
    for f in sorted((ROOT / "pipelines").glob("routes_*.py")):
        found |= {(m.upper(), p) for m, p in re.findall(r'@router\.(get|post)\("([^"]+)"', text(f))}
    import routes_byo                                          # PK7B: its two POST routes are declared with constants, not literals
    return found | {("POST", routes_byo.DRAFT_PATH), ("POST", routes_byo.BUILD_PATH)}


def route_matches(lit, paths):
    for p in paths:
        if lit == p or ("{" in p and lit == p.split("{", 1)[0]):
            return True
        if "{" in p and re.fullmatch(re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(p)), lit):
            return True
    return False


def test_section_inventory_v1_to_v3():
    v1, v3 = text(V1), text(V3)
    missing_in_v1 = [t for t, _ in INVENTORY if t not in v1]
    assert not missing_in_v1, f"inventory names a title the first page does not have: {missing_in_v1}"
    missing = [t for t, _ in INVENTORY if t not in v3]
    assert not missing, f"v3 lost these sections: {missing}"
    # every literal h2/h3 title of the first page is in the inventory (so a new v1 section cannot be forgotten)
    titles = {t.strip() for t in re.findall(r"<h[23][^>]*>([^<$]{3,95})", v1)}
    covered = [t for t, _ in INVENTORY]
    assert all(any(c.strip() and (t.startswith(c.strip()) or c.strip().startswith(t)) for c in covered) for t in titles), titles
    gaps = [c for c in CAPABILITIES if c not in script(V3)]
    assert not gaps, f"v3 lacks: {gaps}"


def test_info_pages_are_ported_verbatim():
    v1, v3 = script(V1), script(V3)
    # showNumbers is redesigned on purpose (PK3, finding U6: the result table first, pruning in the order of AC-05, every run
    # table under "For judges"); tests/test_page_pk3.py checks its new order. PK6 (U7) reworded the other three info pages (source
    # names for layer codes, no build hash, repository references in labelled developer folds, tests/test_page_pk6.py): each still
    # reads the same endpoints and keeps every section title of the first page.
    for name in ("showBuild", "showGeneralization", "showJudges"):
        a = re.search(r"\nasync function " + name + r"\(\) \{\n(.*?)\n\}\n", v1, re.S).group(1)
        b = re.search(r"\nasync function " + name + r"\(\) \{\n(.*?)\n\}\n", v3, re.S).group(1)
        assert set(re.findall(r'fetch\("(/api/[^"]+)"', a)) <= set(re.findall(r'fetch\("(/api/[^"]+)"', b)), name
        assert [h for h in re.findall(r"<h2[^>]*>([^<$]{3,})", a) if h not in b] == [], name
        assert b.count('<section class="card') == b.count('<section class="card rv') + b.count('<section class="card accent rv'), name
    for name in ("mechanismHtml", "timelineSvg"):
        a = re.search(r"\nfunction " + name + r"\([^)]*\) \{\n(.*?)\n\}\n", v1, re.S).group(1)
        assert ("\n" + a + "\n") in v3, name


def test_routes_serve_the_front_page_with_examples_and_the_first_page_unchanged():
    from fastapi.testclient import TestClient
    import app as appmod
    from kg_query import KGIndex
    from nim import NIM
    appmod._state.update(kg=KGIndex(), nim=NIM(backend="offline"))
    c = TestClient(appmod.app)
    assert c.get("/classic").text == text(V1) and c.get("/v2").status_code == 404
    r = c.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "/*__PERSONA_EXAMPLES__*/null" not in r.text and "/*__PERSONA_EXAMPLES__*/null" in text(V3)
    m = re.search(r"const EXAMPLES = (\{.*?\}) \|\| \{\};", r.text, re.S)
    ex = json.loads(m.group(1))
    assert set(ex) == {"clinician", "physician_scientist", "bench_scientist", "drug_developer", "computational_biologist"}
    assert all(3 <= len(v) <= 4 for v in ex.values())


def test_api_urls_and_methods_match_routes():
    js, rs = script(V3), routes()
    lits = {m.group(1) for m in re.finditer(r'["`](/api/[^"`$?]*)', js)}
    assert len(lits) >= 20
    paths = {p for _, p in rs}
    assert not sorted(l for l in lits if not route_matches(l, paths))
    posts = set(re.findall(r'(?:fetch|askFetch)\("(/api/[^"]+)",\s*\{method:\s*"POST"', js))   # CC-46: asks go through askFetch
    assert posts == {"/api/pharos/ask", "/api/pharos/compare/answer", "/api/pharos/subgraph.zip", "/api/query", "/api/summarize",
                     "/api/byo/draft", "/api/byo/build", "/api/neptune/wake"}   # PK7A: compare of an answer the page holds; PK7B: own table; PK15: wake Neptune
    assert all(("POST", p) in rs for p in posts)
    assert re.findall(r'new EventSource\("([^"?]+)', js) == ["/api/ask_stream"]


def test_numbers_tab_empty_states_are_user_facing():
    """CC-29: the Numbers tab never shows repository paths, script names or "the Mac writes ..." instructions when a result
    is missing on the server; it says in a plain sentence that the result is not loaded."""
    for page in (V1, V2, V3):
        m = re.search(r"\nasync function showNumbers\(\) \{\n(.*?)\n\}\n", script(page), re.S)
        body = m.group(1)
        empties = re.findall(r'class=["\\\']*(?:empty|muted)["\\\']*>([^<$]*not loaded[^<]*|[^<$]*this machine[^<]*)<', body)
        assert empties, page.name
        for s in empties:
            assert "not loaded on this server" in s, (page.name, s)
        for bad in ("this machine", "The Mac writes", "run.py --tag", "prune_calibration.py", "outputs/linkpred/", "outputs/pharos/",
                    "e.missing.map"):
            assert bad not in body, (page.name, bad)


def test_nim_banner_shows_when_the_hosted_model_did_not_answer():
    """CC-36: one page-wide banner, filled by the landing check (model.warn) and by any answer that fell back."""
    t, js = text(V3), script(V3)
    assert len(re.findall(r'\sid="nimbanner"', t)) == 1 and 'role="alert"' in t
    assert "function nimBanner(" in js and "if (m.warn) nimBanner(m.paused ? m.note : MODEL_DOWN)" in js          # PK6 (U7): the banner in reader words
    assert 'l.kind === "fallback" || l.kind === "second_model"' in js and '"Backup model"' in js


def test_new_elements_exist():
    t, js = text(V3), script(V3)
    for i in ("hovercard", "drawer", "pexplain", "dtitle", "overlay", "xtabs", "xlist", "xmain", "proglist", "progq", "progf", "pecho",
              "pansw", "out", "cg", "eg", "badge", "pop", "hq", "hf", "pq", "pf", "q", "f", "pquestion", "pask", "pstrip", "pdemo"):
        assert len(re.findall(r'\sid="' + i + '"', t)) == 1, i
    for needle in ('id="thr"', 'type="range"', 'id="thrval"', 'id="thrinfo"', 'blk bottomline', 'class="blpin"', 'id="plist"',
                   'id="pchain"', 'class="pathbox"', 'id="rawlink"', "data-stage", "Edge threshold", "What would change this",
                   "Suggested next step", 'class="msg user"', "Stated failure"):
        assert needle in t, needle
    for fn in ("hcShow", "hcHide", "wireHover", "applyThreshold", "thinPaths", "selectPred", "renderGraphAnswer", "bottomLine",
               "openEvidence", "programCard", "openProgramPanel", "cardOrder", "rerenderForRole", "runExample", "stagesHtml"):
        assert re.search(r"function " + fn + r"\(", js), fn
    assert "mouseover" in js and "pointerdown" in js and 'pointerType === "touch"' in js and "dblclick" in js
    # PK3: dark is the default (owner, 12 Nov) and "Light" switches; reduced motion, coarse pointers and focus rings as before
    assert ':root[data-theme="light"]' in t and 'id="themesw"' in t and "prefers-reduced-motion" in t and "pointer: coarse" in t and ":focus-visible" in t
    # only Google Fonts as an external asset, with system fallbacks for every stack
    ext = set(re.findall(r'(?:src|href)\s*=\s*"(https?://[^"/]+)', t))
    assert ext <= {"https://fonts.googleapis.com", "https://fonts.gstatic.com", "https://pubmed.ncbi.nlm.nih.gov"}, ext
    for stack in ("system-ui", "sans-serif", "ui-monospace"):
        assert stack in t


def test_persona_examples_come_from_the_trial_graph():
    import persona_examples as PE
    import personas
    from kg_query import KGIndex
    kg = KGIndex()
    ex = PE.build(kg, personas.load_personas())
    assert sum(len(v) for v in ex.values()) == len(PE.SPEC)
    for xs in ex.values():
        for x in xs:
            if not x["program"]:
                assert x["context"]["programs_in_trial_layer"] == sum(1 for n in kg.nodes.values() if n["category"] == "tmkg:ClinicalTrialProgram")
                continue
            p, ctx = kg.nodes[x["program"]], x["context"]
            assert ctx["program"] == dict(id=p["id"], name=p["name"]) and ctx.get("stated_failure") == p.get("what_failed")
            assert ctx.get("indication") == p.get("indication")
            for t in ctx.get("trials", []):
                n = kg.nodes[t["id"]]
                assert all(n.get(k) == v for k, v in t.items() if k != "id")
            for d in ctx.get("drugs", []):
                assert kg.nodes[d["id"]]["name"] == d["name"]
            for t in ctx.get("targets", []):
                assert kg.nodes[t["id"]]["name"] == t["name"]
    html = PE.inject("<script>const X = " + PE.PLACEHOLDER + ";</script>", {"a": [{"q": "</script>"}]})
    assert html.count("</script>") == 1 and "\\u003c/script\\u003e" in html      # SEC-14: only the page's own closing tag; the data's is escaped


def test_trial_and_query_examples_answer_offline():
    import persona_examples as PE
    from kg_query import KGIndex
    from nim import NIM
    kg, nim = KGIndex(), NIM(backend="offline")
    for xs in PE.build(kg).values():
        for x in xs:
            if x["kind"] in ("trials", "query"):
                r = PE.check(x, kg, nim)
                assert r["ok"], (x["id"], x["question"], r)


@pytest.mark.skipif(not (ROOT / "data" / "kg" / "pharos_index" / "card_index_v2.sqlite").exists(), reason="card index v2 not on this machine")
def test_card_examples_open_cards_with_the_expected_evidence():
    import persona_examples as PE
    import pharos_index
    from kg_query import KGIndex
    from nim import NIM
    ci = pharos_index.CardIndex(ROOT / "data" / "kg" / "pharos_index" / "card_index_v2.sqlite")
    for xs in PE.build(KGIndex()).values():
        for x in xs:
            if x["kind"] == "card":
                r = PE.check(x, None, NIM(backend="offline"), ci)
                assert r["ok"], (x["id"], r)


# ------------------------------------------------------------------ behaviour (jsdom, optional)
JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node") or ("/opt/homebrew/bin/node" if Path("/opt/homebrew/bin/node").exists() else None)
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(), reason="node + jsdom not available")

EID, EID2 = "e0123456789abcdef", "e00000000000000a1"
ROW = lambda i, w, conf: dict(id=f"e000000000000000{i}", subject="MONDO:0005148", predicate="biolink:gene_associated_with_condition",
                              object=f"NCBIGene:{i}", statement="x", layer="pharos", source="CTD", source_version="v1",
                              knowledge_level="knowledge_assertion", verdict="keep" if w == "kept" else "prune", confidence=conf,
                              other=f"NCBIGene:{i}", other_label=f"GENE{i}", other_category="biolink:Gene", outgoing=False,
                              phrase="is associated with", sentence=f"GENE{i} is associated with test disease", word=w,
                              level_words="curated statement", kind="curated")
ROWS = [ROW(1, "kept", 0.9), ROW(2, "kept", 0.4), ROW(3, "pruned", 0.2)]
CARD = dict(about=dict(id="MONDO:0005148", label="test disease", category="biolink:Disease", type_word="disease", ids=["MONDO:0005148"], properties={}),
            summary=dict(known=3, kinds=2, kept=2, pruned=1, predicted=1, layers=["pharos"]),
            known=[dict(kind="curated", meaning="curated", color="#1e7b4f", n=3, n_kept=2, n_pruned=1, rows=ROWS),
                   dict(kind="annotation", meaning="ontology", color="#868e96", n=1, n_kept=1, n_pruned=0, rows=[dict(ROW(4, "kept", 0.5), kind="annotation")])],
            predicted=[dict(subject="NCBIGene:9", object="MONDO:0005148", other="NCBIGene:9", other_label="GENE9", other_category="biolink:Gene",
                            score=1.5, rank=1, graph="G3", task="B", known_in=[], model="meta-path model",
                            paths=[dict(via="NCBIGene:1", via_label="GENE1", shape="Gene -x-> Disease",
                                        edges=[dict(id=EID, statement="GENE9 -genetically_associated_with-> GENE1"),
                                               dict(id=EID2, statement="GENE1 -gene_associated_with_condition-> test disease")])])],
            prediction_note=None,
            picture=dict(nodes=[dict(id="MONDO:0005148", label="test disease", category="biolink:Disease")] +
                         [dict(id=f"NCBIGene:{i}", label=f"GENE{i}", category="biolink:Gene") for i in (1, 2, 3, 9)],
                         edges=[dict(id=r["id"], source=r["object"], target="MONDO:0005148", predicate=r["predicate"], evidence_type="curated",
                                     predicted=False, word=r["word"]) for r in ROWS] +
                         [dict(id="pred:NCBIGene:9|MONDO:0005148", source="NCBIGene:9", target="MONDO:0005148", predicate="predicted",
                               evidence_type="predicted", predicted=True, word="predicted")]),
            mechanism_neighbours=dict(kind="gene", header="Drugs that act on other members", note="Context, not evidence.", empty="none"),
            served_from="card index", elapsed_ms=1.0)
EXPLAIN = dict(id=EID, statement="s", subject=dict(id="NCBIGene:9", name="GENE9"), object=dict(id="NCBIGene:1", name="GENE1"),
               predicate="biolink:genetically_associated_with", provenance=dict(source="TIGA", knowledge_level="statistical_association"),
               properties={}, verdict=dict(verdict="keep", confidence="0.3", reasons="plausibility=0.2;sources=1"), corroboration=[],
               timeline=dict(events=[], summary=None))
DEMO = dict(id="d1", question="Which diseases?", why="one hop", passed=True, note="", columns=["disease", "evidence"],
            rows=[{"disease": "test disease", "evidence": EID}], evidence=[EID], cypher="MATCH (g:Gene {name:'GENE9'})-[r:x]->(d:Disease) RETURN d.name AS disease, r.id AS evidence")
PERSONAS = [{"id": "physician_scientist", "name": "Physician-scientist", "emoji": "x", "color": "#000", "description": "d", "welcome": "w",
             "show_ids": "hover", "wording": "mechanistic", "start_tab": "pharos", "card_sections": ["about", "known", "predicted", "trials"], "questions": []},
            {"id": "bench_scientist", "name": "Bench scientist", "emoji": "y", "color": "#111", "description": "d", "welcome": "w",
             "show_ids": "always", "wording": "mechanistic", "start_tab": "pharos", "card_sections": ["about", "known", "predicted", "trials"], "questions": []}]

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
  w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
  w.Element.prototype.scrollIntoView = function () {}; w.scrollTo = () => {};
  w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.EventSource = class { constructor(u) {} addEventListener() {} close() {} };
  w.addEventListener("error", e => errors.push("window.error: " + e.message)); } });
const w = dom.window, d = w.document, sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  await sleep(500);
  const out = [];
  for (const a of JSON.parse(actsJson)) {
    const el = a.sel ? d.querySelector(a.sel) : null;
    if (a.sel && !el) errors.push("no element " + a.sel);
    else if (a.do === "click") el.click();
    else if (a.do === "hover") el.dispatchEvent(new w.MouseEvent("mouseover", { bubbles: true, clientX: 100, clientY: 100 }));
    else if (a.do === "slider") { el.value = a.value; el.dispatchEvent(new w.Event("input", { bubbles: true })); }
    await sleep(a.wait || 300);
    if (a.snap) out.push({ name: a.snap, hc: d.querySelector("#hovercard").hidden ? null : d.querySelector("#hovercard").textContent,
      order: d.querySelector("#pcardsec")?.dataset.order || null, off: d.querySelectorAll("#cg line.e.off").length,
      lines: d.querySelectorAll("#cg line.e").length, kept: d.querySelectorAll("#cg line.v-kept").length, pruned: d.querySelectorAll("#cg line.v-pruned").length,
      thin: d.querySelectorAll("#pchain .pathbox.thin").length, hops: d.querySelectorAll("#pchain .h").length,
      bl: d.querySelector("#pansw .bottomline .hl")?.textContent || null, stages: [...d.querySelectorAll("#pansw [data-stage]")].map(x => x.dataset.stage),
      chips: d.querySelectorAll("#pansw table[data-answer] a.chip").length, kinds: [...d.querySelectorAll("#pcardsec details.kind")].map(x => x.dataset.kind) });
  }
  console.log(JSON.stringify({ errors, calls, out })); process.exit(0);
})();
"""


@needs_jsdom
def test_behaviour_hover_slider_bottom_line_and_role_order(tmp_path):
    import answer_summary as S
    demo = dict(DEMO)
    facts = {EID: dict(id=EID, subject="NCBIGene:9", subject_label="GENE9", predicate="biolink:x", phrase="is associated with", object="MONDO:0005148",
                       object_label="test disease", source="TIGA", source_record="r", layer="pharos", source_version="v", knowledge_level="statistical_association",
                       strength="statistical", kind="human genetics", activity=None, activity_type=None, evidence_type=None, verdict="kept", confidence=0.3,
                       reasons="plausibility=0.2", reasons_words="plausibility 0.20")}
    demo["summary"] = S.pharos_summary(demo, facts)
    stubs = {"/api/pharos/card/": CARD, "/api/pharos/explain/": EXPLAIN, "/api/trials_for": {"trials": []}, "/api/pharos/demo/": demo,
             "/api/pharos/demo_questions": [dict(id="d1", question="Which diseases?", why="one hop")],
             "/api/wording": {"predicates": {}, "knowledge_levels": {}, "categories": {"Disease": "disease", "Gene": "gene"}, "evidence_kinds": [],
                              "confidence_words": {"predicted": "a new link"}},
             "/api/landing": {"chips": [], "model": None, "backends": [], "backend_default": "local"}, "/api/personas": {"personas": PERSONAS},
             "/api/pharos/status": {"nodes": 2, "edges": 1, "layers": ["pharos"], "verdicts": {"keep": 1, "prune": 0}, "predictions": None}}
    (tmp_path / "s.json").write_text(json.dumps(stubs))
    (tmp_path / "run.js").write_text(NODE_SCRIPT)
    acts = [dict(wait=600, snap="card"), dict(sel="#cg g[data-id]", do="hover", snap="hover_node"),
            dict(sel="#cg line.hit", do="hover", wait=500, snap="hover_edge"), dict(sel="#thr", do="slider", value="0.5", wait=500, snap="slider"),
            dict(sel="#badge", do="click"), dict(sel='.pop button[data-id="bench_scientist"]', do="click", wait=500, snap="bench"),
            dict(sel='#pstrip [data-demo="d1"]', do="click", wait=700, snap="demo")]
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"))
    page = tmp_path / "page.html"
    page.write_text(text(V3))
    out = subprocess.run([NODE, str(tmp_path / "run.js"), str(page), "http://x.test/#pharos/node/MONDO%3A0005148", str(tmp_path / "s.json"), json.dumps(acts)],
                         capture_output=True, text=True, timeout=120, env=env, cwd=str(JSDOM_DIR))
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["errors"] == [], r["errors"]
    snap = {s["name"]: s for s in r["out"]}
    assert snap["card"]["lines"] == 4 and snap["card"]["kept"] == 2 and snap["card"]["pruned"] == 1 and snap["card"]["hops"] == 2
    assert snap["card"]["order"] == "about,known,predicted,mechanism,trials"
    assert "type" in snap["hover_node"]["hc"] and ("MONDO:0005148" in snap["hover_node"]["hc"] or "NCBIGene:" in snap["hover_node"]["hc"])
    assert "source" in snap["hover_edge"]["hc"] and "verdict" in snap["hover_edge"]["hc"] and "CTD" in snap["hover_edge"]["hc"]
    assert snap["slider"]["off"] == 2 and snap["slider"]["thin"] == 1          # rows with confidence 0.4 and 0.2 hidden; the 0.3 hop greys the path
    assert snap["bench"]["order"] == "about,mechanism,known,predicted,trials" and snap["bench"]["kinds"][0] == "annotation"
    assert snap["demo"]["bl"] == demo["summary"]["headline"] and snap["demo"]["chips"] == 1
    # PK3: one answer component in the brief's reading order for every role (the graph follows the bottom line; on wide windows
    # it is docked on the right); the role still orders the card's sections and kinds (asserted above) and a report's sections
    assert snap["demo"]["stages"] == ["question", "bottomline", "graph", "evidence", "limits", "downloads"]
