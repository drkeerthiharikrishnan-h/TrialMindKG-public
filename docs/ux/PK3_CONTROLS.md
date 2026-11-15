# Every control of the click inventory after PK3 (12 Nov 2026)

Generated from `docs/ux/CLICK_INVENTORY_2026-11-09.md` (every row whose page is the front page) against `pipelines/app_page_v3.html`:
each selector's ids and data attributes are looked up in the page (column "in the page"), and the column "where it is now" names its
place in the new layout. `tests/test_page_pk3.py::test_every_inventory_control_is_listed_and_exists` keeps this list true; the click gate
(`docs/ux/harness_v3/click_gate.js`) clicks the ASK / OPEN controls themselves.

New controls (PK3): `#seeworked` (See a worked answer), `#askown` (Ask your own question), `#tqlist [data-tq]` (tested questions on the
first screen), `#tqall`, `#xalso [data-rtab]` (the role's own questions), the top navigation `#topnav button[data-go]`, `#themesw` (Light / Dark),
the four tiles `.tiles4 a[data-jump]` and the in-answer outline `.blpin nav a`, `[data-morerows]` (rows 13 to N), `[data-ansjson]` (answer as JSON),
`[data-proxy]` (report downloads inside the report), `.gsel [data-gsec]` (section graph in the docked panel), `#tabDownloads`, `#tabAbout`.

| # | Selector (inventory) | Handler | In the page | Where it is now |
|---|---|---|---|---|
| 1 | `#ppills .pill[data-tab=pharos]`, `#tpills .pill[data-tab=pharos]` (persona ASK pills) | `runPill` -> `pharosAsk` | yes | Pharos graph section, role questions |
| 2 | `.pill[data-tab=card]` (persona OPEN pills) | `runPill` -> `pharosFind` -> `pharosCard` | yes | Pharos graph / Failed trials sections, role questions |
| 3 | `#tpills .pill[data-tab=trials]` (persona trial pills) | `runPill` -> `ask` | yes | Failed trials section, role questions |
| 4 | `#hchips .pill` (landing chips, start screen) | `quickStart` -> `pharosAsk` / `pharosFind` / `ask` | yes | first screen, 'Ask the graph' block, under the search box |
| 5 | `#hf` submit (`Go`, start screen search) | `quickStart` (via the submit listener; `e.submitter` = Go) | yes | first screen, 'Ask the graph' block |
| 6 | `#pask` submit (Ask, Pharos question box) | `pharosAsk` (submitter = Ask) | yes | Pharos graph section, 'Ask the Pharos graph a question' |
| 7 | `#pf` submit (Find) | `pharosFind` (openFirst; or the highlighted suggestion via `wireNodes` -> `pharosCard`) | yes | Pharos graph section, entity search |
| 8 | `#pq` input (type-ahead) | `pharosFind` (openFirst false) | yes | Pharos graph section, entity search |
| 9 | `#pstrip [data-demo]` (tested-question strip) | `runDemo` | yes | Pharos graph section (and the same list on the first screen, #tqlist) |
| 10 | `#pdemo` (Run the 5 demo questions) | inline listener -> `askBegin`, then `demoHtml` (v3 also renderGraphAnswer per answer) | yes | Pharos graph section, 'Ask the Pharos graph a question' |
| 11 | `#f` submit (Ask, trials question box) | `ask` (submitter = Ask) | yes | Failed trials section, question box |
| 12 | `#xlist button[data-x]` (worked example, start screen) | `runExample` -> `ask` / `trialQuery` / `pharosFind` | yes | first screen, 'Start from who you are' block (role tabs, three cards each) |
| 13 | `#xmain button[data-x]` (worked example, role fold) | `runExample` | yes | main view, fold 'Worked examples for this role' |
| 14 | `[data-live]` (Re-run live on a stored answer) | `markStored` -> `pharosAsk` / `trialQuery` / `ask` with live = true | yes | stored-answer line above any stored answer |
| 15 | `[data-askfail]` (Ask why it failed, program card) | inline -> `ask` | yes | failed-program cards (Failed trials section, program panel) |
| 16 | `[data-gene]` (Open GENE in the Pharos graph, program card) | inline -> `pharosFind` | yes | failed-program cards |
| 17 | `[data-ind]` (Find the indication in Pharos) | inline -> `pharosFind` (list) | yes | failed-program cards |
| 18 | `#ctrials a[data-trial]` (failed trial on a card) | inline -> `ask` | yes | entity card, block 'Failed trials that mention it' |
| 19 | `.persona` (role cards, start screen) | `choose` | yes | first screen, last block 'Or start as a role' |
| 20 | `#badge` (role badge) | `popover` | yes | top bar, right (role) |
| 21 | `#pop button[data-id]` (popover roles, Back to the role page) | `choose` (keepView) / inline | yes | top bar, role popover |
| 22 | `#tabPharos` ... `#tabBuild` (top tabs) | `tab` | yes | main view section switch (Ask group: Pharos graph / Failed trials; Numbers, For judges, Generalization, Build in their groups; the top bar names the groups) |
| 23 | `#bnav button[data-t]` (phone bottom bar) | inline -> `choose` (on the role page) + `tab` | yes | phone bar at the bottom (Ask, Examples, Graph, Numbers, Files, Judges, About) |
| 24 | `.step` (workflow steps 1-5, Pharos tab) | inline -> `step`, `toast` | yes | Pharos graph section, under the status line |
| 25 | `#trail a[data-nid]` (recent cards) | `wireNodes` -> `pharosCard` | yes | Pharos graph section, under the entity search |
| 26 | `#generalize [data-go]` | inline -> `tab` | yes | For judges > Generalization |
| 27 | `#xtabs [data-xt]` (examples by role) | `drawExampleTabs` | yes | first screen, 'Start from who you are' block |
| 28 | `#xmore` (Show all N examples / Show fewer) | `drawExampleTabs` | yes | first screen, under the example cards |
| 29 | `#xmainfold > summary`, `#mechfold > summary`, card `details > summary` | native `<details>` toggle | yes | main view, fold 'Worked examples for this role' |
| 30 | `a.skip` (Skip to content) | native link (`#mainview`) | yes | first focusable element of the page |
| 31 | `.nepctl [data-nep=run]` ("Run this on Amazon Neptune" under a Pharos graph answer; PK15, 14 Nov; replaces the hidden graph backend select) | `nepRun` -> `askBegin`, `askFetch` | yes | under the bottom line of a Pharos graph answer, only while Amazon Neptune is set up on the server |
| 32 | `.nepctl [data-nep=wake]` ("Wake it (about N minutes)", Amazon Neptune asleep) | `nepWake` -> `nepPoll` -> `nepRun` | yes | the same place, while Amazon Neptune is asleep |
| 33 | `a.why[data-eid]` ("why" links on the card and in answers) | `wireEdges` -> `pharosExplain` | yes | answers and cards: evidence table rows and card rows |
| 34 | `[data-nid]` (node links, search results) | `wireNodes` -> `pharosCard` | yes | cards, search results, prediction paths |
| 35 | `table[data-answer] a.chip`, `a.chip[data-evid]` (evidence chips) | `wireEdges` -> `pharosExplain` / inline -> `openEvidence` | yes | answer component, Evidence block (source chips); opens 'why this link' docked beside the answer on wide windows |
| 36 | `a.ref[data-eid]` (row refs in a bottom line) | `wireEdges` -> `pharosExplain` | yes | answer component, Bottom line block |
| 37 | `button.ref[data-row]`, `button.ref[data-claim]` | `wireRefs` -> `flash` | yes | answer component, Bottom line and Summary blocks |
| 38 | `button[data-sub]` (subgraph download) | `wireSub` -> `downloadSubgraph` | yes | answer component, Downloads and query block |
| 39 | `#thr` (confidence slider) | `wireThreshold` (input / change) | yes | entity card, Graph block (docked on wide windows) |
| 40 | `#cg` graph (hover / click / double-click a node or edge) | `wireHover` -> `showFor` / `hcShow` | yes | entity card, Graph block (docked on wide windows) |
| 41 | `#hovercard [data-hcedge]`, `[data-hcnode]`, `[data-hcx]` | `hcShow` -> `pharosExplain` / `openEvidence` / `hcHide` | yes | graphs (hover card) |
| 42 | `#plist button[data-pi]` (predicted links) | `selectPred` | yes | entity card, Predicted block |
| 43 | `#pchain .h` (path panel hops) | `hopCard` via the `#pchain` listeners -> `hcShow` | yes | entity card, Predicted block |
| 44 | `#cjson`, `#clink` (card JSON, share link) | inline | yes | entity card, Downloads and sharing block |
| 45 | `#ctrials [data-progcard]` | `openProgramPanel` | yes | entity card, block 'Failed trials that mention it' |
| 46 | `#dclose`, `#overlay`, `#dback` (side panel) | `closeDrawer` / inline -> `pharosExplain` | yes | the side panel (drawer, or docked beside the answer on wide windows) |
| 47 | `#pdf`, `#sub` (report PDF, Subgraph) | inline | yes | Failed trials section, question box (also as buttons in the report's Downloads block) |
| 48 | `#json` (report JSON) | inline | yes | Failed trials section (also in the report's Downloads block) |
| 49 | `#out .claim` (a claim) | inline -> `drawGraph` | yes | trial report, Evidence block (sections and claims) |
| 50 | `#out .evcard` (an evidence card) | `card` | yes | trial report, Evidence block, 'Evidence (n) inline' |
| 51 | `#progf` submit, `#progq` input (program search) | `programSearch` | yes | Failed trials section, 'Failed drug programs' |
| 52 | `#progres [data-pid]` | `openProgramPanel` | yes | Failed trials section, 'Failed drug programs' |
| 53 | `[data-explore]` (Program in the trial graph) | inline -> `expand` | yes | failed-program cards -> Graph (trial-graph explorer) |
| 54 | `#sf` submit (explorer search), `#results div[data-id]` | inline -> `expand` | yes | Graph (trial-graph explorer) |
| 55 | `#attribution summary` (Data sources and licences) | native `<details>` | yes | footer |
| 56 | `#attnotice a[href="/DATA_LICENSES.md"]`, `#attlist a` | native links | yes | footer (notice always visible) |
| 57 | Numbers / For judges download links (`/api/file?name=...`, `/api/downloads` list) | native links built by `showNumbers` / `showJudges` | yes | Numbers (source-file links, Downloads and For judges blocks) and the Downloads page |
