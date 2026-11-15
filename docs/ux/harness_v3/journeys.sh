#!/bin/bash
# The browser journeys on the front page (jsdom + bridge.py; no port, no hosted server). Writes one JSON per journey next to this file.
# Run from anywhere:  bash docs/ux/harness_v3/journeys.sh
export PATH=/opt/homebrew/bin:$PATH NODE_USE_ENV_PROXY=1 HOME=/tmp NODE_PATH=/tmp/tmkg/jsdom/node_modules
# The journeys judge the pages, not the model: the in-process app runs the offline stand-in even where .env holds
# NVIDIA_API_KEY (the Mac; a live trial report took > 60 s and blocked the next step of J5). JOURNEYS_LIVE=1 keeps the
# configured backend for the journeys (the click gate has its own switch, GATE_LIVE=1).
[ "${JOURNEYS_LIVE:-0}" = 1 ] || export TMKG_LLM_BACKEND=offline
H=$(cd "$(dirname "$0")" && pwd); R=$(cd "$H/../../.." && pwd)             # the checkout this script is in (Mac or cloud)
PY=$R/.venv/bin/python; [ -x "$PY" ] || PY=$(command -v python3)
# CC-46 click gate FIRST: the routes (one front page at /, the first page at /classic, no /v2), every persona ASK/OPEN pill
# on the front page, every chip, every worked example, the tested-question strip,
# the demo button and the four forms must show a busy state, change their answer area and finish; with the limiter full a
# typed question must show the limit message and "Try again in N min". Any failure stops here (exit 1): no redeploy.
# Result: docs/ux/harness_v3/click_gate.json. STRICT=1 (on the Mac, where the Pharos data is) also fails on error answers.
# SKIP_GATE=1 bash journeys.sh: run the journeys WITHOUT the gate (local debugging only; never before a redeploy).
if [ "${SKIP_GATE:-0}" = 1 ]; then echo "click_gate skipped (SKIP_GATE=1)"
else
  ( cd /tmp/tmkg/jsdom && node $H/click_gate.js $PY $H/bridge.py $H/click_gate.json ); gate=$?
  echo "click_gate exit=$gate"
  if [ "$gate" != 0 ]; then echo "click gate failed: see $H/click_gate.json" >&2; exit 1; fi
fi
FAILED=0   # a journey that exits non-zero or reports script errors makes the whole script exit 1 (CC-46)
# FIX2 F7 (S08 item 2): the actions go to node as @<file>, not on its command line (J10, J12a, J12b were killed, exit 137, on the Mac)
run() { local name=$1 page=$2 where=$3 acts=$4 rc af; af=$(mktemp "${TMPDIR:-/tmp}/tmkg_acts.XXXXXX"); printf '%s' "$acts" > "$af"
  ( cd /tmp/tmkg/jsdom && timeout 400 node $H/run_page_v3.js $PY $H/bridge.py "$page" "$where" "@$af" > $H/$name.json ); rc=$?; rm -f "$af"
  if [ "$rc" = 0 ] && ! "$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("script_errors", 1) == 0 and "harness_error" not in d else 1)' "$H/$name.json"; then rc=1; fi
  echo "$name exit=$rc"; [ "$rc" = 0 ] || FAILED=1; }
CHOOSE='{"click":".persona[data-id=\"physician_scientist\"]","wait":1500}'
run J1_egfr_card_hover_slider / "" '[{"type":["#hq","EGFR"]},{"submit":"#hf"},{"waitFor":"#pchain .pathbox","wait":2500},{"snap":"card"},
 {"hover":"#cg g[data-id]","wait":300},{"snap":"hover_node"},{"hover":"#cg line.hit","wait":800},{"snap":"hover_edge"},
 {"pin":"#cg line.hit","wait":800},{"snap":"pinned_edge"},{"click":"#hovercard [data-hcedge]","wait":1500},{"snap":"side_panel_from_edge"},
 {"click":"#dclose","wait":400},{"slider":["#thr","0.6"],"wait":400},{"snap":"slider_0.6"},{"slider":["#thr","0.9"],"wait":400},{"snap":"slider_0.9"},
 {"click":"#pchain .h","wait":400},{"snap":"hop_card_pinned"}]'
run J2_ptgs2_demo_bottom_line / "" '['"$CHOOSE"',{"click":"#tabPharos","wait":800},{"click":"#pstrip [data-demo=\"pharos_demo_1\"]"},{"waitFor":"#pansw .bottomline","wait":800},{"snap":"demo1_physician"},
 {"click":"#pansw table[data-answer] a.chip","wait":1500},{"snap":"chip_side_panel"},{"click":"#dclose","wait":400},
 {"click":"#badge","wait":200},{"click":".pop button[data-id=\"computational_biologist\"]","wait":800},{"snap":"demo1_after_switch_to_comp_bio"},
 {"click":"#pstrip [data-demo=\"pharos_demo_2\"]"},{"waitFor":"#pansw .bottomline","wait":800},{"snap":"demo2_comp_bio"}]'
run J3_typed_ask_abstain / "" '['"$CHOOSE"',{"click":"#tabPharos","wait":500},{"type":["#pquestion","Which diseases is the gene PTGS2 associated with, and which source says so?"]},{"submit":"#pask"},{"waitFor":"#pansw .bottomline","timeout":60000,"wait":500},{"snap":"typed_ask_offline"}]'
run J4_trial_example_tulip / "" '[{"click":"#xlist button[data-x=\"x05\"]"},{"waitFor":"#out .bottomline","timeout":60000,"wait":1500},{"snap":"tulip_report"},
 {"click":"#out .claim a.chip","wait":1500},{"snap":"evidence_side_panel"},{"click":"#dclose","wait":400},
 {"click":"#out details[data-sec] summary","wait":300},{"hover":"#out svg.graph line.hit","wait":300},{"snap":"trial_graph_hover_edge"},
 {"click":"#badge","wait":200},{"click":".pop button[data-id=\"drug_developer\"]","wait":800},{"snap":"tulip_after_switch_to_drug_developer"}]'
run J5_trial_typed_question / "" '['"$CHOOSE"',{"click":"#tabTrials","wait":500},{"type":["#q","Why did mepolizumab fail in METREO for COPD, and which patients might respond?"]},{"submit":"#f"},{"waitFor":"#out .bottomline","timeout":60000,"wait":1500},{"snap":"metreo_report"},
 {"click":"#proglist [data-prog=\"P20\"] [data-askfail]","wait":200},{"waitFor":"#out .bottomline","timeout":60000,"wait":1000},{"snap":"program_card_ask"},
 {"type":["#progq","dalcetrapib"]},{"submit":"#progf"},{"waitFor":"#pexplain .prog","timeout":30000,"wait":500},{"snap":"program_search_panel"}]'
run J6_persona_switch_card_order / "#pharos/node/NCBIGene%3A1956" '[{"waitFor":"#pcardsec","wait":2000},{"snap":"first_role"},
 {"click":"#badge","wait":200},{"click":".pop button[data-id=\"bench_scientist\"]","wait":1500},{"snap":"bench_scientist"},
 {"click":"#badge","wait":200},{"click":".pop button[data-id=\"clinician\"]","wait":1500},{"snap":"clinician"},
 {"click":"#badge","wait":200},{"click":".pop button[data-id=\"drug_developer\"]","wait":1500},{"snap":"drug_developer"}]'
run J7_shared_link_hash / "#pharos/node/MONDO%3A0005148" '[{"waitFor":"#pcardsec","wait":2500},{"snap":"shared_hash"}]'
run J7b_shared_link_query / "?node=NCBIGene%3A5743" '[{"waitFor":"#pcardsec","wait":2500},{"snap":"shared_query"}]'
run J8_examples_query_and_card / "" '[{"click":"#xtabs [data-xt=\"drug_developer\"]","wait":300},{"snap":"drug_developer_tab"},{"click":"#xlist button[data-x=\"x15\"]"},{"waitFor":"#out .bottomline","timeout":60000,"wait":800},{"snap":"portfolio_query"},
 {"click":"#xmain button[data-x=\"x13\"]"},{"waitFor":"#pcardsec","timeout":30000,"wait":2500},{"snap":"medchem_card_echo"}]'
run J9_info_tabs / "" '['"$CHOOSE"',{"click":"#tabNumbers","wait":2500},{"snap":"numbers"},{"click":"#tabBuild","wait":2500},{"snap":"build"},{"click":"#tabGeneralize","wait":2500},{"snap":"generalize"},{"click":"#tabJudges","wait":2000},{"snap":"judges"},
 {"click":"#tabExplore","wait":300},{"type":["#sq","TULIP"]},{"submit":"#sf","wait":800},{"click":"#results div[data-id]","wait":1500},{"hover":"#eg g[data-id]","wait":300},{"snap":"explore_hover"}]'
# PK3 (12 Nov): the first screen states the result with its number and opens the stored TULIP-1 report in one click; the
# answer has its four tiles, its blocks in the reading order and the graph; Numbers opens with the three-row table.
# FIX1 (14 Nov): the first screen also names the model behind the card predictions (F10), puts the buttons before the four steps
# (F12, G1-U-05), names the scope of the pruned counts (F6) and hides the Safari note behind "Page looks wrong?" (F12, G1-U-09).
run J10_first_screen_result / "" '[{"wait":1500},{"eval":"document.querySelector(\"#herogain\").textContent + \" | \" + document.querySelector(\"#heroline\").textContent"},
 {"eval":"document.querySelector(\"#heromodel\").textContent + \" | buttons before steps: \" + !!(document.querySelector(\"#seeworked\").compareDocumentPosition(document.querySelector(\"#howsteps\")) & 4) + \" | \" + document.querySelector(\"#about\").textContent + \" | safari note open: \" + document.querySelector(\"#safarinote\").closest(\"details\").open"},{"snap":"first_screen"},
 {"click":"#seeworked"},{"waitFor":"#out .bottomline .hl","timeout":60000,"wait":1500},{"eval":"[...document.querySelectorAll(\"#out .tiles4 h4\")].map(x => x.textContent).join(\",\") + \" | \" + [...document.querySelectorAll(\"#out [data-stage]\")].map(x => x.dataset.stage).join(\",\")"},{"snap":"worked_answer"},
 {"click":"#navNumbers","wait":3000},{"eval":"[...document.querySelectorAll(\"#numbers h2\")].slice(0, 8).map(x => x.textContent).join(\" / \") + \" | \" + [...document.querySelectorAll(\"#num-lead tbody tr td:first-child\")].map(x => x.textContent).join(\",\") + \" | folds: \" + document.querySelectorAll(\"#num-lead-folds li\").length"},{"snap":"numbers"}]'
# PK12 (13 Nov): axe-core on four states of the front page (first screen, the stored TULIP-1 answer with its graph, Numbers, the trial-graph
# explorer); any serious or critical violation fails the journey and so `make harness`. axe-core is installed with jsdom (Makefile: jsdom).
run J11_axe_accessibility / "" '[{"wait":1200},{"axe":"first_screen"},{"click":"#seeworked"},{"waitFor":"#out .bottomline .hl","timeout":60000,"wait":1500},{"axe":"worked_answer"},
 {"click":"#navNumbers","wait":3000},{"axe":"numbers"},{"click":"#navGraph","wait":800},{"axe":"explore"},{"click":"#navJudges","wait":2500},{"axe":"judges"}]'
# PK15 (14 Nov): journey J12, Amazon Neptune beside this server's graph, with the host's status file faked by bridge.py (NEPTUNE_FAKE;
# where no card index exists the bridge answers from the Pharos test fixture). (a) available: "Run this on Amazon Neptune" -> one line
# with the counts; (b) asleep: "Wake it" -> waking -> available -> the question runs there by itself (two 10 s status polls);
# (c) a Neptune error: the local answer stays, a plain note, no error text; (d) not set up: no control and no "Runs on" line in the DOM.
NEP_ANS='{"click":".persona[data-id=\"physician_scientist\"]","wait":1500},{"click":"#tabPharos","wait":800},{"click":"#pstrip [data-demo=\"pharos_demo_1\"]"},{"waitFor":"#pansw .bottomline","timeout":60000,"wait":800}'
NEP_EV='{"eval":"JSON.stringify({state: (document.querySelector(\"#pansw .nepctl\") || {dataset: {}}).dataset.state || null, control: (document.querySelector(\"#pansw .nepctl\") || {textContent: \"\"}).textContent.trim(), rows: document.querySelectorAll(\"#pansw table[data-answer] tr[data-row]\").length, status_line: document.querySelector(\"#paskline\").className + \": \" + document.querySelector(\"#paskline\").textContent.trim(), runson: !!document.querySelector(\"#runson\")})"}'
NEPTUNE_FAKE=available run J12a_neptune_available / "" '['"$NEP_ANS"',{"waitFor":"#pansw .nepctl [data-nep=\"run\"]","wait":300},'"$NEP_EV"',{"click":"#pansw .nepctl [data-nep=\"run\"]"},{"waitFor":"#pansw .nepctl[data-state=\"available\"] .nepline:not(:empty)","timeout":30000,"wait":500},'"$NEP_EV"',{"snap":"neptune_answered"}]'
NEPTUNE_FAKE=asleep run J12b_neptune_wake_then_run / "" '['"$NEP_ANS"',{"waitFor":"#pansw .nepctl [data-nep=\"wake\"]","wait":300},'"$NEP_EV"',{"click":"#pansw .nepctl [data-nep=\"wake\"]","wait":800},'"$NEP_EV"',{"waitFor":"#pansw .nepctl[data-state=\"available\"] .nepline:not(:empty)","timeout":60000,"wait":500},'"$NEP_EV"',{"snap":"neptune_woken_and_answered"}]'
# FIX2 / S17 item 2: J12e, the host takes the wake but still reports "stopped" for two status reads; the page must stay on
# "waking" (no second "Wake it" button) and then run the question there by itself.
NEPTUNE_FAKE=asleep_flicker run J12e_neptune_wake_flicker / "" '['"$NEP_ANS"',{"waitFor":"#pansw .nepctl [data-nep=\"wake\"]","wait":300},{"click":"#pansw .nepctl [data-nep=\"wake\"]","wait":800},'"$NEP_EV"',{"wait":11000},'"$NEP_EV"',{"waitFor":"#pansw .nepctl[data-state=\"available\"] .nepline:not(:empty)","timeout":60000,"wait":500},'"$NEP_EV"',{"snap":"neptune_flicker_then_answered"}]'
NEPTUNE_FAKE=error run J12c_neptune_error_local_answer / "" '['"$NEP_ANS"',{"waitFor":"#pansw .nepctl [data-nep=\"run\"]","wait":300},{"click":"#pansw .nepctl [data-nep=\"run\"]"},{"waitFor":"#pansw .nepctl[data-state=\"unavailable\"]","timeout":30000,"wait":500},'"$NEP_EV"',{"snap":"neptune_error"}]'
NEPTUNE_FAKE=not_configured run J12d_neptune_not_configured / "" '['"$NEP_ANS"',{"wait":800},'"$NEP_EV"']'
# Regression (11 Nov, owner decision: "/" is the one front page, /v2 is gone). R1: the first page, now only at /classic, still
# opens a shared card link. R2: a link to the old address /v3, shared before the swap, still opens the same card on "/".
rm -f $H/R2_v2_unchanged.json                                              # the /v2 journey of before 11 Nov; its page is gone
run R1_v1_unchanged /classic "#pharos/node/NCBIGene%3A1956" '[{"wait":4000},{"snap":"v1_card"}]'
run R2_old_v3_link /v3 "?node=NCBIGene%3A1956" '[{"waitFor":"#pcardsec","wait":2500},{"snap":"old_link_card"}]'
exit $FAILED
