#!/usr/bin/env python3
"""
The build loop: PLAN -> DESIGN -> RUN -> JUDGE -> ITERATE -> IMPROVE -> COMMIT, kept in plain files so any agent
(Claude Code, Codex, Claude Science) or the owner can pick the work up from git alone.

    python3 loop/loop.py score            # run every scorecard check, print the score per judging criterion
    python3 loop/loop.py next             # the open task packets, then the heaviest failing check with no open task
    python3 loop/loop.py status           # task packets grouped by status
    python3 loop/loop.py ledger "<id>" "<hypothesis>" "<threshold>" "<result file>" "<decision>" "<who>"

Files: loop/scorecard.yaml (the objective: organizer criteria -> runnable checks), loop/tasks/T-*.yaml (one packet per
unit of work), loop/ledger.tsv (append-only experiment ledger), loop/state.yaml (facts the checks read, e.g. the
hosted demo URL). Standard library plus PyYAML (already a project dependency).

A check is one of: file_exists {path}; grep {file, pattern} (the file exists and the regex matches); command {argv}
(exit code 0, run from the repo root); url {state_key} (the URL stored in loop/state.yaml answers HTTP 200 in
under max_seconds); stranger_results {path} (the filled stranger-test log is finished and honest, loop/stranger.py).
A check that cannot be run is "fail", never "pass": the score only counts what was shown.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEDGER_COLUMNS = ["date", "id", "hypothesis", "preregistered_threshold", "result_file", "decision", "judged_by"]


def load_yaml(path):
    """Takes a path. Returns the parsed YAML (an empty dict for an empty file)."""
    return yaml.safe_load(Path(path).read_text()) or {}


def load_scorecard(path=None):
    """Takes an optional path to scorecard.yaml. Returns (criteria, checks): the criterion -> weight dict and the
    flat list of checks, each check dict carrying its criterion, id, weight and kind fields."""
    sc = load_yaml(path or ROOT / "loop" / "scorecard.yaml")
    criteria = {name: spec["weight"] for name, spec in sc["criteria"].items()}
    checks = []
    for name, spec in sc["criteria"].items():
        for c in spec["checks"]:
            checks.append(dict(c, criterion=name))
    return criteria, checks


def run_check(check, root=ROOT, state=None):
    """Takes one check dict, the repo root and the loop state dict. Returns dict(status, detail) where status is
    'pass' or 'fail'; any error while checking is a 'fail' with the reason in detail."""
    kind = check.get("kind")
    try:
        if kind == "file_exists":
            ok = (Path(root) / check["path"]).exists()
            return dict(status="pass" if ok else "fail", detail=check["path"] + (" exists" if ok else " missing"))
        if kind == "grep":
            p = Path(root) / check["file"]
            if not p.exists():
                return dict(status="fail", detail=check["file"] + " missing")
            ok = re.search(check["pattern"], p.read_text(errors="replace")) is not None
            return dict(status="pass" if ok else "fail", detail=f"pattern {check['pattern']!r} " + ("found" if ok else "not found"))
        if kind == "command":
            r = subprocess.run(check["argv"], cwd=root, capture_output=True, text=True, timeout=check.get("timeout", 600))
            return dict(status="pass" if r.returncode == 0 else "fail", detail=f"exit {r.returncode}")
        if kind == "stranger_results":      # the filled stranger-test log (loop/stranger.py): finished and honest, not "went well"
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import stranger
            ok, detail = stranger.check_results(Path(root) / check["path"])
            return dict(status="pass" if ok else "fail", detail=detail)
        if kind == "url":
            url = (state or {}).get(check["state_key"])
            if not url:
                return dict(status="fail", detail=f"{check['state_key']} not set in loop/state.yaml")
            t0 = time.time()
            with urllib.request.urlopen(url, timeout=check.get("max_seconds", 5)) as resp:
                dt = time.time() - t0
                ok = resp.status == 200 and dt <= check.get("max_seconds", 5)
            return dict(status="pass" if ok else "fail", detail=f"HTTP {resp.status} in {dt:.1f}s")
        return dict(status="fail", detail=f"unknown check kind {kind!r}")
    except Exception as e:  # a check that errors has not shown anything
        return dict(status="fail", detail=f"{type(e).__name__}: {e}"[:200])


def score(root=ROOT, scorecard=None):
    """Takes the repo root and an optional scorecard path. Returns dict(criteria={name: dict(weight, earned)},
    total, results=[dict(id, criterion, weight, status, detail)]). Points are a check's weight when it passes."""
    root = Path(root)
    criteria, checks = load_scorecard(scorecard or root / "loop" / "scorecard.yaml")
    state_path = root / "loop" / "state.yaml"
    state = load_yaml(state_path) if state_path.exists() else {}
    results, earned = [], {name: 0 for name in criteria}
    for c in checks:
        r = run_check(c, root, state)
        if r["status"] == "pass":
            earned[c["criterion"]] += c["weight"]
        results.append(dict(id=c["id"], criterion=c["criterion"], weight=c["weight"], **r))
    return dict(criteria={n: dict(weight=criteria[n], earned=earned[n]) for n in criteria},
                total=sum(earned.values()), results=results)


def load_tasks(tasks_dir=None):
    """Takes an optional tasks directory. Returns the task packets (dicts) sorted by id, each with its file name."""
    d = Path(tasks_dir or ROOT / "loop" / "tasks")
    tasks = []
    for f in sorted(d.glob("T-*.yaml")):
        t = load_yaml(f)
        t["file"] = f.name
        tasks.append(t)
    return tasks


def next_actions(result, tasks):
    """Takes a score() result and the task packets. Returns (open_tasks, gaps): the packets not done, and the failing
    checks with no open task that names them in 'closes', heaviest first."""
    open_tasks = [t for t in tasks if t.get("status") not in ("done", "cancelled")]
    covered = {cid for t in open_tasks for cid in t.get("closes", [])}
    gaps = sorted((r for r in result["results"] if r["status"] == "fail" and r["id"] not in covered),
                  key=lambda r: -r["weight"])
    return open_tasks, gaps


def ledger_append(path, row):
    """Takes the ledger path and a dict with LEDGER_COLUMNS minus date. Appends one tab-separated line (the header is
    written for a new file); tabs and newlines inside values become spaces. Returns the line."""
    path = Path(path)
    row = dict(row, date=row.get("date") or time.strftime("%Y-%m-%d"))
    clean = [re.sub(r"[\t\r\n]+", " ", str(row.get(c, ""))) for c in LEDGER_COLUMNS]
    line = "\t".join(clean)
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a") as f:
        if new:
            f.write("\t".join(LEDGER_COLUMNS) + "\n")
        f.write(line + "\n")
    return line


def _print_score(res):
    for name, v in res["criteria"].items():
        print(f"{name}: {v['earned']} / {v['weight']}")
    print(f"TOTAL: {res['total']} / {sum(v['weight'] for v in res['criteria'].values())}")
    for r in res["results"]:
        mark = "ok  " if r["status"] == "pass" else "FAIL"
        print(f"  {mark} {r['criterion']:<14} {r['id']:<26} w={r['weight']:<3} {r['detail']}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "score"
    if cmd == "score":
        res = score()
        if "--json" in argv:
            print(json.dumps(res, indent=1))
        else:
            _print_score(res)
        return 0
    if cmd in ("next", "status"):
        tasks = load_tasks()
        if cmd == "status":
            for st in ("in_progress", "blocked", "open", "done", "cancelled"):
                group = [t for t in tasks if t.get("status") == st]
                if group:
                    print(f"{st}:")
                    for t in group:
                        print(f"  {t['id']} [{t.get('owner', '?')}] {t['title']}")
            return 0
        open_tasks, gaps = next_actions(score(), tasks)
        print("open task packets:")
        for t in open_tasks:
            print(f"  {t['id']} [{t.get('status')}, owner {t.get('owner', '?')}] {t['title']}")
        print("heaviest failing checks with no open task:")
        for g in gaps[:5]:
            print(f"  {g['id']} (w={g['weight']}, {g['criterion']}): {g['detail']}")
        return 0
    if cmd == "ledger" and len(argv) == 7:
        print(ledger_append(ROOT / "loop" / "ledger.tsv",
                            dict(zip(LEDGER_COLUMNS[1:], argv[1:]))))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
