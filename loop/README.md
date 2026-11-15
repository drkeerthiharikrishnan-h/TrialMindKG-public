# The build loop

PLAN -> DESIGN -> RUN -> JUDGE -> ITERATE -> IMPROVE -> COMMIT, in files, so Claude Code, Codex, Claude Science or
the owner can continue from git alone. Written 2026-11-04 (plan: docs/MASTER_PLAN.md). Deadline: submit Nov 16, final Nov 17.

| Stage | What you do | File |
|---|---|---|
| PLAN | `python3 loop/loop.py next`: open packets, then the heaviest failing scorecard check nobody owns | loop/scorecard.yaml, loop/tasks/ |
| DESIGN | Open (or write) the task packet: objective, files to read, files you may edit, acceptance, stop rule. For science tasks write the pass/fail threshold BEFORE the run | loop/tasks/T-NNN.yaml |
| RUN | Do the work. A long job gets a `RUNNING on the Mac:` line in logs/RUNNING_*.txt and in NEXT_STEPS; nobody touches outputs/linkpred or data/kg meanwhile | logs/ |
| JUDGE | (1) the packet's `validations`; (2) `python3 loop/loop.py score`; (3) for science packets, a fresh-context reviewer answers four questions with file evidence: protocol (was it followed?), provenance (every number from a file?), comparability (same test set, same cap?), uncertainty (interval or caveat stated?). Unknown or missing evidence = not accepted | the packet, the ledger |
| ITERATE | At most 2 rework rounds per packet, then escalate to the owner. Never change acceptance criteria after seeing results | the packet |
| IMPROVE | One paragraph in the packet or the ledger row of what was learned (docs/PROJECT_MEMORY.md is archived); edit the continuity skill if a procedure or pitfall changed; adjust scorecard weights only in a commit that says why | docs/ |
| COMMIT | Tests green, NEXT_STEPS ticked, one small commit `[loop T-NNN] <what>` (science results: also `[science]`) on `main` | git |

## Rules
- Git `main` is the only channel. Pull at the start, push at the end. A handoff = packet + updated HANDOFF.md + committed results.
- Every experiment gets a ledger row (`python3 loop/loop.py ledger ...`): hypothesis, threshold fixed in advance, result file, decision, who judged.
- Primary frozen protocol (docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md section 3) never changes; extra analyses are labelled exploratory.
- Sandbox pitfalls: pytest needs `--ignore=.env -p no:cacheprovider`; tests that `git init` a temporary repo (tests/test_dev_controller.py) cannot run inside Claude Science's sandbox, run them in the owner's Terminal.
- Nothing is invented: a number that is not in a committed file is written as "TODO science: <what to measure>".

## Commands
```
python3 loop/loop.py score      # per-criterion score; failing checks listed
python3 loop/loop.py next       # what to do now
python3 loop/loop.py status     # packets by status
# bin/agent-cli controller ...  -> shelved, see below
```
**The development controller (harness/dev_controller.py, bin/dev-controller) is SHELVED (MASTER_PLAN section 10, process review).**
It refuses to run in this repo (`is_original_workspace` blocks the TrialMindKG path), its `codex` backend is not installed
and its unattended path was never verified. Do not spend time on it; code packets run as plain branches `cc/<id>` + PR.
Code and tests are kept (docs/DEVELOPMENT_CONTROLLER.md, docs/sessions/2026-11-03_controller_handoff.md).
