# Click inventory, `/` and `/v3` (CC-46, 9 Nov 2026)

Built by reading `pipelines/app_page.html` (served at `/`) and `pipelines/app_page_v3.html` (served at `/v3`; the worked
examples are injected by `pipelines/persona_examples.py`), not from memory. Every function in the Handler column exists in
that page's file (checked by `tests/test_click_inventory.py`). "/ + /v3" means both pages bind the control the same way.

Why this exists: in Chrome the persona ASK pills looked dead (session 24 addendum). `runPill()` -> `pharosAsk()` wrote
"Working...", the answer or the error into `#pansw` at the bottom of the Pharos tab; nothing scrolled and nothing changed
near the pill, and when the server answered 429 (20 questions per visitor per hour, visitor = first X-Forwarded-For
address, shared by the owner's browser and Claude Science's checks) the red box was also out of view. `#hchips` ->
`quickStart()` had the same shape.

What every ASK / OPEN path does now (CC-46 item 2; `askBegin` / `askFetch` in both pages):
1. the clicked pill or button turns busy at once (disabled, spinner, "asking...", `aria-busy`), until the answer arrives;
2. the answer area (`#pansw`, `#pcard` / `#pecho`, `#out`) is scrolled into view BEFORE the request;
3. a sticky status line at the top of the tab (`#paskline` for Pharos, `#taskline` for trials) says
   "Asking: <question> ... N s" and then becomes the answer's headline or the error;
4. a 429 puts the server's message AND "Try again in N min" (from Retry-After) in that status line; "0 questions left
   this hour" appears next to the ask boxes (`.askleft`, shown when N <= 5, from `X-Ask-Remaining` / `/api/ask_quota`);
5. a network failure says "The server did not answer..." instead of leaving "Working..." (every ask goes through
   `askFetch`, which wraps `fetch` in try/catch); a dropped event stream asks `/api/ask_quota` whether it was the limit.

## CC-46b (9 Nov, after the Mac run: 109/110)

The one failure on the Mac was the gate, not the page: bench_scientist pills #2 and #3 are both PTGS2 Pharos questions and,
without a NIM key, both get the identical offline stand-in answer, so "answer container differs from the previous answer"
never fired for #3 (90 s timeout) although the busy state and status line were set and the server answered in 2.9 s.
Changes:
1. Pages (`/` and `/v3`): the status line echoes the question while busy ("Asking: <q> ... N s") AND when done or failed
   ("<q>: no rows" / "<q>: 1 row" / "<q>: N rows", plus " - <headline>" when the answer has one; tested questions
   "<q>: check passed, N rows"; trial reports "<q>: <headline>" or "<q>: report ready, N sections"; errors "<q>: <message>").
   Two identical answers to two different questions now differ visibly.
2. Gate (`click_gate.js`): "changed" and "finished" are judged against the snapshot taken right after the click (the busy
   state), not the previous answer; "the status line, no longer busy, contains the question the page said it was asking"
   (the `<b>` of the busy line) is accepted as the completion signal too. New section `repeat`: the same tested question and
   the same typed question clicked twice in a row on each page (identical answers by construction: the Mac failure).
   Each row now records `asked`, `echoed`, `changed_vs_busy`; `status_line` keeps 500 characters (was 220).
3. `SKIP_GATE=1 bash docs/ux/harness_v3/journeys.sh` runs the journeys without the gate (local debugging only). Default
   unchanged: gate first, stop with exit 1 on any failure.
4. `STRICT=1`: the deliberate 429 checks expect an error answer and never count as one (excluded inside `check()`, not
   patched afterwards); the summary has `error_answers` (excluding the 429 rows).

Re-run in the cloud checkout (no Pharos layer, no card index; jsdom 24.1.3, 9 Nov 07:10-07:18 UTC):

| Run | Total | Passed | Failed | error_answers | 429 rows |
|---|---|---|---|---|---|
| `bash journeys.sh` (gate, default) | 118 | 118 | 0 | 65 (no Pharos layer here) | 6/6 pass |
| `STRICT=1` gate | 118 | 53 | 65 | 65 | 6/6 pass (not counted as errors) |
| `GATE_ONLY=repeat` | 8 | 8 | 0 | - | - |

118 = the 110 rows below + 8 `repeat` rows. The 65 STRICT failures are all "error answer (STRICT)": "no built Pharos layer"
in this checkout; STRICT is meant for the Mac. Journeys after the gate (cloud): J3, J4, J5, J9, R1, R2 exit 0; J1, J2, J6,
J7, J7b, J8 exit 1, each a harness wait for the Pharos card / tested-question answer (no card index here); the same journey
on the unchanged HEAD pages times out the same way, so it is the data, not this change. The committed Mac journey JSONs were
kept as they were. On the Mac: `STRICT=1 bash docs/ux/harness_v3/journeys.sh` must give 118/118 and all journeys exit 0.

Column "Seen within 300 ms" is what the person must see after the click; "Where" is where it appears; "In view from the
click" says whether that place is on screen from where the person clicked (after this change).

## ASK and OPEN paths (the ones that were broken)

| Page | Selector | Handler | API endpoints | Seen within 300 ms | Where | In view from the click |
|---|---|---|---|---|---|---|
| / + /v3 | `#ppills .pill[data-tab=pharos]`, `#tpills .pill[data-tab=pharos]` (persona ASK pills) | `runPill` -> `pharosAsk` | `/api/pharos/ask` (v3: `/api/stored_answer/{id}` first when the question is stored) | pill busy "asking..."; status line "Asking: <q> ... 0 s"; answer area scrolled to | pill; `#paskline` (sticky, top of Pharos tab); `#pansw` | yes: pill in place, status line sticky, answer scrolled into view |
| / + /v3 | `.pill[data-tab=card]` (persona OPEN pills) | `runPill` -> `pharosFind` -> `pharosCard` | `/api/pharos/search`, `/api/pharos/card/{id}`, `/api/trials_for` | pill busy; "Opening: <name> ... 0 s"; card skeleton | pill; `#paskline`; `#pcard` | yes |
| / + /v3 | `#tpills .pill[data-tab=trials]` (persona trial pills) | `runPill` -> `ask` | `/api/ask_stream` (EventSource); v3: `/api/stored_answer/{id}` first; on stream error `/api/ask_quota` | pill busy; `#taskline` "Asking: <q>"; `#out` "Working..." + steps | pill; `#taskline`; `#out` | yes |
| / + /v3 | `#hchips .pill` (landing chips, start screen) | `quickStart` -> `pharosAsk` / `pharosFind` / `ask` | as the three rows above, by `data-kind` | main view opens on the right tab; status line busy; answer area scrolled to (the chip itself leaves the screen with the start screen) | `#paskline` / `#taskline`; `#pansw` / `#pcard` / `#out` | yes (status line) |
| / + /v3 | `#hf` submit (`Go`, start screen search) | `quickStart` (via the submit listener; `e.submitter` = Go) | `/api/pharos/search` + card, or `/api/pharos/ask` for a sentence | as the chips | as the chips | yes |
| / + /v3 | `#pask` submit (Ask, Pharos question box) | `pharosAsk` (submitter = Ask) | `/api/pharos/ask` | Ask button busy; status line; `#pansw` scrolled to | Ask button; `#paskline`; `#pansw` | yes |
| / + /v3 | `#pf` submit (Find) | `pharosFind` (openFirst; or the highlighted suggestion via `wireNodes` -> `pharosCard`) | `/api/pharos/search`, `/api/pharos/card/{id}`, `/api/trials_for` | Find busy; "Opening: <name>"; card skeleton | Find; `#paskline`; `#pcard` | yes |
| / + /v3 | `#pq` input (type-ahead) | `pharosFind` (openFirst false) | `/api/pharos/search` | suggestion list under the box (an error says "search is not available") | `#presults` | yes (under the box) |
| / + /v3 | `#pstrip [data-demo]` (tested-question strip) | `runDemo` | `/api/pharos/demo/{id}` | pill busy; "Running the tested question: <q>"; `#pansw` scrolled to | pill; `#paskline`; `#pansw` | yes |
| / + /v3 | `#pdemo` (Run the 5 demo questions) | inline listener -> `askBegin`, then `demoHtml` (v3 also renderGraphAnswer per answer) | `/api/pharos/demo`; v3 also `/api/summarize` per answer | button busy; "Running: the 5 demo questions"; `#pansw` scrolled to | `#pdemo`; `#paskline`; `#pansw` | yes |
| / + /v3 | `#f` submit (Ask, trials question box) | `ask` (submitter = Ask) | `/api/ask_stream`; `/api/ask_quota` on stream error | Ask busy; `#taskline`; `#out` "Working..." | Ask; `#taskline`; `#out` | yes |
| /v3 | `#xlist button[data-x]` (worked example, start screen) | `runExample` -> `ask` / `trialQuery` / `pharosFind` | `/api/stored_answer/{id}`, else `/api/ask_stream` / `/api/query` + `/api/summarize` / `/api/pharos/search` + card | main view on the example's tab; status line busy; answer area scrolled to (the start screen leaves) | `#taskline` / `#paskline`; `#out` / `#pecho` + `#pcard` | yes (status line) |
| /v3 | `#xmain button[data-x]` (worked example, role fold) | `runExample` | as above | button busy; status line; answer area scrolled to | button; status line; `#out` / `#pcard` | yes |
| /v3 | `[data-live]` (Re-run live on a stored answer) | `markStored` -> `pharosAsk` / `trialQuery` / `ask` with live = true | `/api/pharos/ask` / `/api/query` / `/api/ask_stream` | button busy; status line | button; status line; answer area | yes |
| /v3 | `[data-askfail]` (Ask why it failed, program card) | inline -> `ask` | `/api/ask_stream` | trials tab; `#taskline` busy; `#out` scrolled to | `#taskline`; `#out` | yes |
| /v3 | `[data-gene]` (Open GENE in the Pharos graph, program card) | inline -> `pharosFind` | `/api/pharos/search`, card | Pharos tab; "Opening: GENE" | `#paskline`; `#pcard` | yes |
| /v3 | `[data-ind]` (Find the indication in Pharos) | inline -> `pharosFind` (list) | `/api/pharos/search` | Pharos tab; suggestion list under the search box | `#presults` | yes |
| / + /v3 | `#ctrials a[data-trial]` (failed trial on a card) | inline -> `ask` | `/api/ask_stream` | trials tab; `#taskline` busy | `#taskline`; `#out` | yes |

## Role, navigation, tabs

| Page | Selector | Handler | API endpoints | Seen within 300 ms | Where | In view from the click |
|---|---|---|---|---|---|---|
| / + /v3 | `.persona` (role cards, start screen) | `choose` | (`tab` of the start tab: `/api/pharos/status` or the info pages) | main view opens, welcome line, role badge set | page top | yes |
| / + /v3 | `#badge` (role badge) | `popover` | none | role popover | under the badge | yes |
| / + /v3 | `#pop button[data-id]` (popover roles, Back to the role page) | `choose` (keepView) / inline | none | page re-ordered for the role, or the role page | page | yes |
| / + /v3 | `#tabPharos` ... `#tabBuild` (top tabs) | `tab` | `/api/pharos/status`, `/api/results` + `/api/file`, `/api/generalization`, `/api/agent-loop` + `/api/downloads`, `/api/status` | the tab's section (info pages load their tables) | under the tabs | yes |
| / + /v3 | `#bnav button[data-t]` (phone bottom bar) | inline -> `choose` (on the role page) + `tab` | as the tabs | the tab's section; on the role page the main view opens first (CC-43 fix) | page | yes |
| / + /v3 | `.step` (workflow steps 1-5, Pharos tab) | inline -> `step`, `toast` | none | focus to the search box, a scroll to the card section, or "Open an entity first" | page | yes |
| / + /v3 | `#trail a[data-nid]` (recent cards) | `wireNodes` -> `pharosCard` | `/api/pharos/card/{id}`, `/api/trials_for` | card skeleton | `#pcard` | yes |
| / + /v3 | `#generalize [data-go]` | inline -> `tab` | as the tabs | the named tab | page | yes |
| /v3 | `#xtabs [data-xt]` (examples by role) | `drawExampleTabs` | none | that role's examples | `#xlist` | yes |
| /v3 | `#xmore` (Show all N examples / Show fewer) | `drawExampleTabs` | none | the full list / one row | `#xlist` | yes |
| /v3 | `#xmainfold > summary`, `#mechfold > summary`, card `details > summary` | native `<details>` toggle | none | section opens | in place | yes |
| /v3 | `a.skip` (Skip to content) | native link (`#mainview`) | none | focus moves to the main view | page | keyboard only (off-screen until focused) |

## Pharos card, answers, evidence

| Page | Selector | Handler | API endpoints | Seen within 300 ms | Where | In view from the click |
|---|---|---|---|---|---|---|
| /v3 | `.nepctl [data-nep=run]` ("Run this on Amazon Neptune" under a Pharos graph answer; PK15, 14 Nov; replaces the hidden graph backend select) | `nepRun` -> `askBegin`, `askFetch` | `/api/pharos/ask` (backend neptune), `/api/neptune/status` | button busy "asking..."; status line "Running on Amazon Neptune: <q>"; then one line under the answer ("Same question on Amazon Neptune: N rows in T s; K of A evidence links identical.") or the note; the answer stays | button; `#paskline`; `.nepctl` under the bottom line | yes (the control is scrolled into view) |
| /v3 | `.nepctl [data-nep=wake]` ("Wake it (about N minutes)", Amazon Neptune asleep) | `nepWake` -> `nepPoll` -> `nepRun` | `/api/neptune/wake` (POST, not a question), `/api/neptune/status` every 10 s for at most 15 min, then `/api/pharos/ask` | button busy; status line "Waking: Amazon Neptune"; the control shows the waking progress; the question runs there by itself once it is awake | `.nepctl`; `#paskline` | yes |
| / + /v3 | `a.why[data-eid]` ("why" links on the card and in answers) | `wireEdges` -> `pharosExplain` | `/api/pharos/explain/{id}` | side panel opens with the link's record | `#drawer` (fixed, right / bottom sheet) | yes |
| / + /v3 | `[data-nid]` (node links, search results) | `wireNodes` -> `pharosCard` | `/api/pharos/card/{id}`, `/api/trials_for` | card skeleton | `#pcard` | yes (the list is under the box; the card is below it) |
| /v3 | `table[data-answer] a.chip`, `a.chip[data-evid]` (evidence chips) | `wireEdges` -> `pharosExplain` / inline -> `openEvidence` | `/api/pharos/explain/{id}` / `/api/evidence/{id}` | side panel | `#drawer` | yes |
| /v3 | `a.ref[data-eid]` (row refs in a bottom line) | `wireEdges` -> `pharosExplain` | `/api/pharos/explain/{id}` | side panel | `#drawer` | yes |
| /v3 | `button.ref[data-row]`, `button.ref[data-claim]` | `wireRefs` -> `flash` | none | the row / claim is scrolled to and flashes | in place | yes |
| / + /v3 | `button[data-sub]` (subgraph download) | `wireSub` -> `downloadSubgraph` | `/api/pharos/subgraph.zip` (POST) | a zip download | browser downloads | yes |
| /v3 | `#thr` (confidence slider) | `wireThreshold` (input / change) | none | links below the value hidden; "Showing N of M links" | `#thrinfo`, the card graph | yes |
| /v3 | `#cg` graph (hover / click / double-click a node or edge) | `wireHover` -> `showFor` / `hcShow` | none (double-click: `opts.onNode`) | hover card; click pins it | `#hovercard` (fixed, next to the pointer) | yes |
| /v3 | `#hovercard [data-hcedge]`, `[data-hcnode]`, `[data-hcx]` | `hcShow` -> `pharosExplain` / `openEvidence` / `hcHide` | `/api/pharos/explain/{id}` / `/api/evidence/{id}` | side panel / card / card closes | `#drawer` | yes |
| /v3 | `#plist button[data-pi]` (predicted links) | `selectPred` | none | that prediction's paths | `#pchain` | yes |
| /v3 | `#pchain .h` (path panel hops) | `hopCard` via the `#pchain` listeners -> `hcShow` | none | hop card (hover), pinned on click | `#hovercard` | yes |
| / + /v3 | `#cjson`, `#clink` (card JSON, share link) | inline | none | a JSON download / "link copied" toast | download / toast | yes |
| /v3 | `#ctrials [data-progcard]` | `openProgramPanel` | `/api/neighbourhood/{id}` | program panel | `#drawer` | yes |
| / + /v3 | `#dclose`, `#overlay`, `#dback` (side panel) | `closeDrawer` / inline -> `pharosExplain` | `/api/pharos/explain/{id}` (back) | panel closes / previous record | `#drawer` | yes |

## Trials tab and explorer

| Page | Selector | Handler | API endpoints | Seen within 300 ms | Where | In view from the click |
|---|---|---|---|---|---|---|
| / + /v3 | `#pdf`, `#sub` (report PDF, Subgraph) | inline | `/api/report.pdf`, `/api/report_subgraph.zip` | download; with no question a toast "Type a question first" (CC-43) | download / toast | yes |
| / + /v3 | `#json` (report JSON) | inline | none | download; with no report a toast "Ask a question first" (CC-43) | download / toast | yes |
| / + /v3 | `#out .claim` (a claim) | inline -> `drawGraph` | none | the section's graph opens with the claim's evidence highlighted | in place | yes |
| / + /v3 | `#out .evcard` (an evidence card) | `card` | `/api/evidence/{id}` | the record expands in place | in place | yes |
| /v3 | `#progf` submit, `#progq` input (program search) | `programSearch` | `/api/search` | result list; Find opens the first program | `#progres` / `#drawer` | yes |
| /v3 | `#progres [data-pid]` | `openProgramPanel` | `/api/neighbourhood/{id}` | program panel | `#drawer` | yes |
| /v3 | `[data-explore]` (Program in the trial graph) | inline -> `expand` | `/api/neighbourhood/{id}` | explorer tab with the program's neighbourhood | Explore tab | yes |
| / + /v3 | `#sf` submit (explorer search), `#results div[data-id]` | inline -> `expand` | `/api/search`, `/api/neighbourhood/{id}` | result list; the neighbourhood graph | `#results`; Explore tab | yes |

## Footer, downloads, info pages

| Page | Selector | Handler | API endpoints | Seen within 300 ms | Where | In view from the click |
|---|---|---|---|---|---|---|
| / + /v3 | `#attribution summary` (Data sources and licences) | native `<details>` | `/api/attribution` (loaded with the page by `loadAttribution`) | the licence list | footer | yes (the phone bar no longer covers it, CC-43) |
| / + /v3 | `#attnotice a[href="/DATA_LICENSES.md"]`, `#attlist a` | native links | `/DATA_LICENSES.md` / the sources' own sites | the licence file / a new tab | browser | yes |
| / + /v3 | Numbers / For judges download links (`/api/file?name=...`, `/api/downloads` list) | native links built by `showNumbers` / `showJudges` | `/api/file`, `/api/downloads` | the file | browser | yes |

## Harness result (click gate, `docs/ux/harness_v3/click_gate.js`)

Run 2026-11-09 06:02 UTC in the cloud checkout (bridge.py = FastAPI TestClient against this branch's app; jsdom 24).
**click gate: 110/110 passed**, 0 failed (`docs/ux/harness_v3/click_gate.json`). Outcomes: error shown 61, answered 47.

Read the outcomes with care: this checkout has no Pharos layer (`data/kg/` is on the Mac only), so every Pharos ask,
card and tested question answers with an error here ("The Pharos graph is not loaded ..."). The gate checks that the
click responds visibly and finishes (busy state, answer area changed, not "Working...", status line visible), so an
error shown on screen passes; trial questions and the stored answers answered. On the Mac, where the data is, run
`STRICT=1 bash docs/ux/harness_v3/journeys.sh`: then an error answer also fails the gate.

| # | Page | Role | Control | Result | Busy state seen | Outcome | ms |
|---|---|---|---|---|---|---|---|
| 1 | / | physician_scientist | `.pill[data-tab=pharos] #1` | PASS | on the control | error shown | 355 |
| 2 | / | physician_scientist | `.pill[data-tab=pharos] #2` | PASS | on the control | error shown | 354 |
| 3 | / | physician_scientist | `.pill[data-tab=card] #3` | PASS | on the control | error shown | 354 |
| 4 | / | physician_scientist | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 653 |
| 5 | / | clinician | `.pill[data-tab=card] #1` | PASS | on the control | error shown | 352 |
| 6 | / | clinician | `.pill[data-tab=card] #2` | PASS | on the control | error shown | 353 |
| 7 | / | clinician | `.pill[data-tab=pharos] #3` | PASS | on the control | error shown | 354 |
| 8 | / | clinician | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 561 |
| 9 | / | bench_scientist | `.pill[data-tab=card] #1` | PASS | on the control | error shown | 454 |
| 10 | / | bench_scientist | `.pill[data-tab=pharos] #2` | PASS | on the control | error shown | 354 |
| 11 | / | bench_scientist | `.pill[data-tab=pharos] #3` | PASS | on the control | error shown | 353 |
| 12 | / | bench_scientist | `.pill[data-tab=card] #4` | PASS | on the control | error shown | 353 |
| 13 | / | drug_developer | `.pill[data-tab=card] #1` | PASS | on the control | error shown | 354 |
| 14 | / | drug_developer | `.pill[data-tab=pharos] #2` | PASS | on the control | error shown | 353 |
| 15 | / | drug_developer | `.pill[data-tab=trials] #3` | PASS | on the control | answered | 610 |
| 16 | / | drug_developer | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 557 |
| 17 | / | computational_biologist | `.pill[data-tab=pharos] #1` | PASS | on the control | error shown | 358 |
| 18 | / | computational_biologist | `.pill[data-tab=card] #2` | PASS | on the control | error shown | 357 |
| 19 | / | computational_biologist | `.pill[data-tab=pharos] #3` | PASS | on the control | error shown | 358 |
| 20 | / | computational_biologist | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 704 |
| 21 | / |  | `#hchips .pill #1` | PASS | status line (control left the screen) | error shown | 354 |
| 22 | / |  | `#hchips .pill #2` | PASS | status line (control left the screen) | error shown | 352 |
| 23 | / |  | `#hchips .pill #3` | PASS | status line (control left the screen) | answered | 1204 |
| 24 | / |  | `#pstrip [data-demo] #1` | PASS | on the control | error shown | 253 |
| 25 | / |  | `#pstrip [data-demo] #2` | PASS | on the control | error shown | 251 |
| 26 | / |  | `#pstrip [data-demo] #3` | PASS | on the control | error shown | 251 |
| 27 | / |  | `#pstrip [data-demo] #4` | PASS | on the control | error shown | 252 |
| 28 | / |  | `#pstrip [data-demo] #5` | PASS | on the control | error shown | 257 |
| 29 | / |  | `#pdemo` | PASS | on the control | error shown | 253 |
| 30 | / |  | `#pask submit` | PASS | on the control | error shown | 455 |
| 31 | / |  | `#pf submit (Find)` | PASS | on the control | error shown | 252 |
| 32 | / |  | `#f submit` | PASS | on the control | answered | 8848 |
| 33 | / |  | `#hf submit (Go)` | PASS | status line (control left the screen) | error shown | 454 |
| 34 | /v3 | physician_scientist | `.pill[data-tab=pharos] #1` | PASS | on the control | answered | 366 |
| 35 | /v3 | physician_scientist | `.pill[data-tab=pharos] #2` | PASS | on the control | answered | 356 |
| 36 | /v3 | physician_scientist | `.pill[data-tab=card] #3` | PASS | on the control | error shown | 367 |
| 37 | /v3 | physician_scientist | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 284 |
| 38 | /v3 | clinician | `.pill[data-tab=card] #1` | PASS | on the control | error shown | 355 |
| 39 | /v3 | clinician | `.pill[data-tab=card] #2` | PASS | on the control | error shown | 357 |
| 40 | /v3 | clinician | `.pill[data-tab=pharos] #3` | PASS | on the control | answered | 472 |
| 41 | /v3 | clinician | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 264 |
| 42 | /v3 | bench_scientist | `.pill[data-tab=card] #1` | PASS | on the control | error shown | 356 |
| 43 | /v3 | bench_scientist | `.pill[data-tab=pharos] #2` | PASS | on the control | answered | 484 |
| 44 | /v3 | bench_scientist | `.pill[data-tab=pharos] #3` | PASS | on the control | answered | 262 |
| 45 | /v3 | bench_scientist | `.pill[data-tab=card] #4` | PASS | on the control | error shown | 360 |
| 46 | /v3 | drug_developer | `.pill[data-tab=card] #1` | PASS | on the control | error shown | 355 |
| 47 | /v3 | drug_developer | `.pill[data-tab=pharos] #2` | PASS | on the control | answered | 338 |
| 48 | /v3 | drug_developer | `.pill[data-tab=trials] #3` | PASS | on the control | answered | 271 |
| 49 | /v3 | drug_developer | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 263 |
| 50 | /v3 | computational_biologist | `.pill[data-tab=pharos] #1` | PASS | on the control | answered | 404 |
| 51 | /v3 | computational_biologist | `.pill[data-tab=card] #2` | PASS | on the control | error shown | 264 |
| 52 | /v3 | computational_biologist | `.pill[data-tab=pharos] #3` | PASS | on the control | answered | 276 |
| 53 | /v3 | computational_biologist | `.pill[data-tab=trials] #4` | PASS | on the control | answered | 322 |
| 54 | /v3 |  | `#hchips .pill #1` | PASS | status line (control left the screen) | error shown | 354 |
| 55 | /v3 |  | `#hchips .pill #2` | PASS | status line (control left the screen) | answered | 361 |
| 56 | /v3 |  | `#hchips .pill #3` | PASS | status line (control left the screen) | answered | 321 |
| 57 | /v3 |  | `#pstrip [data-demo] #1` | PASS | on the control | error shown | 254 |
| 58 | /v3 |  | `#pstrip [data-demo] #2` | PASS | on the control | error shown | 254 |
| 59 | /v3 |  | `#pstrip [data-demo] #3` | PASS | on the control | error shown | 253 |
| 60 | /v3 |  | `#pstrip [data-demo] #4` | PASS | on the control | error shown | 255 |
| 61 | /v3 |  | `#pstrip [data-demo] #5` | PASS | on the control | error shown | 254 |
| 62 | /v3 |  | `#pdemo` | PASS | on the control | error shown | 254 |
| 63 | /v3 |  | `#pask submit` | PASS | on the control | error shown | 255 |
| 64 | /v3 |  | `#pf submit (Find)` | PASS | on the control | error shown | 255 |
| 65 | /v3 |  | `#f submit` | PASS | on the control | answered | 8347 |
| 66 | /v3 |  | `#hf submit (Go)` | PASS | status line (control left the screen) | error shown | 458 |
| 67 | /v3 | physician_scientist | `#xlist [data-x=x05] (start screen)` | PASS | status line (control left the screen) | answered | 282 |
| 68 | /v3 | physician_scientist | `#xlist [data-x=x06] (start screen)` | PASS | status line (control left the screen) | answered | 310 |
| 69 | /v3 | physician_scientist | `#xlist [data-x=x07] (start screen)` | PASS | status line (control left the screen) | error shown | 260 |
| 70 | /v3 | physician_scientist | `#xlist [data-x=x08] (start screen)` | PASS | status line (control left the screen) | answered | 257 |
| 71 | /v3 | clinician | `#xlist [data-x=x01] (start screen)` | PASS | status line (control left the screen) | answered | 258 |
| 72 | /v3 | clinician | `#xlist [data-x=x02] (start screen)` | PASS | status line (control left the screen) | answered | 263 |
| 73 | /v3 | clinician | `#xlist [data-x=x03] (start screen)` | PASS | status line (control left the screen) | answered | 326 |
| 74 | /v3 | clinician | `#xlist [data-x=x04] (start screen)` | PASS | status line (control left the screen) | answered | 294 |
| 75 | /v3 | bench_scientist | `#xlist [data-x=x09] (start screen)` | PASS | status line (control left the screen) | error shown | 355 |
| 76 | /v3 | bench_scientist | `#xlist [data-x=x10] (start screen)` | PASS | status line (control left the screen) | error shown | 358 |
| 77 | /v3 | bench_scientist | `#xlist [data-x=x11] (start screen)` | PASS | status line (control left the screen) | answered | 256 |
| 78 | /v3 | bench_scientist | `#xlist [data-x=x12] (start screen)` | PASS | status line (control left the screen) | error shown | 358 |
| 79 | /v3 | drug_developer | `#xlist [data-x=x13] (start screen)` | PASS | status line (control left the screen) | error shown | 356 |
| 80 | /v3 | drug_developer | `#xlist [data-x=x14] (start screen)` | PASS | status line (control left the screen) | error shown | 355 |
| 81 | /v3 | drug_developer | `#xlist [data-x=x15] (start screen)` | PASS | status line (control left the screen) | answered | 255 |
| 82 | /v3 | drug_developer | `#xlist [data-x=x16] (start screen)` | PASS | status line (control left the screen) | answered | 317 |
| 83 | /v3 | computational_biologist | `#xlist [data-x=x17] (start screen)` | PASS | status line (control left the screen) | answered | 257 |
| 84 | /v3 | computational_biologist | `#xlist [data-x=x18] (start screen)` | PASS | status line (control left the screen) | answered | 256 |
| 85 | /v3 | computational_biologist | `#xlist [data-x=x19] (start screen)` | PASS | status line (control left the screen) | error shown | 357 |
| 86 | /v3 | physician_scientist | `#xmain [data-x=x05] (role fold)` | PASS | on the control | answered | 274 |
| 87 | /v3 | physician_scientist | `#xmain [data-x=x06] (role fold)` | PASS | on the control | answered | 259 |
| 88 | /v3 | physician_scientist | `#xmain [data-x=x07] (role fold)` | PASS | on the control | error shown | 358 |
| 89 | /v3 | physician_scientist | `#xmain [data-x=x08] (role fold)` | PASS | on the control | answered | 256 |
| 90 | /v3 | clinician | `#xmain [data-x=x01] (role fold)` | PASS | on the control | answered | 259 |
| 91 | /v3 | clinician | `#xmain [data-x=x02] (role fold)` | PASS | on the control | answered | 266 |
| 92 | /v3 | clinician | `#xmain [data-x=x03] (role fold)` | PASS | on the control | answered | 275 |
| 93 | /v3 | clinician | `#xmain [data-x=x04] (role fold)` | PASS | on the control | answered | 260 |
| 94 | /v3 | bench_scientist | `#xmain [data-x=x09] (role fold)` | PASS | on the control | error shown | 356 |
| 95 | /v3 | bench_scientist | `#xmain [data-x=x10] (role fold)` | PASS | on the control | error shown | 358 |
| 96 | /v3 | bench_scientist | `#xmain [data-x=x11] (role fold)` | PASS | on the control | answered | 256 |
| 97 | /v3 | bench_scientist | `#xmain [data-x=x12] (role fold)` | PASS | on the control | error shown | 359 |
| 98 | /v3 | drug_developer | `#xmain [data-x=x13] (role fold)` | PASS | on the control | error shown | 561 |
| 99 | /v3 | drug_developer | `#xmain [data-x=x14] (role fold)` | PASS | on the control | error shown | 355 |
| 100 | /v3 | drug_developer | `#xmain [data-x=x15] (role fold)` | PASS | on the control | answered | 256 |
| 101 | /v3 | drug_developer | `#xmain [data-x=x16] (role fold)` | PASS | on the control | answered | 292 |
| 102 | /v3 | computational_biologist | `#xmain [data-x=x17] (role fold)` | PASS | on the control | answered | 324 |
| 103 | /v3 | computational_biologist | `#xmain [data-x=x18] (role fold)` | PASS | on the control | answered | 261 |
| 104 | /v3 | computational_biologist | `#xmain [data-x=x19] (role fold)` | PASS | on the control | error shown | 361 |
| 105 | / |  | `429: #pask submit` | PASS | on the control | error shown | 251 |
| 106 | / |  | `429: #f submit (event stream)` | PASS | on the control | error shown | 251 |
| 107 | / |  | `429: questions-left note` | PASS | - | - | - |
| 108 | /v3 |  | `429: #pask submit` | PASS | on the control | error shown | 255 |
| 109 | /v3 |  | `429: #f submit (event stream)` | PASS | on the control | error shown | 253 |
| 110 | /v3 |  | `429: questions-left note` | PASS | - | - | - |

The other journeys of `journeys.sh` in the same run (cloud checkout, no Pharos layer):

| Journey | Exit | Why |
|---|---|---|
| J1_egfr_card_hover_slider | 1 | timeout waiting for `#pchain .pathbox` (EGFR card needs the card index) |
| J2_ptgs2_demo_bottom_line | 1 | timeout waiting for `#pansw .bottomline` (tested question needs the Pharos layer) |
| J3_typed_ask_abstain | 0 | - |
| J4_trial_example_tulip | 0 | - |
| J5_trial_typed_question | 0 | - |
| J6_persona_switch_card_order | 1 | timeout waiting for `#pcardsec` (card index) |
| J7_shared_link_hash | 1 | timeout waiting for `#pcardsec` (card index) |
| J7b_shared_link_query | 1 | timeout waiting for `#pcardsec` (card index) |
| J8_examples_query_and_card | 1 | timeout waiting for `#pcardsec` (card index) |
| J9_info_tabs | 0 | - |
| R1_v1_unchanged | 0 | - |
| R2_v2_unchanged | 0 | - |

None of the six failures is a page script error (each JSON lists only harness waits that timed out); the committed
Mac runs of the same journeys (`docs/ux/harness_v3/J*.json`, kept as they were) had 0 script errors. `journeys.sh` now
exits 1 when any journey fails, so this checkout cannot be redeployed from without the card index; on the Mac all must pass.

**Offline by default (9 Nov, after the Mac run 117/118):** the one failure there (`/` `#f submit`, 90 s, still busy) was the
gate's own app calling the hosted model, because `.env` on the Mac holds `NVIDIA_API_KEY`; the page showed "Asking ... N s"
the whole time. The gate now starts its app with `TMKG_LLM_BACKEND=offline` (no model call, no credits); `GATE_LIVE=1`
keeps the configured backend. The repeat test no longer assumes two answers to the same question are identical.
