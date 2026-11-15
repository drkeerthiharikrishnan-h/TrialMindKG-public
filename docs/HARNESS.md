# Verification harness: every check in the repository

What each check proves, how to run it, what a pass looks like and how long it takes. Set-up and data:
[HOW_TO_RUN.md](../HOW_TO_RUN.md). Times are measured: "cloud" = the Linux sandbox Claude Code works in (no Pharos data,
CPU only), "Mac" = the owner's machine, where the full data live. Packet CC-R11; verified on the Mac by CS-R5.

## One command each

| command | runs | pass looks like | time |
|---|---|---|---|
| `make check` | `make tests`, then `make harness`, then `make schema` | last line `make check: PASS (tests, harness, schema)`, exit 0 | cloud ~12 min (tests 5 min, harness 6.5 min) |
| `make tests` | pytest on `tests/` (minus three files, below) | `N passed, M skipped`, no `failed` | cloud 5 min 13 s (1,130 tests) |
| `make harness` | jsdom click gate + 12 browser journeys, offline | `click_gate exit=0` and every journey `exit=0` | cloud 6.5 min |
| `make schema` | `pipelines/schema_card.py --check` | `schema check: 10 classes, 12 predicates, 0 non-BioLink terms`, exit 0 | 2 s |
| `make demo` | small graph end to end + the benchmark CLI (`scripts/demo.py`) | `outputs/demo/SUMMARY.txt`, first and last line say DEMO | cloud 49 s - 2 min 4 s (5 runs) |
| `make hosted-check` | the hosted app, GET and demo endpoints only | no line starting with `FAIL ` (EXPECTED-FAIL lines are known, see below), exit 0 | not yet timed (the cloud sandbox cannot reach the site) |
| `bash scripts/fresh_clone_check.sh <repository URL>` | clone, new venv, install from the lock file, `pip install -e .`, `make check`, `make demo` | `RESULT: PASS` | Mac: install + 15 min |

## Every asset

| asset | what it proves | how to run | expected result | run time |
|---|---|---|---|---|
| **pytest suite** (`tests/`, 123 files, 1,130 tests collected) | code behaves as documented; see the groups below | `make tests` | no failures (known failures: HOW_TO_RUN.md section 5) | cloud 5 min 13 s |
| - link prediction (15 files, 136 tests) | frozen splits, leakage filter, paired comparison, controls, meta-paths, KGE, predictions export | `pytest -q tests/test_linkpred*.py` | pass | part of the suite |
| - graph build and pruning (24 files, 186 tests) | source cards -> KGX, BioLink terms only (rule 6), prune verdicts, verdict file, spot-check sampler | `pytest -q tests/test_kgbuilder.py tests/test_prune*.py ...` | pass | part of the suite |
| - app and UI (32 files, 349 tests) | every page and API answer, personas, ask limit, click inventory, downloads, Numbers page from frozen files only | `pytest -q tests/test_app*.py ...` | pass | part of the suite |
| - agents and answers (18 files, 146 tests) | answers only via kg_query and verify_answer (rule 10), claim status, NIM schema checks, offline stand-in | `pytest -q tests/test_querier*.py tests/test_nim*.py ...` | pass | part of the suite |
| - trial layer and documents (26 files, 125 tests) | snapshots (rule 9), provenance on every edge (rule 2), **byte-identical offline rebuild** (`test_reproducible.py`) | `pytest -q tests/test_reproducible.py ...` | pass | part of the suite |
| - repository tooling (8 files, 104 tests) | agent CLI, pre-public check, this reproducibility pack (`test_demo.py`, `test_review_pack.py`, `test_download_hashes.py`) | `pytest -q tests/test_demo.py tests/test_review_pack.py ...` | pass | part of the suite |
| **jsdom click gate** (`docs/ux/harness_v3/click_gate.js`) | every persona pill, chip, example, demo button and form on `/` and `/v3` shows a busy state, changes its answer area and finishes; with the ask limit full a typed question shows the limit message | `make harness` (gate runs first; `SKIP_GATE=1` skips it, never before a redeploy) | `click_gate.json`: `"passed": 118, "failed": 0` | ~1 min |
| **12 journeys** J1-J9 (+J7b), R1, R2 (`docs/ux/harness_v3/journeys.sh`, `run_page_v3.js`) | a user's path through the page works with no script errors: card + hover + slider (J1), demo bottom line (J2), typed ask that abstains (J3), trial example (J4), typed trial question (J5), persona switch (J6), shared links (J7, J7b), examples (J8), info tabs (J9), old pages unchanged (R1, R2) | `make harness`; `JOURNEYS_LIVE=1` / `GATE_LIVE=1` use the configured model instead of the offline stand-in; `STRICT=1` (Mac) also fails on error answers | one `<name>.json` per journey, every line `exit=0` | ~5.5 min |
| **hosted_check.py** (`docs/claude_science/trialmindkg-continuity/`) | the deployed app answers every GET endpoint, the review-panel assertions hold (benchmark runs, experiments, novel predictions, no text-mined first row, no developer text) | `make hosted-check` (= `--get-only`); without the flag it also calls the two ask endpoints, which count against the shared visitor limit (20 per hour per IP) | `GET non-200: [] of 16`, assertions `PASS`; `EXPECTED-FAIL(<packet>)` = known, waits for that packet | not yet timed |
| **demo** (`scripts/demo.py`, fixture `tests/fixtures/demo/`) | the whole chain runs from source rows to a paired comparison: build, prune, graphs, frozen benchmark CLI, compare | `make demo` | `outputs/demo/compare.md` and `SUMMARY.txt`, labelled DEMO | cloud 49 s - 2 min 4 s (5 runs) |
| **schema check** (`pipelines/schema_card.py --check`) | every node class and predicate in the cards is a BioLink term (rule 6); warns when `docs/SCHEMA_CARD.md` is older than the layer summaries | `make schema` | exit 0, `0 non-BioLink terms` | 2 s |
| **blinded spot check** (`scripts/make_spotcheck_blinded.py` + `scripts/score_spotcheck.py`) | the pruner's verdicts against a person's judgement, scored by a rule written before the sheet was opened | `python scripts/make_spotcheck_blinded.py` (Mac, needs data/kg/pharos) -> rate `outputs/pharos/spotcheck_blind.xlsx` -> `python scripts/score_spotcheck.py` | `PASS` / `FAIL` and the verdict x judgement table | sample ~5 min; scoring seconds |
| **frozen benchmark CLI** (`pipelines/linkpred/run.py`, frozen) | link prediction on the same leakage-filtered test pairs for every graph (test digest `24f5864cc07ad723`) | flags below; then `python -m pipelines.linkpred.compare --base G0@<tag>` | result JSONs + `compare.md` with paired intervals | ~10.5 h per graph on the Mac, ~2 h on an H100 |
| **quick numbers** (`scripts/benchmark_quick.py`) | the headline comparisons recomputed from the committed result files | `python3 scripts/benchmark_quick.py` | the paired comparisons printed | < 1 min |
| **pre-public check** (`scripts/prepublic_check.py`) | no secrets, .env files, personal e-mails, IP addresses or oversized files in what a public clone would contain | `python3 scripts/prepublic_check.py [--history]` | an empty to-do list (it changes nothing) | ~10 s; minutes with `--history` |
| **attribution check** (`tests/test_attribution.py`, `pipelines/attribution.py`) | the README / app footer names every upstream source, version and licence, generated from the data | `pytest -q tests/test_attribution.py`; fix with `python pipelines/attribution.py --write` | pass | seconds |
| **fresh-clone check** (`scripts/fresh_clone_check.sh`) | a stranger can clone, install from the lock file and run `make check` and `make demo` | `bash scripts/fresh_clone_check.sh <repository URL>` (or `--no-clone .` where git clone is blocked) | `PASS` per step, `RESULT: PASS` | install + ~15 min |
| **review pack** (`scripts/review_pack.py`, CC-R0) | one evidence document for a judge review (FINAL_SPRINT_PLAN section 6.1): git HEAD, test summary, GET-only hosted check, schema/prune/G2/G3 counts, ledger tail, CS-9 comparisons, downloads, routes, click gate, journeys; numbers copied from the files | `python scripts/review_pack.py` (`--skip-tests`, `--skip-hosted`, `--run-harness`) | `outputs/review/pack_<date>.md` with sections 1-10 | 0.2 s from the files; + the test run (5 min) and the hosted check |
| **download sizes and hashes** (`scripts/download_hashes.py`) | HOW_TO_RUN.md section 3 lists every hosted download with the size from `downloads.json` and the lines of its SHA256SUMS | `python scripts/download_hashes.py` after each upload (where the site is reachable); `--check` without network | `--check`: `0 problem(s)` | seconds |
| **full frozen run** (`scripts/benchmark_frozen.sh`) | the benchmark exactly as frozen, flags read from `scripts/frozen_flags.txt` | `bash scripts/benchmark_frozen.sh G0,G2,G3 <tag>` | result JSONs; then `compare` | ~10.5 h per graph (Mac), ~2 h (H100) |

## The frozen benchmark flags, written out once

From docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md section 3 (runs A and B). The one file that holds them is
`scripts/frozen_flags.txt`; `scripts/benchmark_frozen.sh` and `make demo` both read it (the demo changes `--epochs 300` to
20 and runs `--graphs G0,G2 --tag demo`), and `tests/test_demo.py` checks it against the protocol text. Written out:

```bash
python -m pipelines.linkpred.run --graphs G0,G2,G3 --tasks B --splits kfold --seeds 1 --epochs 300 --hidden 64 \
  --drop-levels text_co_occurrence --test-levels knowledge_assertion --positives assertions --max-edges 2000000 \
  --reverse-edges --learn-embeddings --num-bases 30 --neg-pool training --neg-match degree \
  --baselines --metapaths --strata --tag <tag>
```

## Excluded from `make check`, and why

- `tests/test_prepublic_check.py`, `tests/test_dev_controller.py`: they `git init` scratch repositories, which some
  sandboxes refuse; `tests/test_controller_backends.py`: it runs the development controller's sandbox validator
  (harness/dev_controller.py), which depends on the platform. Run them alone where they work: `pytest -q <file>`.
- `.env` is ignored so pytest never reads a local secrets file.
