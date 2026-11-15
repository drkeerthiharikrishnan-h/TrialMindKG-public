"""PK7B (13 Nov): the two page tabs "What the agent did" and "Generalization"
(docs/packets_2026-11-11/PK7B_agent_and_byo_tabs.md; review findings U13, U16).

Server (no browser):
  - usability.agent_story: the five-step strip; every figure is read from the record it names;
  - usability.no_evidence_example: one stored question the graph cannot answer, with the nearest related evidence and edge ids that exist;
  - usability.readable_page / GET /api/page/{name}: the tutorial and the ADC answer as text blocks; nothing else can be read;
  - the wording of all of it holds no file path, run tag, graph code, packet id or model-operations word.
Rendered (node + jsdom + the real app over bridge.py; skipped without jsdom):
  - the strip sits above every developer table, the log shows the first entries then all, the scientist filter keeps only decisions;
  - a stored run shows its table rows, the proposed mapping, the checks and the edges with provenance; a pasted table is drafted,
    approved and built; a refused paste shows a plain sentence; the readable pages open;
  - R11 (no developer words) holds on both tabs and after every interaction.
"""
import csv
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import routes_agent as ra  # noqa: E402
import usability as us  # noqa: E402
import test_page_pk6 as pk6  # noqa: E402  (the jsdom runner and the R11 scan are shared)

PAGE = (ROOT / "pipelines" / "app_page_v3.html").read_text(encoding="utf-8")
needs_jsdom = pk6.needs_jsdom


def strings(obj):
    """Every string VALUE in a JSON-like object (keys are code names, not shown to a reader)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


# ------------------------------------------------------------------ server: the strip
def test_strip_has_the_five_steps_and_every_figure_comes_from_its_record():
    st = us.agent_story(ROOT)
    assert st["available"] and [s["step"] for s in st["steps"]] == ["Ingest", "Filter", "Clean", "Limit", "Maintain"]
    assert all(s["what"] for s in st["steps"]) and all(s["figures"] for s in st["steps"])
    fig = {f["label"]: f for s in st["steps"] for f in s["figures"]}
    # Ingest: totals recomputed from the layers' own build records
    layers = [r for r in ra.shipped_records(ROOT) if not re.match(r"outputs/g\d+/", r)]
    sums = [ra.parse_summary((ROOT / r).read_text()) for r in layers]
    sums = [s for s in sums if s["n_edges"] is not None and s["title"]]
    assert fig["layers built, each from its own source card"]["value"] == len(sums)
    assert fig["links in those layers"]["value"] == sum(s["n_edges"] for s in sums)
    # Filter: the tested questions
    with open(ROOT / "data/curated/golden_questions.csv", encoding="utf-8") as f:
        assert [x["value"] for x in fig.values() if x["record"] == "Tested questions"] == [sum(1 for _ in csv.DictReader(f))]
    # Clean and Limit: the number is written in the cited file
    for f in fig.values():
        if f["record"] == "Pruning report of the Pharos layer":
            assert f["text"] in (ROOT / st["records"][f["record"]]).read_text()
        if f["record"] == "Record of the enriched graph":
            assert f["text"] in (ROOT / st["records"][f["record"]]).read_text()
    assert all(f["text"] == f"{f['value']:,}" for f in fig.values())


def test_strip_counts_the_logs_scientist_decisions_and_changes_with_the_records(tmp_path):
    st = us.agent_story(ROOT)
    n = [f["value"] for s in st["steps"] for f in s["figures"] if f["record"] == "Experiment log"]
    assert n == [ra.agent_log(ROOT)["counts"]["scientist_decisions"]] == [st["scientist_decisions"]]
    empty = us.agent_story(tmp_path)                       # nothing on disk: no figure, no error
    assert empty["available"] is False and all(s["figures"] == [] for s in empty["steps"])


def test_strip_and_no_evidence_wording_is_clean_for_a_reader():
    loop = us.agent_loop(ROOT)
    story = {k: v for k, v in loop["story"].items() if k != "records"}      # records: file behind each figure, shown only in a developer fold
    for part in (story, loop["no_evidence"]):
        bad = {t: ra.internal_terms(t) for t in strings(part) if ra.internal_terms(t)}
        assert bad == {}, bad


def test_agent_loop_carries_the_strip_and_the_example_and_keeps_the_developer_steps():
    a = us.agent_loop(ROOT)
    assert a["story"]["available"] and a["no_evidence"]["available"]
    assert [s["step"] for s in a["steps"]] == ["Ingest", "Filter", "Clean", "Limit", "Maintain"]
    assert all(s["module"] and s["command"] and s["tests"] for s in a["steps"])      # the developer table is still there
    assert us.agent_loop(Path("/nonexistent")) == {"available": False, "doc": "docs/AGENT_LOOP.md"}


# ------------------------------------------------------------------ server: the "no evidence" example
def test_no_evidence_example_is_a_true_absence_with_nearest_evidence_that_exists():
    import kg_query
    n = us.no_evidence_example(ROOT)
    assert n["available"] and n["headline"] == "No evidence in this graph."
    kg = kg_query.KGIndex(ROOT / "outputs")
    pid = next(p for p, node in kg.nodes.items() if node.get("category") == "tmkg:ClinicalTrialProgram" and node["name"] in n["question"])
    assert kg.program_history(pid)["rows"] == []                  # the graph really holds none for this program
    trials = kg.program_trials(pid)["rows"]
    assert [t["trial"].split(":")[-1] for t in trials] == [t["trial"] for t in n["nearest"]["trials"]]
    related = n["nearest"]["related_programs"]
    assert related and all(r["records"] > 0 and r["targets"] for r in related)
    assert sum(r["records"] for r in related) == sum(len(kg.program_history(p)["rows"]) for p in kg.nodes if kg.nodes[p].get("name") in {r["program"] for r in related} and kg.nodes[p].get("category") == "tmkg:ClinicalTrialProgram")
    assert n["edges"] and all(e in kg.edges for e in n["edges"])  # every cited edge id is in the graph
    assert f"{len(trials)} trial" in n["answer"] and f"{sum(r['records'] for r in related)} such record" in n["answer"]
    assert "is different from saying there is nothing" in n["note"]


def test_no_evidence_example_is_unavailable_without_the_trial_graph(tmp_path):
    assert us.no_evidence_example(tmp_path) == {"available": False}


# ------------------------------------------------------------------ server: readable pages
@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import app as A
    return TestClient(A.app)


def test_readable_pages_are_served_and_only_those(client):
    for name in ("tutorial", "adc-answer"):
        r = client.get(f"/api/page/{name}")
        assert r.status_code == 200
        p = r.json()
        assert p["id"] == name and p["blocks"] and {b["t"] for b in p["blocks"]} <= {"h", "p", "ul", "ol", "quote", "code", "table"}
    for bad in ("README", "..%2Fapp.py", "docs/BRING_YOUR_OWN_SOURCE.md", "tutorial.md", ""):
        assert client.get(f"/api/page/{bad}").status_code in (404, 405), bad
    assert us.readable_page("../../etc/passwd", ROOT) is None and us.readable_page("tutorial", Path("/nonexistent")) is None


def test_the_sponsor_answer_page_names_no_file_and_says_synthetic():
    p = us.readable_page("adc-answer", ROOT)
    assert p["developer"] is False and "SYNTHETIC" in p["label"]
    texts = [t for t in strings(p["blocks"])]
    assert [ra.internal_terms(t) for t in texts if ra.internal_terms(t)] == []
    assert any("SYNTHETIC" in t for t in texts) and any("N = 23" in t for t in texts)      # the content is kept, only the file names go


def test_the_tutorial_page_is_marked_for_developers_and_keeps_its_commands():
    p = us.readable_page("tutorial", ROOT)
    assert p["developer"] is True
    assert any("build_kg.py" in t for t in strings(p["blocks"]))
    assert [b["t"] for b in p["blocks"]][0] == "h"


def test_generalization_lists_the_pages_it_links_to():
    g = us.generalization(ROOT)
    assert {p["id"] for p in g["pages"]} == {"tutorial", "adc-answer"} and all(p["url"] == f"/api/page/{p['id']}" for p in g["pages"])
    assert us.generalization(Path("/nonexistent"))["pages"] == []


# ------------------------------------------------------------------ page source: the blocks are marked and nothing else changed shape
def test_page_blocks_are_marked_and_use_the_stored_endpoints():
    for tag in ("PK7B generalisation tab: start", "PK7B generalisation tab: end", "PK7B agent tab: start", "PK7B agent tab: end"):
        assert PAGE.count(tag) == 1
    for url in ("/api/byo/examples", "/api/byo/example/", "/api/byo/draft", "/api/byo/build", "/api/page/", "/api/agent-log", "/api/agent-loop"):
        assert url in PAGE, url
    assert "approved: true" in PAGE and "draft_token" in PAGE        # a build is only ever sent after the visitor pressed approve


# ------------------------------------------------------------------ rendered
@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return pk6.extended_fixture(tmp_path_factory.mktemp("pk7b"))


def J(expr):
    """An eval action that returns JSON of a page expression."""
    return {"eval": f"JSON.stringify({expr})", "wait": 50}


def texts(sel, n=400):
    return J(f"[...document.querySelectorAll({json.dumps(sel)})].map(e => e.textContent.replace(/\\s+/g, ' ').trim().slice(0, {n}))")


def run(fx, acts):
    evals, errors = pk6.run_view(fx, acts)
    assert errors == [], errors
    return [json.loads(e) for e in evals]


@needs_jsdom
def test_agent_tab_strip_above_developer_table_example_and_log(fx):
    story, order, noev, first, n_all, r11_all, filt, r11 = run(fx, [
        pk6.CHOOSE, {"click": "#tabJudges", "waitFor": "#alog li", "wait": 400},
        texts("#agent-strip li", 700),
        J("(() => { const els = [...document.querySelectorAll('#judges > section, #judges details[data-dev]')]; "
          "return [els.findIndex(e => e.id === 'agent-strip'), els.findIndex(e => e.matches('details[data-dev]')), document.querySelectorAll('#agent-strip details').length]; })()"),
        texts("#agent-noevidence", 3000),
        J("document.querySelectorAll('#alog li').length"),
        {"click": "#alogmore", "wait": 200}, J("document.querySelectorAll('#alog li').length"), pk6.R11,
        {"click": '#agent-log [data-who="scientist"]', "wait": 200},
        J("[document.querySelectorAll('#alog li').length, document.querySelectorAll('#alog li.decided').length, document.querySelectorAll('#alog .tag.decision').length, document.querySelector('#alogmore').hidden]"),
        pk6.R11])
    st = us.agent_story(ROOT)
    assert len(story) == 5 and all(f["text"] in " ".join(story) for s in st["steps"] for f in s["figures"])
    assert order[0] == 0 and order[1] > order[0] and order[2] == 0            # the strip is the first section; no developer fold inside it
    n = us.no_evidence_example(ROOT)
    assert n["question"] in noev[0] and "No evidence in this graph." in noev[0] and n["nearest"]["trials"][0]["trial"] in noev[0]
    log = ra.agent_log(ROOT)
    assert first == min(8, log["counts"]["entries"]) and n_all == log["counts"]["entries"]
    assert filt == [log["counts"]["scientist_decisions"]] * 3 + [True]       # only decisions, each marked, nothing more to show
    assert r11_all[0] == 0 and r11[0] == 0, (r11_all, r11)          # every dated entry in view, then the decisions only


@needs_jsdom
def test_generalization_before_after_pasted_table_and_readable_pages(fx):
    run_row = json.loads((ROOT / "examples/byo_source/runs/screen.json").read_text())
    table = "\n".join("\t".join(r) for r in [run_row["table"]["header"], *run_row["table"]["rows"]])
    n_rows, n_edges = len(run_row["table"]["rows"]), run_row["build"]["counts"]["edges"]
    (stage_heads, made, rows_edges, prov, second, no_btn, refused, drafted, built, built_rows, post, r11_a, tut, adc, r11_b) = run(fx, [
        pk6.CHOOSE, {"click": "#tabGeneralize", "waitFor": "#byoRun .byo-stages", "wait": 500},
        texts("#byoRun .byo-stages h4", 80),
        J("[document.querySelectorAll('#byoRun .byo-table tbody tr').length, document.querySelectorAll('#byoRun .byo-table tr.became').length, document.querySelectorAll('#byoRun .byo-table tr.noedge').length]"),
        J("document.querySelectorAll('#byoRun .byo-edges tbody tr').length"),
        texts("#byoRun .byo-edges tbody tr:first-child .prov dt", 40),
        J("[...document.querySelectorAll('[data-run]')].map(b => b.textContent)"),
        J("document.querySelector('#byoApprove') === null"),
        {"type": ["#byoText", "only_one_column\nvalue"]}, {"click": "#byoDraftBtn", "wait": 800}, J("document.querySelector('#byoState').textContent"),
        {"type": ["#byoText", table]}, {"type": ["#byoHint", "Each row is one compound tested against one protein."]},
        {"click": "#byoDraftBtn", "waitFor": "#byoApprove", "wait": 500}, texts("#byoOut .byo-dec li", 80),
        {"click": "#byoApprove", "waitFor": "#byoBuilt .byo-edges", "wait": 500},
        J("document.querySelectorAll('#byoBuilt .byo-edges tbody tr').length"),
        J("[document.querySelectorAll('#byoBuilt svg.byo-graph circle').length, document.querySelectorAll('#byoBuilt svg.byo-graph line').length]"),
        J("document.querySelector('#byoBuilt').textContent.includes('SYNTHETIC')"),
        pk6.R11,
        {"click": '[data-page="tutorial"]', "waitFor": "#byoReader .readable", "wait": 300},
        J("[document.querySelector('#byoReader details[data-dev]') !== null, document.querySelector('#byoReader h3').textContent]"),
        {"click": "#byoReaderClose", "wait": 100}, {"click": '[data-page="adc-answer"]', "waitFor": "#byoReader .readable", "wait": 300},
        J("[document.querySelector('#byoReader details[data-dev]') !== null, document.querySelector('#byoReader .readable').textContent.includes('N = 23')]"),
        pk6.R11])
    assert len(stage_heads) == 4 and [h.split(". ")[0] for h in stage_heads] == ["1", "2", "3", "4"]
    assert made == [n_rows, n_edges, n_rows - n_edges] and rows_edges == n_edges            # every edge points back at the row it came from
    assert prov == ["Source", "Knowledge source", "Kind of knowledge", "Made by", "Release", "Retrieved", "Row it came from", "Kept from the row"]
    assert len(second) == 2 and no_btn is True                                              # both stored runs; no build without a draft
    assert "tabs" in refused or "column" in refused.lower()                                  # a refused paste: one plain sentence
    assert len(drafted) >= 5 and built == n_edges                                           # draft -> approve -> the same six edges
    assert built_rows == [run_row["build"]["counts"]["nodes"], n_edges]                       # the small picture: one circle per node, one line per edge
    assert post is True and r11_a[0] == 0, r11_a
    assert tut == [True, "Tutorial: from your table to a graph"] and adc == [False, True] and r11_b[0] == 0, (adc, r11_b)
