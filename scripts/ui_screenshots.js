// Screenshots of the front page for review (PK3: five states at 1440 px and 390 px, plus the docked lineage panel and the light theme).
// Usage: python3 -m tests.fixtures.pharos_fixture <dir>          (a tiny Pharos fixture; or point at the real data root)
//        TMKG_PHAROS_ROOT=<dir> TMKG_PHAROS_INDEX=off python3 pipelines/app.py &      (or uvicorn app:app --port 8000 from pipelines/)
//        node scripts/ui_screenshots.js [out_dir] [base_url]
// Needs Playwright with Chromium (pre-installed in the cloud container; on the Mac: npm i playwright). Fails (exit 1) on a script
// error in the page or a page wider than its window.
const { chromium } = require('playwright');
const out = process.argv[2] || 'docs/ux/pk3_shots';
const base = (process.argv[3] || 'http://localhost:8000/').replace(/\/?$/, '/');
const STORED_Q = 'Which biological processes involve the protein encoded by PTGS2?';     // a stored 50-row answer (outputs/examples)
const problems = [];
(async () => {
  require('fs').mkdirSync(out, { recursive: true });
  const b = await chromium.launch();
  for (const [w, h, tag] of [[1440, 900, '1440'], [390, 844, '390']]) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: w < 500 ? 2 : 1 });
    const p = await ctx.newPage();
    p.on('pageerror', e => problems.push(`${tag}: page error ${e.message}`));
    const shot = async (name, full = true) => {
      const wide = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (wide > 1) problems.push(`${tag} ${name}: page is ${wide} px wider than the window`);
      await p.screenshot({ path: `${out}/${tag}_${name}.png`, fullPage: full });
    };
    const ready = async sel => { await p.waitForSelector(sel, { timeout: 30000 }).catch(() => problems.push(`${tag}: no ${sel}`)); await p.waitForTimeout(1800); };
    await p.goto(base); await p.evaluate(() => localStorage.clear()); await p.goto(base);
    await p.waitForFunction(() => !document.querySelector('#about').textContent.startsWith('Loading'), null, { timeout: 60000 }).catch(() => {});
    await p.waitForTimeout(800);
    await shot('1_first_screen');
    // the trial example ("See a worked answer": the stored TULIP-1 report)
    await p.click('#seeworked'); await ready('#out .bottomline .hl');
    await shot('2_trial_example');
    if (w >= 1200) { await p.click('#out .claim a.chip'); await p.waitForTimeout(1200); await shot('2b_trial_lineage_docked', false); await p.keyboard.press('Escape'); }
    // a tested question
    await p.evaluate(() => { tab('pharos'); }); await p.waitForTimeout(400);
    await p.evaluate(() => document.querySelector('#pstrip [data-demo]').click()); await ready('#pansw .bottomline .hl');
    await p.evaluate(() => document.querySelector('#pansw').scrollIntoView()); await p.waitForTimeout(300);
    await shot('3_tested_answer');
    // a stored 50-row answer, with the lineage of its first link
    await p.fill('#pquestion', STORED_Q); await p.evaluate(() => document.querySelector('#pask button[type=submit]').click());
    await ready('#pansw .bottomline .hl'); await p.evaluate(() => document.querySelector('#pansw').scrollIntoView()); await p.waitForTimeout(300);
    await shot('3b_stored_answer_50_rows');
    await p.evaluate(() => document.querySelector('#pansw table[data-answer] a.chip').click()); await p.waitForTimeout(1500);
    await shot('3c_why_this_link', false); await p.keyboard.press('Escape'); await p.waitForTimeout(300);
    // the entity card
    await p.fill('#pq', 'PTGS2'); await p.evaluate(() => document.querySelector('#pf button[type=submit]').click()); await ready('#pcardsec');
    await p.evaluate(() => document.querySelector('#pcardsec').scrollIntoView()); await p.waitForTimeout(300);
    await shot('4_entity_card');
    // Numbers
    await p.evaluate(() => go('numbers')); await ready('#num-lead');
    await shot('5_numbers');
    if (w >= 1200) { await p.evaluate(() => { setTheme(true); showStart(); }); await p.waitForTimeout(500); await shot('1b_first_screen_light', false); await p.evaluate(() => setTheme(false)); }
    await ctx.close();
  }
  await b.close();
  console.log(JSON.stringify({ out, problems }, null, 1));
  process.exit(problems.length ? 1 : 0);
})();
