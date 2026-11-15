"""PK7A (docs/packets_2026-11-11/PK7A_answer_features.md; review findings U9, U12, U13): the answer area of the front page.

  - U9   the graph of an answer draws the steps of the top rows from the compare payload (at most 40 entities), with the page's own
         SVG renderer, one line per pair of entities, hover cards that say which graph holds the link;
  - U12  every row of the five tested questions and every link of an entity card has a badge "in Pharos" / "added by enrichment"
         (from the layers of the cited links), one sentence, and one switch "Pharos alone / Agent-built graph" that hides what only the
         enrichment adds (rows, links, graph) and swaps the bottom line, the tiles and the limits;
  - drawer: "Independent check" from GET /api/pharos/audit/{edge_id}: a stored record, a failure sentence, the limit message;
  - U13  a stored "no evidence" answer (outputs/examples, written by scripts/make_gap_example.py) with the nearest related evidence,
         why the gap exists and what data would close it.

Rendered tests run the real page in node + jsdom against the real app (bridge.py) on the enriched Pharos fixture of
tests/test_routes_compare.py (skipped without jsdom). The data tests need no browser.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import kg_query  # noqa: E402
import make_gap_example as mge  # noqa: E402
import stored_answers  # noqa: E402
import test_routes_audit as TA  # noqa: E402
import test_routes_compare as TC  # noqa: E402
from fixtures.pharos_fixture import build  # noqa: E402
from test_page_pk6 import CHOOSE, NODE, R11, needs_jsdom, run_view  # noqa: E402,F401

PAGE = ROOT / "pipelines" / "app_page_v3.html"
T = PAGE.read_text(encoding="utf-8")
FORBIDDEN = re.compile(r"\bG[0-4]R?\b|\bPK\d+[AB]?\b|\bCC-R?\d+|_layer\b|opentargets_layer|chembl37|frozen_[a-z]|kfold|\.(?:py|json|tsv|gz)\b|(?:data|outputs|pipelines|tests)/")


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return TC.enrich(build(tmp_path_factory.mktemp("pk7a")))


def block(name):
    """Returns the source of the page's PK7A block (from its banner to its end marker)."""
    a = T.index("// PK7A (review U9, U12, U13)")
    return T[a:T.index("// ================================================================ end of the PK7A block")]


# ------------------------------------------------------------------ U13: the stored no-evidence example (data, no browser)
@pytest.fixture(scope="module")
def gap_file():
    path = next(p for p in (ROOT / "outputs" / "examples").glob("q_*.json") if "gap" in json.loads(p.read_text(encoding="utf-8")).get("answer", {}))
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_the_no_evidence_example_is_what_the_script_builds():
    assert mge.main(["--check"]) == 0                         # the committed file equals a fresh build from the graph, the build's flags and the frozen response


def test_the_no_evidence_example_is_a_valid_stored_answer(gap_file):
    path, d = gap_file
    assert path.stem == d["id"] == stored_answers.question_id(d["question"]) and d["kind"] == "query" and d["endpoint"] == "/api/query"
    assert any(x["id"] == d["id"] for x in stored_answers.index(stored_answers.FOLDER))
    loaded = stored_answers.load(stored_answers.FOLDER, d["id"])
    a = loaded["answer"]
    assert a["rows"] == [] and a["evidence"] == [] and a["problems"] == [] and a["gap"]["statement"].startswith("No evidence in this graph for ")
    assert d["question"] == re.search(r'const GAP_QUESTION = "([^"]+)"', T).group(1)            # the page's pill asks exactly this question


def test_the_no_evidence_example_says_only_what_the_graph_and_the_frozen_response_say(gap_file):
    _, d = gap_file
    g = d["answer"]["gap"]
    kg = kg_query.KGIndex()
    a, b = g["subject"]["id"], g["object"]["id"]
    assert not [e for e in kg.edges.values() if e["predicate"] == "biolink:associated_with" and {e["subject"], e["object"]} == {a, b}]   # really no link
    assert kg.name(a) == g["subject"]["label"] and kg.name(b) == g["object"]["label"]
    for item in g["nearest"]:
        for eid in item["edges"]:
            assert eid in kg.edges                                              # every evidence record it points to exists
    fetched, rows = mge.top_diseases(a.split(":")[1])
    text = " ".join(x["text"] for x in g["nearest"]) + " ".join(g["why"])
    best = rows[0]
    assert f"{best['score']:g}" in text and best["name"] in text                  # the highest score of the list and its disease
    near = [x for x in rows if x["name"] in text and x["name"] != best["name"]]
    assert near and f"{near[0]['score']:g}" in text                                # the nearest disease and its score are the frozen response's
    top, cut = mge.cutoff()
    assert f"top {top} diseases" in text and f"{cut:g} or higher" in text
    assert g["retrieved"] == fetched[:10] and g["nearest"] and g["why"] and g["data_needed"]
    for s in [g["statement"], *g["why"], *g["data_needed"], *(x["text"] for x in g["nearest"])]:
        assert not FORBIDDEN.search(s), s


def test_the_no_evidence_script_refuses_a_pair_that_is_linked():
    kg = kg_query.KGIndex()
    pair = mge.find_pair(kg)
    assert pair is not None and pair[2] == "ENSEMBL:ENSG00000197442"
    assert not [e for e in kg.by_subject.get(pair[2], []) if e["predicate"] == "biolink:associated_with"]       # MAP3K5 has no association edge at all
    other = next(e for e in kg.edges.values() if e["predicate"] == "biolink:associated_with")
    with pytest.raises(ValueError):
        mge.build(kg, mge.load_report(), "2026-10-05T00:00:00Z", [], mge.load_terms(), (pair[0], pair[1], other["subject"], other["object"]))


# ------------------------------------------------------------------ the page block as text (no browser)
def test_the_page_draws_numbers_it_is_given_and_adds_none():
    b = block("pk7a")
    assert "Math.random" not in b and "toFixed" not in b                              # no number of its own
    assert "/api/pharos/compare/" in b and "/api/pharos/audit/" in b
    assert "safeUrl" in b and "javascript:" not in b                                  # a link from the audit payload is http(s) only


@pytest.mark.skipif(not NODE, reason="node not available")
def test_remap_rows_maps_the_pharos_alone_view_back_to_the_full_answer(tmp_path):
    import subprocess
    src = re.search(r"function remapRows\(o, map\) \{.*?\n\}\n", T, re.S).group(0)
    js = src + """
const s = {headline_refs: {rows: [1, 2]}, points: [{text: "x", refs: {rows: [2], edges: ["e1"]}}], refs: {row_strength: {1: "curated", 2: "text-mined"}},
  evidence_analysis: {flagged: [{row: 2, edge: "e9"}]}, counts: {rows: 2}};
console.log(JSON.stringify(remapRows(s, {1: 1, 2: 4})));"""
    f = tmp_path / "r.js"
    f.write_text(js)
    out = json.loads(subprocess.run([NODE, str(f)], capture_output=True, text=True, check=True).stdout)
    assert out["headline_refs"]["rows"] == [1, 4] and out["points"][0]["refs"] == {"rows": [4], "edges": ["e1"]}
    assert out["refs"]["row_strength"] == {"1": "curated", "4": "text-mined"} and out["evidence_analysis"]["flagged"] == [{"row": 4, "edge": "e9"}]
    assert out["counts"] == {"rows": 2}                                              # a count is not a row number


# ------------------------------------------------------------------ rendered: U12 + U9 on the five tested questions
STATE_JS = r"""(() => { const a = document.querySelector('#pansw'); const q = s => a.querySelector(s);
  return JSON.stringify({badges: [...a.querySelectorAll('table.answer tr[data-row]')].map(tr => ((tr.querySelector('[data-cmpbadge]') || {dataset: {}}).dataset.cmpbadge || '-') + (tr.hidden ? '/hidden' : '')),
    badge_text: [...a.querySelectorAll('table.answer [data-cmpbadge]')].map(b => b.textContent.trim()),
    line: (q('.cmpline') || {}).textContent, shown: (q('.cmpshown') || {}).textContent, headline: (q('[data-blbody] .hl') || {}).textContent, pin: (q('.blpin .hl') || {}).textContent,
    pressed: [...a.querySelectorAll('[data-cmpmode]')].map(b => b.dataset.cmpmode + ':' + b.getAttribute('aria-pressed')), evh: (q('.evblock h2 .muted') || {}).textContent,
    nodes: a.querySelectorAll('svg[data-ansgraph] g[data-id]').length, lines: a.querySelectorAll('svg[data-ansgraph] line.e').length,
    add: a.querySelectorAll('svg[data-ansgraph] [data-cmp=add]').length, nodes_off: a.querySelectorAll('svg[data-ansgraph] g.cmp-off').length, off: a.querySelectorAll('svg[data-ansgraph] .cmp-off').length,
    summary_hidden: !!(q('.summaryblk') || {}).hidden, tiles: [...a.querySelectorAll('.tiles4 .big')].map(x => x.textContent)}, (k, v) => v === undefined ? null : v); })()"""
STATE = {"eval": STATE_JS, "wait": 50}


def demo(i):
    return [CHOOSE, {"click": "#tabPharos", "wait": 1200}, {"click": f'#pstrip [data-demo="pharos_demo_{i}"]'}, {"waitFor": "#pansw .cmpline", "timeout": 20000, "wait": 600}]


@needs_jsdom
def test_tested_question_has_badges_a_sentence_and_the_switch(fx):
    evals, errors = run_view(fx, [*demo(1), STATE, {"click": '#pansw [data-cmpmode="alone"]', "wait": 300}, STATE, {"click": '#pansw [data-cmpmode="built"]', "wait": 300}, STATE])
    assert errors == []
    built, alone, back = (json.loads(e) for e in evals)
    assert built["badges"] == ["ph", "ph", "add"] and built["badge_text"] == ["in Pharos", "in Pharos", "+ added by enrichment"]
    assert built["line"].startswith("1 of 3 rows exists only because of the enrichment.") and built["shown"] is None
    assert built["pressed"] == ["built:true", "alone:false"]
    assert "cancer" in built["headline"] and built["off"] == 0 and built["add"] >= 2
    assert alone["badges"] == ["ph", "ph", "add/hidden"] and alone["pressed"] == ["built:false", "alone:true"]
    assert "cancer" not in alone["headline"] and alone["headline"] != built["headline"] and alone["pin"] == alone["headline"]
    assert alone["shown"].startswith("Showing the 2 of 3 rows that Pharos alone gives.") and alone["evh"].startswith("2 rows")
    assert alone["off"] >= 2 and alone["off"] == alone["add"]
    assert back["badges"] == built["badges"] and back["headline"] == built["headline"] and back["off"] == 0 and back["shown"] is None
    assert back["tiles"] == built["tiles"] and back["evh"] == built["evh"]


@needs_jsdom
def test_all_five_tested_questions_carry_their_marks(fx):
    expected = {1: ["ph", "ph", "add"], 2: ["ph", "add"], 3: ["ph", "add"]}                    # test_routes_compare.EXPECTED, by the page's badge
    for i, badges in expected.items():
        evals, errors = run_view(fx, [*demo(i), STATE, {"click": '#pansw [data-cmpmode="alone"]', "wait": 300}, STATE])
        assert errors == [], (i, errors)
        built, alone = (json.loads(e) for e in evals)
        assert built["badges"] == badges, (i, built["badges"])
        assert alone["badges"] == [b + "/hidden" if b == "add" else b for b in badges], i
        assert alone["headline"] != built["headline"], i
    for i in (4, 5):
        evals, errors = run_view(fx, [*demo(i), STATE])
        assert errors == [], (i, errors)
        s = json.loads(evals[0])
        assert s["line"] and s["pressed"] == ["built:true", "alone:false"] and all(b in ("ph", "add", "unk") for b in s["badges"]), (i, s)


@needs_jsdom
def test_the_graph_of_an_answer_draws_every_hop_of_the_top_rows(fx):
    evals, errors = run_view(fx, [*demo(2), STATE, {"click": '#pansw [data-cmpmode="alone"]', "wait": 300}, STATE])
    assert errors == []
    built, alone = (json.loads(e) for e in evals)
    assert 4 <= built["nodes"] <= 40 and built["lines"] >= 3                       # gene - protein - compounds: the two hops of the question
    assert built["nodes_off"] == 0 and alone["nodes_off"] == 1 and built["nodes"] - alone["nodes_off"] == 3     # the compound only the enrichment gives is gone, the Pharos hops stay


@needs_jsdom
def test_hover_card_of_a_line_says_which_graph_holds_it(fx):
    hover = {"eval": r"""(() => { const l = document.querySelector('#pansw svg[data-ansgraph] line.hit[data-id="e0000000000000067"]') || document.querySelector('#pansw svg[data-ansgraph] line.hit');
      l.dispatchEvent(new MouseEvent('mouseover', {bubbles: true})); return document.querySelector('#hovercard').textContent.replace(/\s+/g, ' '); })()""", "wait": 100}
    evals, errors = run_view(fx, [*demo(1), hover])
    assert errors == []
    assert "graph" in evals[0] and ("added by enrichment" in evals[0] or "in Pharos" in evals[0])


@needs_jsdom
def test_an_answer_the_page_holds_is_compared_with_a_post(fx):
    """A typed question or a stored answer has no tested-question id: the page POSTs the answer itself."""
    probe = {"eval": r"""(() => { const d = LASTANS && LASTANS.r; const copy = JSON.parse(JSON.stringify(d, (k, v) => k === '_g' || k === '_cmp' ? undefined : v)); delete copy.id;
      window.__p = cmpFetch(copy, {}).then(x => { window.__cmp = x; }); return 'sent'; })()""", "wait": 1500}
    done = {"eval": "JSON.stringify({applicable: window.__cmp && window.__cmp.applicable, sentence: window.__cmp && window.__cmp.sentence})", "wait": 10}
    evals, errors = run_view(fx, [*demo(1), probe, done])
    assert errors == [] and json.loads(evals[1]) == {"applicable": True, "sentence": "1 of 3 rows exists only because of the enrichment."}


# ------------------------------------------------------------------ rendered: U12 on the entity card
CARD_JS = r"""(() => { const a = document.querySelector('#pcardsec'); const q = s => a.querySelector(s);
  return JSON.stringify({badges: [...a.querySelectorAll('details.kind tbody tr')].map(tr => ((tr.querySelector('[data-cmpbadge]') || {dataset: {}}).dataset.cmpbadge || '-') + (tr.hidden ? '/hidden' : '')),
    line: (q('.cmpbar .cmpline') || {}).textContent, tiles: [...a.querySelectorAll('.band a.tile b')].slice(0, 5).map(b => b.textContent), hl: (q('.bottomline p.hl') || {}).textContent,
    pin: (q('.blpin .hl') || {}).textContent, kinds_hidden: [...a.querySelectorAll('details.kind')].filter(d => d.hidden).length, kinds: a.querySelectorAll('details.kind').length,
    pressed: [...a.querySelectorAll('[data-cmpmode]')].map(b => b.dataset.cmpmode + ':' + b.getAttribute('aria-pressed')),
    pred_note: (q('.cmppred') || {}).hidden, nodes: a.querySelectorAll('#cg g[data-id]').length, off: a.querySelectorAll('#cg .cmp-off').length}, (k, v) => v === undefined ? null : v); })()"""
CARDSTATE = {"eval": CARD_JS, "wait": 50}


@needs_jsdom
def test_entity_card_has_badges_a_sentence_and_the_pharos_alone_numbers(fx):
    card = [CHOOSE, {"type": ["#pq", "PTGS2"]}, {"submit": "#pf"}, {"waitFor": "#pcardsec .cmpbar", "timeout": 20000, "wait": 1500}]
    evals, errors = run_view(fx, [*card, CARDSTATE, {"click": '#pcardsec [data-cmpmode="alone"]', "wait": 300}, CARDSTATE, {"click": '#pcardsec [data-cmpmode="built"]', "wait": 300}, CARDSTATE])
    assert errors == []
    built, alone, back = (json.loads(e) for e in evals)
    assert built["line"].startswith("1 of the 7 links shown exists only because of the enrichment.") and built["pressed"] == ["built:true", "alone:false"]
    assert sorted(set(b for b in built["badges"] if b != "-")) == ["add", "ph"] and built["badges"].count("add") == 2
    assert alone["badges"].count("add/hidden") == 2 and alone["pressed"] == ["built:false", "alone:true"]
    assert alone["tiles"][:5] == ["5", "4", "4", "1", "0"]                              # known_shown, kinds, kept, pruned, predicted of the Pharos-alone summary
    assert alone["hl"].startswith("In Pharos alone this card shows 5 of the 7 links") and alone["pin"].endswith("5 known links, 1 pruned, 0 predicted")
    assert alone["off"] >= 1 and alone["pred_note"] in (None, False)                  # this fixture holds no prediction for the card; with one, the note is shown
    assert back["tiles"] == built["tiles"] and back["hl"] == built["hl"] and back["pin"] == built["pin"] and back["off"] == 0 and back["badges"] == built["badges"]


# ------------------------------------------------------------------ rendered: the independent check in the drawer
AUDIT_STATE = {"eval": r"""(() => { const s = document.querySelector('#pexplain section.audit'); return JSON.stringify({present: !!s, button: !!(s && s.querySelector('[data-auditgo]') && !s.querySelector('[data-auditgo]').hidden),
  text: s ? s.textContent.replace(/\s+/g, ' ').trim() : '', links: s ? [...s.querySelectorAll('a')].map(a => a.getAttribute('href')) : [], fail: !!(s && s.querySelector('[data-auditfail]'))}); })()""", "wait": 50}
OPEN_WHY = [CHOOSE, {"type": ["#pq", "PTGS2"]}, {"submit": "#pf"}, {"waitFor": "#pcardsec a.why", "timeout": 20000, "wait": 800}, {"click": "#pcardsec a.why", "wait": 1500}]


@needs_jsdom
def test_drawer_independent_check_unavailable_is_one_plain_sentence(fx):
    evals, errors = run_view(fx, [*OPEN_WHY, AUDIT_STATE, {"click": "#pexplain [data-auditgo]", "wait": 2500}, AUDIT_STATE])
    assert errors == []
    before, after = (json.loads(e) for e in evals)
    assert before["present"] and before["button"] and "sources the pruner does not read" in before["text"] and "not told the pruning verdict" in before["text"]
    assert after["fail"] and after["text"].endswith("independent check unavailable right now") and not after["button"]


@needs_jsdom
def test_drawer_independent_check_shows_a_stored_dossier_with_links(fx):
    TA.write_stored(fx / "data" / "audit" / "edges", "e0000000000000001")
    evals, errors = run_view(fx, [CHOOSE, {"type": ["#pq", "PTGS2"]}, {"submit": "#pf"}, {"waitFor": "#pcardsec a.why", "timeout": 20000, "wait": 800},
                                  {"click": '#pcardsec a.why[data-eid="e0000000000000001"]', "wait": 1500}, {"click": "#pexplain [data-auditgo]", "wait": 1500}, AUDIT_STATE])
    assert errors == []
    s = json.loads(evals[0])
    assert "Judgement: Supported" in s["text"] and "checked by an auditing agent against sources the pruner does not read" in s["text"]
    assert "ahead of time" in s["text"] and "3 of 3 raters agree" in s["text"] and "IntAct" in s["text"] and "Reactome" in s["text"] and "did not answer" in s["text"]
    assert s["links"] and all(h.startswith("https://") for h in s["links"])            # the record's javascript: link is not drawn
    assert not FORBIDDEN.search(s["text"]), s["text"]


@needs_jsdom
def test_drawer_independent_check_says_when_the_limit_is_used_up(fx):
    """The server answers 429 with a sentence for the visitor (tests/test_routes_audit.py); the page shows that sentence as it is."""
    stub = {"eval": r"""(() => { const f = window.fetch; window.fetch = (u, o) => /pharos\/audit\//.test(String(u)) ? Promise.resolve({ok: false, status: 429,
      json: async () => ({detail: "This demo runs at most 1 questions and independent checks per visitor per hour. Please try again in about 59 minutes.", available: false})}) : f(u, o); return 'stubbed'; })()""", "wait": 10}
    evals, errors = run_view(fx, [*OPEN_WHY, stub, {"click": "#pexplain [data-auditgo]", "wait": 800}, AUDIT_STATE,
                                  {"eval": "JSON.stringify(!!document.querySelector('#pexplain [data-auditgo]'))", "wait": 10}])
    assert errors == []
    s = json.loads(evals[1])
    assert s["fail"] and "per visitor per hour" in s["text"] and json.loads(evals[2]) is True        # the button stays, so the visitor can try again later


# ------------------------------------------------------------------ rendered: the no-evidence example
GAP_STATE = {"eval": r"""(() => { const o = document.querySelector('#out'); const g = o.querySelector('.gapblock');
  return JSON.stringify({gap: !!g, heads: g ? [...g.querySelectorAll('h3')].map(h => h.textContent) : [], items: g ? g.querySelectorAll('li').length : 0, refs: g ? g.querySelectorAll('a[data-evid]').length : 0,
    headline: (o.querySelector('.bottomline .hl') || {}).textContent, stored: !!o.querySelector('.answerlabel.stored'), banner: (o.querySelector('.answerlabel.stored') || {}).textContent,
    has: (window.__expect || []).map(x => o.textContent.replace(/\s+/g, ' ').includes(x))}); })()""", "wait": 50}


@needs_jsdom
def test_the_no_evidence_example_opens_from_a_pill_and_says_what_is_missing(fx, gap_file):
    _, d = gap_file
    g = d["answer"]["gap"]
    expect = {"eval": "window.__expect = " + json.dumps([g["nearest"][2]["text"], g["why"][1], g["data_needed"][0]]) + "; 'set'", "wait": 10}
    evals, errors = run_view(fx, [CHOOSE, {"click": "#tabTrials", "wait": 800}, {"waitFor": "#gappill [data-gapex]", "timeout": 10000, "wait": 200},
                                  {"click": "#gappill [data-gapex]", "wait": 400}, {"waitFor": "#out .gapblock", "timeout": 20000, "wait": 800}, expect, GAP_STATE])
    assert errors == []
    s = json.loads(evals[1])
    assert s["gap"] and s["heads"] == ["Nearest related evidence in the graph", "Why there is a gap", "What data would close it"]
    assert s["items"] == len(g["nearest"]) + len(g["why"]) + len(g["data_needed"]) and s["refs"] == sum(min(3, len(x["edges"])) for x in g["nearest"])
    assert s["headline"] == g["statement"] and s["stored"] and "fixed rules" in s["banner"]
    assert s["has"] == [True, True, True]                                              # the sentences are drawn as the file has them


@needs_jsdom
def test_a_record_link_of_the_no_evidence_example_opens_the_evidence_record(fx):
    evals, errors = run_view(fx, [CHOOSE, {"click": "#tabTrials", "wait": 800}, {"waitFor": "#gappill [data-gapex]", "timeout": 10000, "wait": 200},
                                  {"click": "#gappill [data-gapex]", "wait": 400}, {"waitFor": "#out .gapblock a[data-evid]", "timeout": 20000, "wait": 500},
                                  {"click": "#out .gapblock a[data-evid]", "wait": 1500},
                                  {"eval": "JSON.stringify({open: !document.querySelector('#drawer').hidden, title: document.querySelector('#dtitle').textContent})", "wait": 20}])
    assert errors == []
    assert json.loads(evals[0]) == {"open": True, "title": "Evidence record"}


@needs_jsdom
def test_no_developer_words_in_the_new_views(fx):
    TA.write_stored(fx / "data" / "audit" / "edges", "e0000000000000001")
    runs = {"tested question, both views": [*demo(1), R11, {"click": '#pansw [data-cmpmode="alone"]', "wait": 300}, R11],
            "card, both views": [CHOOSE, {"type": ["#pq", "PTGS2"]}, {"submit": "#pf"}, {"waitFor": "#pcardsec .cmpbar", "timeout": 20000, "wait": 1500}, R11,
                                 {"click": '#pcardsec [data-cmpmode="alone"]', "wait": 300}, R11],
            "drawer with a stored check": [CHOOSE, {"type": ["#pq", "PTGS2"]}, {"submit": "#pf"}, {"waitFor": "#pcardsec a.why", "timeout": 20000, "wait": 800},
                                           {"click": '#pcardsec a.why[data-eid="e0000000000000001"]', "wait": 1500}, {"click": "#pexplain [data-auditgo]", "wait": 1500}, R11],
            "no-evidence example": [CHOOSE, {"click": "#tabTrials", "wait": 800}, {"waitFor": "#gappill [data-gapex]", "timeout": 10000, "wait": 200},
                                    {"click": "#gappill [data-gapex]", "wait": 400}, {"waitFor": "#out .gapblock", "timeout": 20000, "wait": 800}, R11]}
    problems = {}
    for name, acts in runs.items():
        evals, errors = run_view(fx, acts)
        assert errors == [], (name, errors)
        hits = [h for e in evals for h in json.loads(e)[1:]]
        if hits:
            problems[name] = hits
    assert problems == {}, json.dumps(problems, indent=1)
