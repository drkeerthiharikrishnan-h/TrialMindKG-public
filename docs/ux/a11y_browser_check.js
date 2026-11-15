// PK12 (13 Nov): the browser half of the accessibility checks (needs Chromium + Playwright; the jsdom journey J11 cannot measure colour or layout).
//   node docs/ux/a11y_browser_check.js [base-url] [screenshot-dir]
// Start the app first (offline model; for the entity card and the tested questions point TMKG_PHAROS_ROOT at the fixture:
//   python -m tests.fixtures.pharos_fixture <dir>). For both themes and for 1280 px and 390 px it
//   1. runs axe-core WITH colour contrast on five states (first screen, stored answer, entity card, drawer, Numbers) and fails on serious/critical,
//   2. at 390 px finds anything that makes the page wider than the screen and is not inside its own scroll box,
//   3. walks the graph with the keyboard (Tab, arrows, Enter, Escape) and the drawer (focus in, Escape, focus back),
//   4. writes the 390 px screenshots when a directory is given.
// Exit code 1 on any failure; the report is JSON on stdout.
const { chromium } = require("playwright");
const fs = require("fs"), path = require("path");
const BASE = process.argv[2] || "http://localhost:8000", SHOTS = process.argv[3] || "";
const axeSrc = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const report = { failures: [], axe: {}, overflow: {}, keyboard: {} };
const fail = m => report.failures.push(m);
async function states(p, phone) {
  const nav = id => (phone ? `#bnav [data-t=${id}]` : `#nav${id[0].toUpperCase() + id.slice(1)}`);
  return [
    ["first_screen", async () => { await p.goto(BASE + "/"); await p.waitForTimeout(1500); }],
    ["stored_answer", async () => { await p.click("#seeworked"); await p.waitForSelector("#out .bottomline .hl", { timeout: 60000 }); await p.waitForTimeout(1200); }],
    ["entity_card", async () => { await p.goto(BASE + "/?node=NCBIGene%3A5743"); await p.waitForSelector("#pcardsec", { timeout: 30000 }).catch(() => {}); await p.waitForTimeout(1200); }],
    ["drawer", async () => { const a = await p.$("#pcardsec a.why"); if (a) { await a.click(); await p.waitForTimeout(1200); } }],
    ["numbers", async () => { await p.goto(BASE + "/"); await p.waitForTimeout(800); await p.click(nav("numbers")); await p.waitForTimeout(3000); }],
  ];
}
(async () => {
  const b = await chromium.launch();
  for (const phone of [false, true]) for (const theme of ["dark", "light"]) {
    const ctx = await b.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: 1280, height: 900 }, deviceScaleFactor: phone ? 2 : 1, isMobile: phone, hasTouch: phone });
    await ctx.addInitScript(t => { try { localStorage.setItem("tmkg3_theme", t); } catch (e) {} }, theme);
    const p = await ctx.newPage(); const errs = []; p.on("pageerror", e => errs.push(String(e)));
    for (const [name, step] of await states(p, phone)) {
      const key = `${phone ? "390" : "1280"}/${theme}/${name}`;
      await step(); await p.addScriptTag({ content: axeSrc });
      const v = await p.evaluate(async () => (await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] }, rules: { region: { enabled: false } } }))
        .violations.map(x => ({ id: x.id, impact: x.impact, n: x.nodes.length, sample: x.nodes.slice(0, 2).map(n => n.target.join(" ") + " :: " + (n.any[0] || n.all[0] || n.none[0] || {}).message) })));
      report.axe[key] = v.map(x => `${x.id}/${x.impact} x${x.n}`);
      for (const x of v) if (x.impact === "serious" || x.impact === "critical") fail(`axe ${key}: ${x.id} ${x.sample.join(" | ")}`);
      if (phone) {
        const o = await p.evaluate(() => { const W = innerWidth, bad = [];
          document.querySelectorAll("body *").forEach(el => { const r = el.getBoundingClientRect(); if (r.width && r.right > W + 1 && getComputedStyle(el).position !== "fixed") {
            for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const ox = getComputedStyle(a).overflowX; if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") return; }
            bad.push(el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + "." + String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className).split(" ")[0]); } });
          return { scrollW: document.documentElement.scrollWidth, W, bad: bad.slice(0, 6) }; });
        report.overflow[key] = o; if (o.scrollW > o.W + 1 || o.bad.length) fail(`390 px overflow ${key}: page ${o.scrollW}px, ${o.bad.join(", ")}`);
        if (SHOTS && theme === "dark") { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, `${name}_390.png`) }); }
      }
    }
    if (errs.length) fail(`page errors ${theme}: ${errs.slice(0, 2).join(" | ")}`);
    if (!phone && theme === "dark") {            // keyboard: the graph of the stored answer, then the drawer on the entity card
      await p.goto(BASE + "/"); await p.evaluate(() => localStorage.removeItem("tmkg3_persona")); await p.goto(BASE + "/"); await p.waitForTimeout(800); await p.click("#seeworked"); await p.waitForSelector("#out svg.graph g[data-id]", { timeout: 60000 }); await p.waitForTimeout(800);
      await p.focus("#out svg.graph g[data-id]"); await p.keyboard.press("ArrowRight"); await p.keyboard.press("ArrowRight");
      const moved = await p.evaluate(() => document.activeElement && document.activeElement.closest("svg.graph") ? document.activeElement.getAttribute("aria-label") : null);
      const shown = await p.evaluate(() => !document.querySelector("#hovercard").hidden);
      await p.keyboard.press("Enter"); const pinned = await p.evaluate(() => document.querySelector("#hovercard").classList.contains("pinned") && document.querySelector("#hovercard").contains(document.activeElement));
      await p.keyboard.press("Escape"); const back = await p.evaluate(() => document.querySelector("#hovercard").hidden && !!document.activeElement.closest("svg.graph"));
      const stops = await p.evaluate(() => document.activeElement.closest("svg.graph").querySelectorAll('[tabindex="0"]').length);
      report.keyboard.graph = { moved, card_on_focus: shown, enter_pins_and_takes_focus: pinned, escape_returns_focus: back, tab_stops_in_graph: stops };
      if (!moved || !shown || !pinned || !back || stops !== 1) fail("graph keyboard walk: " + JSON.stringify(report.keyboard.graph));
      await p.goto(BASE + "/?node=NCBIGene%3A5743"); await p.waitForSelector("#pcardsec a.why", { timeout: 30000 }).catch(() => {});
      if (await p.$("#pcardsec a.why")) { await p.focus("#pcardsec a.why"); await p.keyboard.press("Enter"); await p.waitForTimeout(900);
        const inD = await p.evaluate(() => document.querySelector("#drawer").contains(document.activeElement)); await p.keyboard.press("Escape"); await p.waitForTimeout(500);
        const out = await p.evaluate(() => document.querySelector("#drawer").hidden && document.activeElement && document.activeElement.matches("a.why"));
        report.keyboard.drawer = { focus_moves_in: inD, escape_closes_and_returns: !!out }; if (!inD || !out) fail("drawer keyboard walk: " + JSON.stringify(report.keyboard.drawer)); }
    }
    await ctx.close();
  }
  await b.close(); console.log(JSON.stringify(report, null, 1)); process.exit(report.failures.length ? 1 : 0);
})();
