// Run a TrialMindKG page in jsdom against the real app, with no port: fetch and EventSource go through bridge.py
// (FastAPI TestClient over stdin/stdout). Reports script errors and DOM-level snapshots of what the screen shows.
// usage: node run_page_v3.js <python> <bridge.py> <page path: / | /classic | /v3 (old links: redirects to /)> <hash or ?query> '<actions json>'
// actions: {wait}, {click: sel}, {type: [sel, text]}, {submit: sel}, {hash: "#..."}, {hover: sel}, {pin: sel},
//          {slider: [sel, value]}, {waitFor: sel, timeout}, {snap: name}, {axe: name} (PK12: axe-core on the page as it is now; a serious or
//          critical violation is a script error, so the journey and `make harness` fail; colour contrast is checked by tests/test_page_pk12.py and
//          docs/ux/a11y_browser_check.js, because jsdom has no layout)
const { JSDOM, VirtualConsole } = require("jsdom");
const { spawn } = require("child_process");
const readline = require("readline");
const [py, bridgePath, pagePath, where, actsJson] = process.argv.slice(2);
const BASE = "http://tmkg.local";
const errors = [], calls = [];
const bridge = spawn(py, [bridgePath], { stdio: ["pipe", "pipe", "pipe"] });
let stderr = ""; bridge.stderr.on("data", d => { stderr += d; if (stderr.length > 20000) stderr = stderr.slice(-20000); });
let finished = false;   // if the Python bridge dies (or cannot start) before the run ends, say so instead of exiting with no output
const bridgeDied = why => { if (finished) return; finished = true; console.log(JSON.stringify({ harness_error: "bridge.py " + why, python: py, bridge_stderr: stderr.slice(-3000) })); process.exit(1); };
bridge.on("error", e => bridgeDied("could not start: " + e.message));
bridge.on("exit", (code, sig) => setTimeout(() => bridgeDied("exited early (code " + code + ", signal " + sig + ")"), 300));
const pending = new Map(); let nextId = 1;
readline.createInterface({ input: bridge.stdout }).on("line", line => { const r = JSON.parse(line); const p = pending.get(r.id); if (p) { pending.delete(r.id); p(r); } });
const ask = (method, url, body) => new Promise(res => { const id = nextId++; pending.set(id, res); bridge.stdin.write(JSON.stringify({ id, method, url, body }) + "\n"); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  // the page is asked for as a browser would (with its ?query); after a redirect (an old /v3 link) the window gets the address
  // the server sent it to, and keeps the #... part, as a browser does
  const query = (where || "").startsWith("?") ? where : "", hash = (where || "").startsWith("#") ? where : "";
  const page = await ask("GET", pagePath + query);
  if (page.status !== 200) throw new Error("page " + pagePath + " -> " + page.status);
  const at = page.url ? new URL(page.url) : null, landed = at ? at.pathname + at.search : pagePath + query;
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => errors.push("jsdomError: " + String(e.detail?.stack || e.message || e).split("\n").slice(0, 3).join(" | ")));
  vc.on("error", (...a) => errors.push("console.error: " + a.map(String).join(" ").slice(0, 300)));
  const url = BASE + landed + hash;
  const dom = new JSDOM(page.text, { url, runScripts: "dangerously", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (u, o = {}) => { const full = new URL(u, url); const path = full.pathname + full.search; calls.push((o.method || "GET") + " " + path);
        const r = await ask(o.method || "GET", path, o.body);
        const text = r.text ?? Buffer.from(r.b64 || "", "base64").toString("latin1");
        return { ok: r.status >= 200 && r.status < 300, status: r.status, headers: { get: k => k.toLowerCase() === "content-type" ? r.type : ((r.headers || {})[k.toLowerCase()] ?? null) },
                 json: async () => JSON.parse(text), text: async () => text, blob: async () => new w.Blob([text]) }; };
      w.EventSource = class { constructor(u) { this.l = {}; const full = new URL(u, url); calls.push("SSE " + full.pathname + full.search);
          ask("GET", full.pathname + full.search).then(async r => { for (const chunk of (r.text || "").split("\n\n")) { const ev = /^event: (\w+)/m.exec(chunk), da = /^data: (.*)$/m.exec(chunk);
            if (ev && da && this.l[ev[1]]) { for (const cb of this.l[ev[1]]) cb({ data: da[1] }); await sleep(10); } } }); }
        addEventListener(t, cb) { (this.l[t] = this.l[t] || []).push(cb); } close() {} };
      w.matchMedia = q => ({ matches: false, media: q, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
      w.Element.prototype.scrollIntoView = function () {}; w.scrollTo = () => {};
      w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} }; w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
      w.navigator.clipboard = { writeText: async () => {} }; w.URL.createObjectURL = () => "blob:x"; w.URL.revokeObjectURL = () => {};
      const aclick = w.HTMLAnchorElement.prototype.click;            // downloads (blob: / download=) do nothing in jsdom; other links click normally
      w.HTMLAnchorElement.prototype.click = function () { if (this.hasAttribute("download") || String(this.href).startsWith("blob:")) return; return aclick.call(this); };
      w.addEventListener("error", e => errors.push("window.error: " + String(e.error?.stack || e.message).split("\n").slice(0, 3).join(" | ")));
      w.addEventListener("unhandledrejection", e => errors.push("unhandledrejection: " + String(e.reason?.stack || e.reason).split("\n").slice(0, 3).join(" | ")));
    } });
  const w = dom.window, d = w.document;
  const vis = el => { for (let e = el; e && e !== d.body; e = e.parentElement) if (e.hidden) return false; return !!el; };
  const q = s => [...d.querySelectorAll(s)].filter(vis);
  const txt = (s, n = 200) => (d.querySelector(s)?.textContent || "").replace(/\s+/g, " ").trim().slice(0, n);
  const snapOf = name => ({ name, hash: w.location.hash, persona: w.localStorage.getItem("tmkg3_persona"), errors_so_far: errors.length,
    lens: txt("#lensline", 160),
    start: { visible: vis(d.querySelector("#start")), example_tabs: q("#xtabs button").length, example_cards: q("#xlist .xcard").length },
    main_examples: q("#xmain .xcard").length, programs: { cards: q("#proglist .prog").length, first: txt("#proglist .prog", 260) },
    echo: { question: txt("#pecho .msg.user .q", 160), context_fields: [...d.querySelectorAll("#pecho .ctx dt")].map(x => x.textContent) },
    card: d.querySelector("#pcardsec") ? { title: txt("#pcardsec h2", 60), order: d.querySelector("#pcardsec").dataset.order,
      sections: [...d.querySelectorAll("#pcardsec .cardsec")].map(x => x.dataset.sec), kinds: [...d.querySelectorAll("#pcardsec details.kind")].map(x => x.dataset.kind),
      rows: d.querySelectorAll("#pcardsec tr").length, strength_chips: d.querySelectorAll("#pcardsec td .chip").length, why: d.querySelectorAll("#pcardsec a.why").length,
      cg_nodes: d.querySelectorAll("#cg g[data-id]").length, cg_lines: d.querySelectorAll("#cg line.e").length,
      cg_lines_by_verdict: ["kept", "pruned", "predicted", "unscored"].map(v => v + ":" + d.querySelectorAll("#cg line.v-" + v).length).join(" "),
      cg_hidden: d.querySelectorAll("#cg line.e.off").length, cg_dimmed: d.querySelectorAll("#cg g.dim").length,
      slider: d.querySelector("#thr")?.value, slider_info: txt("#thrinfo", 300), legend: txt("#cl", 300),
      trials: txt("#ctrials", 200), plist: d.querySelectorAll("#plist li").length, pathboxes: d.querySelectorAll("#pchain .pathbox").length,
      hops: d.querySelectorAll("#pchain .h").length, hops_kept: d.querySelectorAll("#pchain .h.v-kept").length, hops_pruned: d.querySelectorAll("#pchain .h.v-pruned").length,
      paths_thin: d.querySelectorAll("#pchain .pathbox.thin").length, first_chain: txt("#pchain .chain", 240), weakest: txt("#pchain [data-weak]", 160) } : null,
    hovercard: d.querySelector("#hovercard") ? { hidden: d.querySelector("#hovercard").hidden, pinned: d.querySelector("#hovercard").classList.contains("pinned"), text: txt("#hovercard", 400) } : null,
    answer: (() => { const root = d.querySelector("#pansw"); if (!root || !root.querySelector(".thread")) return null; return answerOf("#pansw"); })(),
    report: (() => { const root = d.querySelector("#out"); if (!root || !root.querySelector(".thread")) return null;
      return Object.assign(answerOf("#out"), { sections: [...d.querySelectorAll("#out section.card[data-role]")].map(x => x.dataset.role), claims: d.querySelectorAll("#out .claim").length,
        claim_evidence_chips: d.querySelectorAll("#out .claim a.chip").length, subgraphs: d.querySelectorAll("#out svg.graph").length }); })(),
    drawer: !d.querySelector("#drawer") ? null : { open: !d.querySelector("#drawer").hidden, title: txt("#dtitle", 60), lead: txt("#pexplain .lead", 160), raw: d.querySelector("#pexplain #rawlink")?.getAttribute("href") || null,
      headings: [...d.querySelectorAll("#pexplain h3, #pexplain h4")].map(x => x.textContent.trim().slice(0, 50)) },
    visible: { buttons: q("button").length, links: q("a").length, sections: q("section").length, tables: q("table").length },
    headings: q("main h2").map(h => h.textContent.trim().slice(0, 60)).slice(0, 40) });
  function answerOf(sel) { const r = d.querySelector(sel);
    return { user: txt(sel + " .msg.user .q", 160), context_fields: [...r.querySelectorAll(".msg.user .ctx dt")].map(x => x.textContent), pinned_bottom_line: !!r.querySelector(".blpin"),
      stages: [...r.querySelectorAll("[data-stage]")].map(x => x.dataset.stage), headline: txt(sel + " .bottomline .hl", 300), confidence: txt(sel + " .bottomline .conf", 40),
      confidence_reason: txt(sel + " .bottomline .conf + span", 200), points: [...r.querySelectorAll(".bottomline > ul:first-of-type > li")].map(x => x.textContent.replace(/\s+/g, " ").trim().slice(0, 220)),
      caveats: [...r.querySelectorAll(".bottomline h4")].map(h => h.textContent), refs: r.querySelectorAll(".bottomline .ref").length,
      rows: r.querySelectorAll("table[data-answer] tr[data-row]").length, evidence_chips: r.querySelectorAll("table[data-answer] a.chip").length,
      first_chip: txt(sel + " table[data-answer] a.chip", 160), checks: [...r.querySelectorAll("ul.checks li")].map(x => x.className + ": " + x.textContent.trim().slice(0, 100)),
      abstain: txt(sel + " .abstain", 200) }; }
  const snaps = [], evals = [], axeRuns = [];
  await sleep(1500);
  // "@<file>": the actions are read from that file (the tests use this: some Macs kill a process whose arguments carry page script)
  const actsText = (actsJson || "").startsWith("@") ? require("fs").readFileSync(actsJson.slice(1), "utf8") : actsJson;
  for (const a of JSON.parse(actsText || "[]")) {
    try {
      if (a.hash) w.location.hash = a.hash;
      if (a.click) { const el = d.querySelector(a.click); if (el) el.click(); else errors.push("no element " + a.click); }
      if (a.type) { const el = d.querySelector(a.type[0]); el.value = a.type[1]; el.dispatchEvent(new w.Event("input", { bubbles: true })); }
      if (a.submit) d.querySelector(a.submit).dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true }));
      if (a.hover) { const el = d.querySelector(a.hover); if (el) el.dispatchEvent(new w.MouseEvent("mouseover", { bubbles: true, clientX: 300, clientY: 260 })); else errors.push("no element " + a.hover); }
      if (a.pin) { const el = d.querySelector(a.pin); if (el) el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, clientX: 300, clientY: 260 })); else errors.push("no element " + a.pin); }
      if (a.slider) { const el = d.querySelector(a.slider[0]); el.value = String(a.slider[1]); el.dispatchEvent(new w.Event("input", { bubbles: true })); }
      if (a.waitFor) { const t0 = Date.now(); while (!d.querySelector(a.waitFor) && Date.now() - t0 < (a.timeout || 20000)) await sleep(150); if (!d.querySelector(a.waitFor)) errors.push("timeout waiting for " + a.waitFor); }
      if (a.eval) evals.push(String(w.eval(a.eval)).slice(0, 2000));
      if (a.snap) snaps.push(snapOf(a.snap));
      if (a.axe) { w.HTMLCanvasElement.prototype.getContext = () => null; if (!w.axe) w.eval(require("fs").readFileSync(require.resolve("axe-core/axe.min.js"), "utf8"));
        const r = await w.axe.run(d, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] }, rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
        const v = r.violations.map(x => ({ id: x.id, impact: x.impact, nodes: x.nodes.length, sample: x.nodes.slice(0, 3).map(n => n.target.join(" ")) }));
        axeRuns.push({ name: a.axe, violations: v }); for (const x of v) if (x.impact === "serious" || x.impact === "critical") errors.push(`axe ${a.axe}: ${x.id} (${x.impact}) on ${x.nodes} element(s), e.g. ${x.sample[0]}`); }
    } catch (e) { errors.push("action " + JSON.stringify(a) + ": " + e.message); }
    await sleep(a.wait ?? 400);
  }
  if (!snaps.length) snaps.push(snapOf("end"));
  if (process.env.DUMP) snaps.push({ dump: Object.fromEntries(process.env.DUMP.split("|").map(sel => [sel, (d.querySelector(sel)?.outerHTML ?? null)?.slice(0, 1500)])) });
  console.log(JSON.stringify({ page: pagePath, where, landed, script_errors: errors.length, errors: errors.slice(0, 20), api_calls: calls.length,
    api_calls_first: calls.slice(0, 25), api_calls_last: calls.slice(-15), evals, axe: axeRuns, snapshots: snaps }, null, 1));
  finished = true;
  bridge.stdin.write(JSON.stringify({ method: "QUIT" }) + "\n"); w.close(); setTimeout(() => process.exit(0), 200);
})().catch(e => { finished = true; console.log(JSON.stringify({ harness_error: String(e.stack || e), errors, bridge_stderr: stderr.slice(-3000) })); try { bridge.kill(); } catch (x) {} process.exit(1); });
