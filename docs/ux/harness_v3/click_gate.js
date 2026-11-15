// CC-46 click gate: every ASK / OPEN control on the front page ("/") must visibly respond and finish, in jsdom against the real app
// (bridge.py: FastAPI TestClient over stdin/stdout, no port). For each control it checks, and records:
//   bound    - the element has a click listener (submit buttons: their form has a submit listener) and the page function the
//              handler calls exists (runPill, quickStart, runExample, runDemo, pharosAsk, pharosFind, ask, trialQuery);
//   busy     - right after the click: the control shows the busy state (class busy, aria-busy) while it is on screen, or,
//              when the click moved the view away from it (a start-screen example), the section's status line is busy;
//   changed  - the answer container's final text differs from its text right AFTER the click (the busy state), or the
//              status line, no longer busy, names the question the page said it was asking (CC-46b: two different
//              questions can get the identical answer, e.g. the offline stand-in, so "differs from the previous answer"
//              is not a completion signal);
//   finished - within the timeout the status line is no longer busy, the container no longer says "Working..." /
//              "Running...", and one of the two "changed" signals holds;
//   visible  - the status line (#paskline / #taskline) is on screen with the headline or the error;
//   rows     - PK6 (U15): a control whose question has a stored answer (/api/stored_answers) or is a tested question
//              (/api/pharos/demo_questions) and that ends "answered" must show at least one evidence row or one claim (the
//              demo button: every tested answer); an answered stored or tested question with no rows FAILS ("empty answer").
// Then, with the limiter full (bridge CLICK_GATE_429=1), a typed Pharos question and a typed trial question must put the
// server's limit message AND "Try again in N min" in the status line. PK15: last, in every persona, the "Run this on Amazon Neptune"
// control under a tested answer (bridge NEPTUNE_FAKE=available) must respond, finish and print its line in the status line.
// 11 Nov (owner decision): "/" is the one front page (app_page_v3.html). The first page lives at /classic for the regression
// journey R1 only and is no longer clicked here; section "routes" checks that "/" is the page with the stored answers and the
// worked examples, that /classic still opens, that /v2 is gone and that an old /v3 link lands on "/" with its query.
// usage: node click_gate.js <python> <bridge.py> [out.json]      env GATE_LIVE=1: use the configured model (default: offline)
//      env STRICT=1: an error answer also fails (use where the data is);
//        the deliberate limit (429) checks expect an error answer and never count as one
// Exit code: 0 all pass, 1 any failure, 2 harness error.
const { JSDOM, VirtualConsole } = require("jsdom");
const { spawn } = require("child_process");
const readline = require("readline");
const fs = require("fs");
const [py, bridgePath, outPath] = process.argv.slice(2);
const BASE = "http://tmkg.local", STRICT = process.env.STRICT === "1", TIMEOUT = +(process.env.GATE_TIMEOUT_MS || 90000);
const sleep = ms => new Promise(r => setTimeout(r, ms));
// a window closed after its check may still have requests in flight; their errors belong to no check and are only counted
let closedNoise = 0; process.on("uncaughtException", e => { closedNoise++; }); process.on("unhandledRejection", e => { closedNoise++; });
// the page functions the controls' handlers call
const FRONT = "/", PAGES = [FRONT];
const HANDLERS = {[FRONT]: ["runPill", "quickStart", "runExample", "runDemo", "pharosAsk", "pharosFind", "pharosCard", "ask", "trialQuery", "askBegin", "askFetch"]};

// The gate judges the page, not the model: its app runs the offline stand-in (no NVIDIA call, no credits, no 90 s live
// report) even where .env holds NVIDIA_API_KEY (the Mac). GATE_LIVE=1 keeps the configured backend.
const GATE_ENV = process.env.GATE_LIVE === "1" ? {} : { TMKG_LLM_BACKEND: "offline" };
function startBridge(env) {
  const b = spawn(py, [bridgePath], { stdio: ["pipe", "pipe", "pipe"], env: Object.assign({}, process.env, GATE_ENV, env || {}) });
  let stderr = ""; b.stderr.on("data", d => { stderr += d; if (stderr.length > 20000) stderr = stderr.slice(-20000); });
  const pending = new Map(); let nextId = 1;
  readline.createInterface({ input: b.stdout }).on("line", line => { const r = JSON.parse(line); const p = pending.get(r.id); if (p) { pending.delete(r.id); p(r); } });
  const ask = (method, url, body) => new Promise(res => { const id = nextId++; pending.set(id, res); b.stdin.write(JSON.stringify({ id, method, url, body }) + "\n"); });
  return { ask, quit: () => { try { b.stdin.write(JSON.stringify({ method: "QUIT" }) + "\n"); } catch (e) {} }, stderr: () => stderr };
}

async function openPage(bridge, pagePath, where) {
  const page = await bridge.ask("GET", pagePath);
  if (page.status !== 200) throw new Error("page " + pagePath + " -> " + page.status);
  const errors = [], listeners = new WeakMap();
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => errors.push("jsdomError: " + String(e.detail?.stack || e.message || e).split("\n").slice(0, 2).join(" | ")));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 200)));
  const url = BASE + pagePath + (where || "");
  const dom = new JSDOM(page.text, { url, runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      const add = w.EventTarget.prototype.addEventListener;           // record which elements have which listeners
      w.EventTarget.prototype.addEventListener = function (type, fn, opts) { const l = listeners.get(this) || []; l.push(type); listeners.set(this, l); return add.call(this, type, fn, opts); };
      w.fetch = async (u, o = {}) => { const full = new URL(u, url); const r = await bridge.ask(o.method || "GET", full.pathname + full.search, o.body);
        const text = r.text ?? Buffer.from(r.b64 || "", "base64").toString("latin1");
        return { ok: r.status >= 200 && r.status < 300, status: r.status, headers: { get: k => (r.headers || {})[k.toLowerCase()] ?? (k.toLowerCase() === "content-type" ? r.type : null) },
                 json: async () => JSON.parse(text), text: async () => text, blob: async () => new w.Blob([text]) }; };
      // EventSource: a non-200 answer (a 429) or a stream that ends without its report fires onerror, as a browser does
      w.EventSource = class { constructor(u) { this.l = {}; this.closed = false; const full = new URL(u, url);
          bridge.ask("GET", full.pathname + full.search).then(async r => { let reported = false;
            if (r.status === 200) for (const chunk of (r.text || "").split("\n\n")) { const ev = /^event: (\w+)/m.exec(chunk), da = /^data: (.*)$/m.exec(chunk);
              if (ev && da && this.l[ev[1]]) { if (ev[1] === "report") reported = true; for (const cb of this.l[ev[1]]) cb({ data: da[1] }); await sleep(5); } }
            if (!reported && !this.closed && this.onerror) this.onerror(new w.Event("error")); }); }
        addEventListener(t, cb) { (this.l[t] = this.l[t] || []).push(cb); } close() { this.closed = true; } };
      w.matchMedia = q => ({ matches: false, media: q, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
      w.Element.prototype.scrollIntoView = function () { (w.__scrolled = w.__scrolled || []).push(this.id || this.tagName); }; w.scrollTo = () => {};
      w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} }; w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      w.navigator.clipboard = { writeText: async () => {} }; w.URL.createObjectURL = () => "blob:x"; w.URL.revokeObjectURL = () => {};
      w.addEventListener("error", e => errors.push("window.error: " + String(e.error?.stack || e.message).split("\n").slice(0, 2).join(" | ")));
      w.addEventListener("unhandledrejection", e => errors.push("unhandledrejection: " + String(e.reason?.stack || e.reason).split("\n").slice(0, 2).join(" | ")));
    } });
  const w = dom.window, d = w.document;
  for (let i = 0; i < 100 && !d.querySelector(".persona"); i++) await sleep(100);     // role cards come from /api/personas
  await sleep(800);                                                                     // chips, examples, strip, stored answers
  return { w, d, errors, listeners, close: () => { w.__closed = true; setTimeout(() => { try { w.close(); } catch (e) {} }, 3000); } };
}
const vis = el => { if (!el || !el.isConnected) return false; for (let e = el; e; e = e.parentElement) if (e.hidden) return false; return true; };
const txt = el => (el ? el.textContent : "").replace(/\s+/g, " ").trim();
const WORKING = /Working(:|\.\.\.)|Running the|Running\.\.\.|Loading\.\.\./;
// PK6 (U15): what an answer shows: evidence rows of a graph answer, claims of a trial report (the demo button: per tested answer)
const answerRows = c => c ? c.querySelectorAll("table[data-answer] tr[data-row]").length + c.querySelectorAll(".claim").length : 0;
const demoRows = c => c ? [...c.querySelectorAll("[data-demoans]")].map(answerRows) : [];
let ANSWERED = { stored: new Set(), tested: new Set() };      // filled once from the first page (run)
const norm = q => String(q || "").toLowerCase().replace(/\s+/g, " ").replace(/[?.\s]+$/, "").trim();

// click one control and judge its response; returns a result row
async function check(pg, el, o) {
  const { w, d, errors, listeners } = pg;
  const row = { page: o.page, persona: o.persona || "", control: o.control, label: txt(el).slice(0, 90), container: o.container };
  if (!el) return Object.assign(row, { pass: false, why: "control not found" });
  const form = o.submit ? el.form || el.closest("form") : null;
  const bound = o.submit ? (listeners.get(form) || []).includes("submit") : (listeners.get(el) || []).includes("click");
  const missing = (HANDLERS[o.page] || []).filter(f => typeof w[f] !== "function");
  const cont = d.querySelector(o.container), line = d.querySelector(o.container === "#out" ? "#taskline" : "#paskline");
  const before = txt(cont), e0 = errors.length;
  if (o.type) { const inp = d.querySelector(o.type[0]); inp.value = o.type[1]; inp.dispatchEvent(new w.Event("input", { bubbles: true })); }
  if (form) form.requestSubmit(el); else el.click();
  const after = txt(cont);                                       // the busy state: the snapshot "changed" is judged against
  const asked = line && line.classList.contains("busy") ? txt(line.querySelector("b")) : "";   // what the page says it asks
  const pillBusy = vis(el) && el.classList.contains("busy") && el.getAttribute("aria-busy") === "true";
  const lineBusy = vis(line) && line.classList.contains("busy");
  const busy = vis(el) ? pillBusy : lineBusy;                    // a control still on screen must itself show it is working
  const echoed = () => !!asked && txt(line).includes(asked);
  const t0 = Date.now(); let done = false;
  while (Date.now() - t0 < TIMEOUT) { await sleep(100);
    const c = d.querySelector(o.container);
    if (line && !line.classList.contains("busy") && !line.hidden && !WORKING.test(txt(c)) && (txt(c) !== after || echoed())) { done = true; break; } }
  await sleep(150);
  const c = d.querySelector(o.container), final = txt(c), lineTxt = txt(line);
  const outcome = !done ? "unfinished" : line.classList.contains("err") ? "error shown" : "answered";
  const changed = final !== after || (done && echoed());
  const restored = !el.isConnected || (!el.classList.contains("busy") && !el.disabled);
  const q = norm(asked || o.question || txt(el)), has = o.tested || ANSWERED.tested.has(q) ? "tested" : ANSWERED.stored.has(q) ? "stored" : "";
  const nrows = answerRows(c), perDemo = o.demoAll ? demoRows(c) : null;
  const empty = outcome === "answered" && !!has && (perDemo ? !perDemo.length || perDemo.some(n => n === 0) : nrows === 0);
  const why = [!bound && "no listener bound", missing.length && "missing functions: " + missing.join(","), !busy && "no busy state on click",
    !changed && "answer container did not change and the status line does not name the question", WORKING.test(final) && "still says Working", !done && `not finished in ${TIMEOUT / 1000} s`,
    !vis(line) && "status line not visible", !restored && "control left busy", errors.length > e0 && "script error: " + errors.slice(e0).join("; ").slice(0, 200),
    STRICT && outcome === "error shown" && !o.limit && "error answer (STRICT)",
    empty && `empty answer: a ${has} question shows ${perDemo ? "a tested answer with" : ""} no rows or claims`, o.expect && !o.expect.every(x => lineTxt.includes(x)) && "status line lacks: " + o.expect.filter(x => !lineTxt.includes(x)).join(" / ")].filter(Boolean);
  console.error(`${!why.length ? "PASS" : "FAIL"} ${o.page} ${o.persona || ""} ${o.control} ${Date.now() - t0} ms ${outcome}${why.length ? " - " + why.join("; ") : ""}`);
  return Object.assign(row, { pass: !why.length, why: why.join("; "), bound, busy: busy ? (vis(el) ? "on the control" : "status line (control left the screen)") : "none",
    outcome, ms: Date.now() - t0, asked, echoed: echoed(), answer_of: has || "", rows: perDemo || nrows, changed_vs_busy: final !== after, status_line: lineTxt.slice(0, 500), answer_start: final.slice(0, 140), scrolled: (w.__scrolled || []).slice(-2).join(",") });
}

const containerFor = kind => kind === "trials" || kind === "query" ? "#out" : kind === "card" ? "#pcard" : "#pansw";
async function choosePersona(pg, pid) { pg.d.querySelector(`.persona[data-id="${pid}"]`).click(); await sleep(600); }

const ONLY = (process.env.GATE_ONLY || "routes,pills,chips,first,forms,repeat,examples,limit,neptune").split(",");
async function run() {
  const rows = [], bridge = startBridge();
  const P0 = await openPage(bridge, FRONT, "");
  const personas = [...P0.d.querySelectorAll(".persona")].map(b => b.dataset.id);
  const v3examples = {}; for (const pid of personas) v3examples[pid] = JSON.parse(P0.w.eval(`JSON.stringify((EXAMPLES[${JSON.stringify(pid)}] || []).map(x => ({id: x.id, kind: x.kind})))`));
  // PK6 (U15): the questions that have a stored answer or are tested questions (their answers must have rows)
  ANSWERED = { stored: new Set(JSON.parse(P0.w.eval("JSON.stringify(Object.keys(STORED))")).map(norm)),
               tested: new Set([...P0.d.querySelectorAll("#tqlist [data-tq]")].map(b => norm(b.dataset.label))) };
  P0.close();
  // 0. the routes: one front page, the first page only at /classic, /v2 gone, old /v3 links still land on the front page
  if (ONLY.includes("routes")) {
    const route = (page, control, pass, why, label) => { console.error(`${pass ? "PASS" : "FAIL"} ${page}  ${control}${pass ? "" : " - " + why}`);
      rows.push({ page, persona: "", control, label, pass, why: pass ? "" : why, outcome: "route" }); };
    const front = await bridge.ask("GET", FRONT), classic = await bridge.ask("GET", "/classic"), v2 = await bridge.ask("GET", "/v2");
    const moved = await bridge.ask("GET", "/v3?node=NCBIGene%3A1956");
    const isFront = t => /const STORED\b|stored_answer/.test(t || "") && (t || "").includes("const EXAMPLES =");
    route(FRONT, "route: the front page has stored answers and worked examples", front.status === 200 && isFront(front.text), `status ${front.status}, front page markers ${isFront(front.text)}`, "GET /");
    route("/classic", "route: the first page opens (regression journey only)", classic.status === 200 && (classic.text || "").includes("<html") && !isFront(classic.text), `status ${classic.status}`, "GET /classic");
    route("/v2", "route: removed", v2.status === 404, `status ${v2.status}, expected 404`, "GET /v2");
    route("/v3", "route: an old link lands on the front page with its query", moved.status === 200 && isFront(moved.text) && /[?&]node=NCBIGene%3A1956$/.test(moved.url || ""), `status ${moved.status}, final url ${moved.url}`, "GET /v3?node=NCBIGene%3A1956");
  }
  for (const page of PAGES) {
    // 1. every persona pill (ASK and OPEN), on one page per role, clicked one after the other
    for (const pid of ONLY.includes("pills") ? personas : []) {
      const pg = await openPage(bridge, page, ""); await choosePersona(pg, pid);
      const n = pg.d.querySelectorAll(".pill[data-tab]").length;
      for (let i = 0; i < n; i++) { const b = pg.d.querySelectorAll(".pill[data-tab]")[i];
        const t = b.dataset.tab, sel = t === "trials" ? "#tabTrials" : "#tabPharos"; pg.d.querySelector(sel).click(); await sleep(150);
        rows.push(await check(pg, b, { page, persona: pid, control: `.pill[data-tab=${t}] #${i + 1}`, container: containerFor(t) })); }
      pg.close();
    }
    // 2. every landing chip (#hchips -> quickStart), each from a fresh start screen
    const pg0 = await openPage(bridge, page, ""); const chips = [...pg0.d.querySelectorAll("#hchips .pill")].map(b => b.dataset.kind); pg0.close();
    for (let i = 0; i < (ONLY.includes("chips") ? chips.length : 0); i++) { const pg = await openPage(bridge, page, "");
      rows.push(await check(pg, pg.d.querySelectorAll("#hchips .pill")[i], { page, control: `#hchips .pill #${i + 1}`, container: containerFor(chips[i]) })); pg.close(); }
    // 2b. PK3: the first screen's own controls, each from a fresh first screen: "See a worked answer" (the stored TULIP-1 report),
    // every tested question of the list, and every "Also for this role" question of the first role tab
    if (ONLY.includes("first")) {
      let pg = await openPage(bridge, page, "");
      rows.push(await check(pg, pg.d.querySelector("#seeworked"), { page, control: "#seeworked (See a worked answer)", container: "#out" })); pg.close();
      pg = await openPage(bridge, page, ""); const nt = pg.d.querySelectorAll("#tqlist [data-tq]").length; pg.close();
      for (let i = 0; i < nt; i++) { pg = await openPage(bridge, page, "");
        rows.push(await check(pg, pg.d.querySelectorAll("#tqlist [data-tq]")[i], { page, control: `#tqlist [data-tq] #${i + 1}`, container: "#pansw", tested: true })); pg.close(); }
      pg = await openPage(bridge, page, ""); const also = [...pg.d.querySelectorAll("#xalso [data-rtab]")].map(b => b.dataset.rtab); pg.close();
      for (let i = 0; i < also.length; i++) { pg = await openPage(bridge, page, "");
        rows.push(await check(pg, pg.d.querySelectorAll("#xalso [data-rtab]")[i], { page, control: `#xalso [data-rtab=${also[i]}] #${i + 1}`, container: containerFor(also[i]) })); pg.close(); }
    }
    // 3. the tested-question strip, the demo button and the four forms
    if (ONLY.includes("forms")) { const pg = await openPage(bridge, page, ""); await choosePersona(pg, personas[0]); pg.d.querySelector("#tabPharos").click(); await sleep(300);
      const n = pg.d.querySelectorAll("#pstrip [data-demo]").length;
      for (let i = 0; i < n; i++) rows.push(await check(pg, pg.d.querySelectorAll("#pstrip [data-demo]")[i], { page, control: `#pstrip [data-demo] #${i + 1}`, container: "#pansw", tested: true }));
      rows.push(await check(pg, pg.d.querySelector("#pdemo"), { page, control: "#pdemo", container: "#pansw", tested: true, demoAll: true }));
      rows.push(await check(pg, pg.d.querySelector("#pask button[type=submit]"), { page, control: "#pask submit", container: "#pansw", submit: true, type: ["#pquestion", "Which genes are associated with SOD1?"] }));
      rows.push(await check(pg, pg.d.querySelector("#pf button[type=submit]"), { page, control: "#pf submit (Find)", container: "#pcard", submit: true, type: ["#pq", "EGFR"] }));
      pg.d.querySelector("#tabTrials").click(); await sleep(300);
      rows.push(await check(pg, pg.d.querySelector("#f button[type=submit]"), { page, control: "#f submit", container: "#out", submit: true, type: ["#q", "Why did the dal-GenE trial of dalcetrapib fail?"] }));
      pg.close(); }
    if (ONLY.includes("forms")) { const pg = await openPage(bridge, page, "");
      rows.push(await check(pg, pg.d.querySelector("#hf button[type=submit]"), { page, control: "#hf submit (Go)", container: "#pcard", submit: true, type: ["#hq", "EGFR"] })); pg.close(); }
  }
  // 3b. CC-46b repeat: the same control twice in a row gets the identical answer (the offline stand-in on the Mac); the second
  //     click must still be judged finished (busy snapshot + the question echoed in the status line), not time out
  for (const page of ONLY.includes("repeat") ? PAGES : []) { const pg = await openPage(bridge, page, ""); await choosePersona(pg, personas[0]);
    pg.d.querySelector("#tabPharos").click(); await sleep(300);
    for (const k of [1, 2]) rows.push(await check(pg, pg.d.querySelectorAll("#pstrip [data-demo]")[0], { page, control: `repeat: #pstrip [data-demo] #1, click ${k}`, container: "#pansw", tested: true }));
    for (const k of [1, 2]) rows.push(await check(pg, pg.d.querySelector("#pask button[type=submit]"), { page, control: `repeat: #pask submit, same question, click ${k}`, container: "#pansw", submit: true, type: ["#pquestion", "Which diseases is PTGS2 associated with?"] }));
    pg.close(); }
  // 4. every worked example: from the start screen (fresh page each) and from the role's fold in the main view
  for (const pid of ONLY.includes("examples") ? personas : []) for (const x of v3examples[pid]) {
    const pg = await openPage(bridge, FRONT, "");
    pg.d.querySelector(`#xtabs [data-xt="${pid}"]`).click(); await sleep(100);
    if (pg.d.querySelector("#xmore") && !pg.d.querySelector(`#xlist button[data-x="${x.id}"]`)) { pg.d.querySelector("#xmore").click(); await sleep(100); }
    rows.push(await check(pg, pg.d.querySelector(`#xlist button[data-x="${x.id}"]`), { page: FRONT, persona: pid, control: `#xlist [data-x=${x.id}] (start screen)`, container: containerFor(x.kind) }));
    pg.close();
  }
  for (const pid of ONLY.includes("examples") ? personas : []) { const pg = await openPage(bridge, FRONT, ""); await choosePersona(pg, pid); pg.d.querySelector("#xmainfold").open = true;
    for (const x of v3examples[pid]) { rows.push(await check(pg, pg.d.querySelector(`#xmain button[data-x="${x.id}"]`), { page: FRONT, persona: pid, control: `#xmain [data-x=${x.id}] (role fold)`, container: containerFor(x.kind) })); }
    pg.close(); }
  bridge.quit();
  // 5. the limit: every ask answers 429; typed questions (not stored) must show the server's message and the retry time
  const lim = startBridge({ CLICK_GATE_429: "1" });
  for (const page of ONLY.includes("limit") ? PAGES : []) {
    const pg = await openPage(lim, page, ""); await choosePersona(pg, personas[0]); pg.d.querySelector("#tabPharos").click(); await sleep(300);
    rows.push(await check(pg, pg.d.querySelector("#pask button[type=submit]"), { page, control: "429: #pask submit", container: "#pansw", submit: true, limit: true,
      type: ["#pquestion", "Which genes are associated with SOD1 and with ALS?"], expect: ["questions per visitor per hour", "Try again in 31 min"] }));
    pg.d.querySelector("#tabTrials").click(); await sleep(300);
    rows.push(await check(pg, pg.d.querySelector("#f button[type=submit]"), { page, control: "429: #f submit (event stream)", container: "#out", submit: true, limit: true,
      type: ["#q", "Why did the dal-GenE trial of dalcetrapib fail, and for whom?"], expect: ["questions per visitor per hour", "Try again in 31 min"] }));
    const left = pg.d.querySelector(".askleft:not([hidden])");
    rows.push({ page, control: "429: questions-left note", pass: !!left && /0 questions left this hour/.test(txt(left)), why: left ? "" : "no visible 'questions left' note", label: txt(left) });
    pg.close();
  }
  lim.quit();
  // 6. PK15: "Run this on Amazon Neptune" under a Pharos answer, in every persona. The bridge fakes the host's status file and answers
  //    the Neptune query from the app's own graph (NEPTUNE_FAKE=available; without a card index the Pharos test fixture). The control
  //    must turn busy, put "Running on Amazon Neptune" in the status line, finish, and add its line under the answer, which keeps its rows.
  const nep = startBridge({ NEPTUNE_FAKE: "available" });
  for (const pid of ONLY.includes("neptune") ? personas : []) {
    const pg = await openPage(nep, FRONT, ""); await choosePersona(pg, pid); pg.d.querySelector("#tabPharos").click(); await sleep(300);
    const first = pg.d.querySelector("#pstrip [data-demo]"); if (first) first.click();
    const sel = '#pansw .nepctl [data-nep="run"]';
    for (let i = 0; i < 300 && !pg.d.querySelector(sel); i++) await sleep(100);
    for (let i = 0; i < 100 && pg.d.querySelector("#paskline.busy"); i++) await sleep(100);     // the tested question has finished
    rows.push(await check(pg, pg.d.querySelector(sel), { page: FRONT, persona: pid, control: "neptune: " + sel + " (Run this on Amazon Neptune)", container: "#pansw",
      expect: ["Same question on Amazon Neptune"] }));
    pg.close();
  }
  nep.quit();
  const failed = rows.filter(r => !r.pass);
  const summary = { when: new Date().toISOString(), strict: STRICT, errors_after_close: closedNoise, total: rows.length, passed: rows.length - failed.length, failed: failed.length,
    error_answers: rows.filter(r => r.outcome === "error shown" && !r.control.startsWith("429:")).length,   // the 429 checks expect one
    empty_answers: rows.filter(r => /empty answer/.test(r.why || "")).length,                              // PK6 (U15): always fails
    outcomes: rows.reduce((a, r) => (a[r.outcome || "-"] = (a[r.outcome || "-"] || 0) + 1, a), {}), rows };
  const out = JSON.stringify(summary, null, 1);
  if (outPath) fs.writeFileSync(outPath, out); else console.log(out);
  console.error(`click gate: ${summary.passed}/${summary.total} passed` + (failed.length ? "; FAILED: " + failed.map(r => `${r.page} ${r.persona} ${r.control}: ${r.why}`).join(" | ") : ""));
  setTimeout(() => process.exit(failed.length ? 1 : 0), 200);
}
run().catch(e => { console.error("click gate harness error: " + (e.stack || e)); process.exit(2); });
