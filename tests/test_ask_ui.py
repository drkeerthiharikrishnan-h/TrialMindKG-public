"""CC-46 (9 Nov): every ASK path on both page files (the front page at "/" and the first page, at "/classic" since 11 Nov)
visibly responds where the person is looking.

Static (always): both pages carry the shared ask helpers (askBegin / askFetch / askLeft / streamError), the sticky status
lines #paskline / #taskline, the "questions left" slots; every ASK entry point (runPill, quickStart, the #pask / #f / #pf /
#hf forms, the tested-question strip, #pdemo, /v3 examples) passes the clicked control on and starts with askBegin; every
ask request goes through askFetch (try/catch: no "Working..." left forever); the trial event stream's error path asks
/api/ask_quota; the Safari Reader note is on both pages; the CC-43 page fixes stay.
Behaviour (node + jsdom, else skipped): the click gate (docs/ux/harness_v3/click_gate.js) on its forms and limit sections.
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
V1, V3 = ROOT / "pipelines" / "app_page.html", ROOT / "pipelines" / "app_page_v3.html"
PAGES = pytest.mark.parametrize("page", [V1, V3], ids=["v1", "v3"])
SAFARI = ("If this page shows as plain text with a Summarise button, you are in Safari Reader view - click the Reader icon and "
          "choose Hide Reader. Cmd-Shift-R in Safari opens Reader; use Option-reload to reload.")


def script(p):
    return re.search(r"<script>([\s\S]*)</script>", p.read_text(encoding="utf-8")).group(1)


def body(js, head):
    """The text of the function or handler that starts with `head`, up to the next top-level line."""
    i = js.index(head)
    m = re.search(r"\n(?=\S)", js[i + len(head):])
    return js[i: i + len(head) + (m.start() if m else len(js))]


@PAGES
def test_shared_helpers_and_status_lines(page):
    html, js = page.read_text(encoding="utf-8"), script(page)
    for f in ("function askBegin(", "async function askFetch(", "function askLeft(", "async function askQuota(", "async function streamError("):
        assert f in js, f
    assert '<div class="askline" id="paskline" role="status" aria-live="polite" hidden></div>' in html
    assert '<div class="askline" id="taskline" role="status" aria-live="polite" hidden></div>' in html
    assert html.count('<span class="askleft" hidden></span>') == 2
    assert ".askline { position:sticky;" in html and "button.busy" in html
    begin = body(js, "function askBegin(")
    assert 'btn.classList.add("busy")' in begin and 'setAttribute("aria-busy", "true")' in begin and "asking&hellip;" in begin
    assert begin.index("scrollIntoView") < begin.index("setInterval")          # the scroll happens before anything waits
    fetch_ = body(js, "async function askFetch(")
    assert "try { resp = await fetch(url, opts); } catch" in fetch_ and "The server did not answer" in fetch_
    assert "resp.status === 429" in fetch_ and "Retry-After" in fetch_ and "X-Ask-Remaining" in fetch_
    assert "Try again in ${Math.max(1, Math.ceil((+s || 0) / 60))} min." in js
    assert "askQuota();" in js.split("loadPersonas();")[-1]                    # the count is read on load


@PAGES
def test_every_ask_entry_point_passes_the_control_and_starts_busy(page):
    js = script(page)
    assert "quickStart(b.dataset.q, b.dataset.kind, b)" in js and "runPill(b.dataset.tab, b.dataset.q, b)" in js
    assert "runDemo(b.dataset.demo, b.textContent.replace(/^tested/, \"\"), b)" in js
    for form in ("#pask", "#f", "#pf", "#hf"):
        h = body(js, f'$("{form}").addEventListener("submit"')
        assert "e.submitter" in h, form
    for f in ("async function pharosAsk(", "async function ask(", "async function runDemo(", "async function pharosFind("):
        b = body(js, f)
        assert "askBegin(" in b, f
        assert "st.fail(" in b and ("st.done(" in b), f
    assert "askBegin(" in body(js, '$("#pdemo").addEventListener("click"')
    if page == V3:
        assert "runExample(exampleById(b.dataset.x), b)" in js and "askBegin(" in body(js, "async function trialQuery(")
    # no ask request left outside askFetch (so an exception never leaves "Working..." on screen)
    assert not re.search(r'await fetch\("/api/pharos/ask"|await fetch\("/api/query"|await fetch\("/api/pharos/demo(/|")', js)
    assert re.search(r'askFetch\("/api/pharos/ask"', js) and re.search(r'askFetch\("/api/pharos/demo', js)


@PAGES
def test_event_stream_errors_are_explained(page):
    b = body(script(page), "async function ask(")
    assert "es.onerror = async () =>" in b and "await streamError()" in b and "if (got) return;" in b
    assert "/api/ask_quota" in body(script(page), "async function askQuota(")


@PAGES
def test_safari_reader_note(page):
    html = page.read_text(encoding="utf-8")
    assert f'<p id="safarinote">{SAFARI}</p>' in html


@PAGES
def test_cc43_fixes_kept(page):
    html = page.read_text(encoding="utf-8")
    assert "body { padding-bottom:calc(72px + env(safe-area-inset-bottom)); }" in html          # bar never covers content
    assert ".backendsw { flex-wrap:wrap; } .backendsw select { max-width:100%; min-width:0; }" in html
    assert ".modelbadge { min-width:0; max-width:30vw;" in html and "code { overflow-wrap:anywhere; }" in html
    js = script(page)
    assert 'if (!$("#start").hidden && PERSONAS.length)' in body(js, "document.querySelectorAll(\"#bnav button\")") or \
        'if (!$("#start").hidden && PERSONAS.length)' in js
    assert 'toast("Ask a question first: JSON downloads the report on screen")' in js
    assert 'toast("Type a question first: PDF writes the report for it")' in js


def test_pages_are_revalidated_after_a_redeploy():
    sys.path.insert(0, str(ROOT / "pipelines"))
    from fastapi.testclient import TestClient
    import app as A
    c = TestClient(A.app)
    for path in ("/", "/classic"):
        assert c.get(path).headers.get("cache-control") == "no-cache", path


def test_ask_quota_counts_nothing(monkeypatch):
    sys.path.insert(0, str(ROOT / "pipelines"))
    from fastapi.testclient import TestClient
    import app as A
    monkeypatch.setenv("TMKG_ASK_LIMIT_PER_HOUR", "3")
    monkeypatch.setattr(A, "_ask_log", {})
    c = TestClient(A.app)
    first = c.get("/api/ask_quota").json()
    assert first == {"limit": 3, "remaining": 3, "retry_after": 0, "detail": ""}
    assert c.get("/api/ask_quota").json()["remaining"] == 3              # asking for the count does not spend a question
    key = A.visitor_key({}, "", "testclient")
    import time
    A._ask_log[key] = [time.time() - 600] * 3
    full = c.get("/api/ask_quota").json()
    assert full["remaining"] == 0 and 3000 <= full["retry_after"] <= 3001 and "3 questions per visitor per hour" in full["detail"]
    monkeypatch.delenv("TMKG_ASK_LIMIT_PER_HOUR")
    assert c.get("/api/ask_quota").json()["remaining"] is None


# ------------------------------------------------------------------ behaviour (jsdom, optional)
JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node")
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(), reason="node + jsdom not available")
H = ROOT / "docs" / "ux" / "harness_v3"


@needs_jsdom
def test_click_gate_forms_and_limit(tmp_path):
    """Strip, demo button and the four forms on the front page (the only page the gate clicks since 11 Nov) respond and finish; with the limiter full the status line shows
    the limit message and 'Try again in 31 min', and the 'questions left' note says 0."""
    out = tmp_path / "gate.json"
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"), GATE_ONLY="forms,limit", GATE_TIMEOUT_MS="30000")
    r = subprocess.run([NODE, str(H / "click_gate.js"), sys.executable, str(H / "bridge.py"), str(out)], cwd=JSDOM_DIR, env=env,
                       capture_output=True, text=True, timeout=900)
    assert out.exists(), r.stderr[-2000:]
    d = json.loads(out.read_text())
    bad = [(x["page"], x["control"], x["why"]) for x in d["rows"] if not x["pass"]]
    assert r.returncode == 0 and not bad, (bad, r.stderr[-1500:])
    lim = [x for x in d["rows"] if x["control"].startswith("429:") and "status_line" in x]
    assert len(lim) == 2 and all("Try again in 31 min" in x["status_line"] for x in lim)
    assert all(x["busy"] != "none" for x in d["rows"] if "busy" in x)


# ------------------------------------------------------------------ CC-46b: the status line names the question
@PAGES
def test_status_line_echoes_the_question(page):
    """Busy: "Asking: <q> ... N s"; done or failed: "<q>: <outcome>" (rows answers: "no rows" / "1 row" / "N rows")."""
    js = script(page)
    begin = body(js, "function askBegin(")
    assert '${esc(verb || "Asking")}: <b>${esc(q)}</b>' in begin
    assert 'line.innerHTML = `<span><b>${esc(q)}</b>: ${esc(text)}</span>`' in begin
    assert 'const rowsWord = n => !n ? "no rows" : n === 1 ? "1 row" : `${n} rows`;' in js
    assert "st.done(rowsLine(r));" in body(js, "async function pharosAsk(")
    assert "rowsLine(d)" in body(js, "async function runDemo(")
    assert not re.search(r'for "\$\{(q|label)\}"', js)          # the line itself names the question: no second copy


@needs_jsdom
def test_rows_word_and_status_line_text():
    """rowsWord / rowsLine evaluated in the page (v3): 0 -> no rows, 1 -> 1 row, 2 -> 2 rows, headline appended."""
    code = ("const {JSDOM} = require('jsdom'); const fs = require('fs');"
            "const js = fs.readFileSync(process.argv[1], 'utf8').match(/const rowsWord[^\\n]*\\n[^\\n]*\\n/)[0];"
            "const w = new JSDOM('', {runScripts: 'outside-only'}).window; w.eval(js + ';globalThis.R = [rowsWord(0), rowsWord(1), rowsWord(2),"
            " rowsLine({rows: [1, 2, 3]}), rowsLine({rows: [], summary: {headline: \"H\"}}), rowsLine(null)];');"
            "console.log(JSON.stringify(w.R));")
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"))
    r = subprocess.run([NODE, "-e", code, str(V3)], cwd=JSDOM_DIR, env=env, capture_output=True, text=True, timeout=60)
    assert json.loads(r.stdout) == ["no rows", "1 row", "2 rows", "3 rows", "no rows - H", "no rows"], r.stderr[-800:]


def test_gate_judges_against_the_busy_snapshot_and_the_echo():
    g = (H / "click_gate.js").read_text(encoding="utf-8")
    i = g.index("form.requestSubmit(el); else el.click();")
    after = g.index("const after = txt(cont);")
    assert i < after < g.index("const t0 = Date.now()")                 # snapshot taken right after the click
    assert 'const asked = line && line.classList.contains("busy") ? txt(line.querySelector("b")) : "";' in g
    assert "(txt(c) !== after || echoed())" in g and "const changed = final !== after || (done && echoed());" in g
    assert "txt(c) !== before" not in g                                   # the old "differs from the previous answer" rule
    assert 'STRICT && outcome === "error shown" && !o.limit' in g          # the deliberate 429 is never an error answer
    assert g.count("limit: true") == 2
    assert '"routes,pills,chips,first,forms,repeat,examples,limit,neptune"' in g  # PK3: + the first screen's controls; PK15: + Neptune


@needs_jsdom
def test_click_gate_repeat_identical_answers(tmp_path):
    """The Mac failure (CC-46b): the same tested question / typed question twice gets the identical answer; the second click
    must still pass (finished, status line names the question). STRICT=1 so the 429 rule is not involved."""
    out = tmp_path / "gate.json"
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"), GATE_ONLY="repeat", GATE_TIMEOUT_MS="30000")
    r = subprocess.run([NODE, str(H / "click_gate.js"), sys.executable, str(H / "bridge.py"), str(out)], cwd=JSDOM_DIR, env=env,
                       capture_output=True, text=True, timeout=600)
    d = json.loads(out.read_text())
    assert d["total"] == 4 and d["failed"] == 0 and r.returncode == 0, [x["why"] for x in d["rows"] if not x["pass"]]
    second = [x for x in d["rows"] if x["control"].endswith("click 2")]
    assert len(second) == 2 and all(x["echoed"] and x["asked"] and x["asked"] in x["status_line"] for x in second)
    # (no assertion that the two answers are identical: on the Mac they need not be, and the gate must pass either way)


def test_click_gate_runs_the_app_offline_unless_asked():
    """The Mac failure after CC-46b: with NVIDIA_API_KEY in .env the gate's app called the hosted model, so the "/" trial
    question ran live for over 90 s (and spent credits). The gate's bridge now gets TMKG_LLM_BACKEND=offline unless
    GATE_LIVE=1, and every bridge it starts (normal and limit) goes through startBridge."""
    g = (H / "click_gate.js").read_text(encoding="utf-8")
    assert 'const GATE_ENV = process.env.GATE_LIVE === "1" ? {} : { TMKG_LLM_BACKEND: "offline" };' in g
    assert "Object.assign({}, process.env, GATE_ENV, env || {})" in g
    assert g.count("spawn(py, [bridgePath]") == 1 and g.count("startBridge(") >= 3
    sys.path.insert(0, str(ROOT / "pipelines"))
    import nim
    assert nim.NIM().backend in ("nim", "offline")
    os.environ["TMKG_LLM_BACKEND"] = "offline"
    try:
        assert nim.NIM().backend == "offline"                                # what the gate's app gets, key or no key
    finally:
        os.environ.pop("TMKG_LLM_BACKEND")


@pytest.mark.skipif(not Path("/tmp/tmkg/jsdom").is_dir(), reason="journeys.sh cds into /tmp/tmkg/jsdom")
@pytest.mark.parametrize("skip", ["1", None], ids=["SKIP_GATE=1", "default"])
def test_journeys_skip_gate(tmp_path, skip):
    """journeys.sh with stub node scripts: SKIP_GATE=1 runs the journeys without the gate; the default runs the gate first
    and stops (exit 1) when it fails, before any journey."""
    h = tmp_path / "docs" / "ux" / "harness_v3"; h.mkdir(parents=True)
    shutil.copy(H / "journeys.sh", h / "journeys.sh")
    (h / "click_gate.js").write_text(f"require('fs').writeFileSync({json.dumps(str(tmp_path / 'gate_ran'))}, 'x'); process.exit(1);")
    (h / "run_page_v3.js").write_text("console.log(JSON.stringify({script_errors: 0}));")
    (h / "bridge.py").write_text("")
    env = {k: v for k, v in os.environ.items() if k not in ("SKIP_GATE",)}
    if skip:
        env["SKIP_GATE"] = skip
    r = subprocess.run(["bash", str(h / "journeys.sh")], env=env, capture_output=True, text=True, timeout=120)
    journeys = sorted(p.name for p in h.glob("[JR]*.json"))
    if skip:
        assert r.returncode == 0 and not (tmp_path / "gate_ran").exists() and len(journeys) == 19, (r.stdout, r.stderr)   # PK15: + J12a-d; FIX2: + J12e (wake flicker)
        assert "click_gate skipped (SKIP_GATE=1)" in r.stdout
    else:
        assert r.returncode == 1 and (tmp_path / "gate_ran").exists() and not journeys and "click gate failed" in r.stderr


@pytest.mark.skipif(not Path("/tmp/tmkg/jsdom").is_dir(), reason="journeys.sh cds into /tmp/tmkg/jsdom")
@pytest.mark.parametrize("live", [None, "1"], ids=["default-offline", "JOURNEYS_LIVE=1"])
def test_journeys_run_the_app_offline_unless_asked(tmp_path, live):
    """The Mac J5 failure: the journeys' app called the hosted model (NVIDIA_API_KEY in .env), a live trial report took more
    than 60 s and blocked the next step. journeys.sh exports TMKG_LLM_BACKEND=offline unless JOURNEYS_LIVE=1."""
    h = tmp_path / "docs" / "ux" / "harness_v3"; h.mkdir(parents=True)
    shutil.copy(H / "journeys.sh", h / "journeys.sh")
    seen = tmp_path / "backend_seen"
    (h / "run_page_v3.js").write_text(f"require('fs').appendFileSync({json.dumps(str(seen))}, (process.env.TMKG_LLM_BACKEND || 'unset') + '\\n');"
                                      " console.log(JSON.stringify({script_errors: 0}));")
    (h / "click_gate.js").write_text("process.exit(0);")
    (h / "bridge.py").write_text("")
    env = {k: v for k, v in os.environ.items() if k not in ("TMKG_LLM_BACKEND", "JOURNEYS_LIVE", "SKIP_GATE")}
    env["SKIP_GATE"] = "1"
    if live:
        env["JOURNEYS_LIVE"] = live
    r = subprocess.run(["bash", str(h / "journeys.sh")], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    vals = set(seen.read_text().split())
    assert vals == ({"unset"} if live else {"offline"}), vals
