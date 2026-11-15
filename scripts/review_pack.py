"""
Evidence pack for a judge review (CC-R0; docs/archive/FINAL_SPRINT_PLAN_2026-11-10.md section 6.1). No model is called.

    python scripts/review_pack.py [--out outputs/review/pack_<date>.md] [--skip-tests] [--skip-hosted] [--run-harness]

One markdown file, so a review starts from one document and not from loose files. Sections, in the plan's order:
  1. git HEAD, branch and dirty files                 (git)
  2. test summary                                     (pytest, the `make tests` arguments; --skip-tests: not run)
  3. hosted check, GET only                           (hosted_check.py --get-only; --skip-hosted: not run)
  4. counts: schema card, prune report, G2, G3        (outputs/schema_card.json, outputs/pharos/prune_report.md,
                                                       outputs/g2/summary.md, outputs/g3/summary.md)
  5. ledger tail, 10 rows                             (loop/ledger.tsv)
  6. benchmark D on the CS-9 graphs                   (outputs/linkpred/compare_cs9_gnnpath.md, compare_cs9_metapath.md)
  7. downloads                                        (downloads.json)
  8. pages and endpoints                              (the @app routes in pipelines/app.py)
  9. jsdom click gate                                 (docs/ux/harness_v3/click_gate.json)
 10. the 12 journeys, offline                         (docs/ux/harness_v3/J*.json, R*.json; --run-harness runs them first)
Every number in the pack is copied from those files or command outputs (CLAUDE.md rule 7); a missing file is reported
as missing, never filled in.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = "docs/ux/harness_v3"
REPORTS = ["outputs/pharos/prune_report.md", "outputs/g2/summary.md", "outputs/g3/summary.md"]
COMPARES = ["outputs/linkpred/compare_cs9_gnnpath.md", "outputs/linkpred/compare_cs9_metapath.md"]
PYTEST_ARGS = ["--ignore=.env", "--ignore=tests/test_prepublic_check.py", "--ignore=tests/test_controller_backends.py",
               "--ignore=tests/test_dev_controller.py", "-p", "no:cacheprovider", "-q"]
ROUTE = re.compile(r'^@app\.(get|post|put|delete)\("([^"]+)"', re.M)


def run(argv, root=ROOT, timeout=None):
    """Takes a command (list) and the folder to run it in. Returns (exit code, stdout+stderr text); a command that
    cannot start or times out gives (None, the reason)."""
    try:
        r = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"{type(e).__name__}: {e}"


def git_section(root=ROOT, runner=run):
    """Takes the repository root. Returns markdown lines: HEAD commit, branch and the dirty files (or 'clean')."""
    _, head = runner(["git", "log", "-1", "--format=%H %ad %s", "--date=iso"], root)
    _, branch = runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    _, dirty = runner(["git", "status", "--short"], root)
    dirty = [ln for ln in dirty.splitlines() if ln.strip()]
    out = [f"- HEAD: `{head.strip()}`", f"- branch: `{branch.strip()}`"]
    out.append("- working tree: clean" if not dirty else f"- working tree: {len(dirty)} dirty file(s):")
    out += [f"  - `{ln}`" for ln in dirty[:40]]
    return out


def pytest_summary(text):
    """Takes pytest -q output. Returns its final summary line ('N passed, M failed ... in Xs'), or None."""
    lines = [ln.strip("= ").strip() for ln in text.splitlines() if re.search(r"\d+ (passed|failed|error)", ln)]
    return lines[-1] if lines else None


def tests_section(root=ROOT, skip=False, runner=run, python=sys.executable):
    """Runs pytest with the `make tests` arguments unless skip. Returns markdown lines: the summary line and every
    FAILED / ERROR test id."""
    if skip:
        return ["- not run (--skip-tests)"]
    code, text = runner([python, "-m", "pytest"] + PYTEST_ARGS, root)
    failed = [ln for ln in text.splitlines() if ln.startswith(("FAILED ", "ERROR "))]
    return [f"- `pytest -q` exit {code}: {pytest_summary(text) or 'no summary line'}"] + [f"  - `{ln}`" for ln in failed]


def hosted_section(root=ROOT, skip=False, runner=run, python=sys.executable):
    """Runs hosted_check.py --get-only unless skip. Returns markdown lines: its exit code, the PASS / FAIL /
    EXPECTED-FAIL counts and its full output in a code block."""
    if skip:
        return ["- not run (--skip-hosted)"]
    code, text = runner([python, "scripts/hosted_check.py", "--get-only"], root, 900)
    counts = {k: sum(1 for ln in text.splitlines() if ln.startswith(k)) for k in ("PASS ", "FAIL ", "EXPECTED-FAIL")}
    head = f"- exit {code}; " + ", ".join(f"{k.strip()}: {v}" for k, v in counts.items())
    return [head, "", "```", text.strip()[-6000:], "```"]


def file_head(path, n=12):
    """Takes a path. Returns its first n lines (or a 'missing' line) as markdown lines."""
    p = Path(path)
    if not p.exists():
        return [f"- MISSING: `{p}`"]
    return [f"`{p.name}` (first {n} lines):", "", "```"] + p.read_text().splitlines()[:n] + ["```"]


def counts_section(root=ROOT):
    """Returns markdown lines: classes, predicates and non-BioLink terms from outputs/schema_card.json, its layer
    table, and the head of the prune report and the G2 / G3 summaries."""
    out = []
    sc = Path(root) / "outputs" / "schema_card.json"
    if sc.exists():
        card = json.loads(sc.read_text())
        out.append(f"- schema card (`outputs/schema_card.json`): {len(card.get('classes', []))} classes, "
                   f"{len(card.get('predicates', []))} predicates, {len(card.get('problems', []))} non-BioLink terms")
        for layer in card.get("layers", []):
            out.append(f"  - layer {layer.get('layer')}: {layer.get('n_nodes'):,} nodes, {layer.get('n_edges'):,} edges "
                       f"({layer.get('source_version')}; from `{layer.get('file')}`)")
    else:
        out.append("- MISSING: `outputs/schema_card.json`")
    for rel in REPORTS:
        out += ["", f"**{rel}**", ""] + file_head(Path(root) / rel)
    return out


def ledger_section(root=ROOT, n=10):
    """Returns markdown lines: the last n rows of loop/ledger.tsv as a table (date, id, decision, judged by); long
    cells are cut at 400 characters and say so."""
    p = Path(root) / "loop" / "ledger.tsv"
    if not p.exists():
        return ["- MISSING: `loop/ledger.tsv`"]
    rows = [ln.split("\t") for ln in p.read_text().splitlines() if ln.strip()]
    header, body = rows[0], rows[1:][-n:]

    def cell(r, name):
        v = r[header.index(name)] if name in header and header.index(name) < len(r) else ""
        v = v.replace("|", "/")
        return v if len(v) <= 400 else v[:400] + " [cut]"
    out = ["| date | id | decision | judged by |", "|---|---|---|---|"]
    out += [f"| {cell(r, 'date')} | {cell(r, 'id')} | {cell(r, 'decision')} | {cell(r, 'judged_by')} |" for r in body]
    short = [i + 1 for i, r in enumerate(rows) if len(r) != len(header)]
    if short:
        out.append(f"\n- rows with the wrong number of columns (line numbers in the file, header = 1): {short}")
    return out


def compare_section(root=ROOT):
    """Returns markdown lines: the full text of the two CS-9 comparison files (or 'missing')."""
    out = []
    for rel in COMPARES:
        p = Path(root) / rel
        out += ["", f"**{rel}**", ""] + (p.read_text().splitlines() if p.exists() else [f"- MISSING: `{rel}`"])
    return out


def downloads_section(root=ROOT):
    """Returns markdown lines: base_url and one row per downloads.json entry (path, bytes, title)."""
    p = Path(root) / "downloads.json"
    if not p.exists():
        return ["- MISSING: `downloads.json`"]
    d = json.loads(p.read_text())
    out = [f"- base_url: {d.get('base_url') or '(empty: downloads hidden)'}", "", "| path | bytes | title |", "|---|---|---|"]
    out += [f"| {f.get('path')} | {f.get('bytes', '')} | {f.get('title')} |" for f in d.get("files", [])]
    return out


def routes(app_file):
    """Takes pipelines/app.py. Returns [(METHOD, path)] of every @app route, in file order."""
    return [(m.upper(), path) for m, path in ROUTE.findall(Path(app_file).read_text())]


def routes_section(root=ROOT):
    """Returns markdown lines: the pages (HTML) and API endpoints the app serves, from its route decorators."""
    rs = routes(Path(root) / "pipelines" / "app.py")
    pages = [p for m, p in rs if not p.startswith("/api/")]
    api = [f"{m} {p}" for m, p in rs if p.startswith("/api/")]
    return [f"- {len(rs)} routes: {len(pages)} pages/files, {len(api)} API endpoints",
            "- pages/files: " + ", ".join(f"`{p}`" for p in pages), "- API: " + ", ".join(f"`{a}`" for a in api)]


def click_gate_section(root=ROOT):
    """Returns markdown lines: total / passed / failed / error answers and the run time of click_gate.json."""
    p = Path(root) / HARNESS / "click_gate.json"
    if not p.exists():
        return [f"- MISSING: `{HARNESS}/click_gate.json`"]
    d = json.loads(p.read_text())
    return [f"- `{HARNESS}/click_gate.json` (run {d.get('when')}, strict={d.get('strict')}): {d.get('passed')} of "
            f"{d.get('total')} passed, {d.get('failed')} failed, {d.get('error_answers')} error answers"]


def journeys_section(root=ROOT):
    """Returns markdown lines: one row per journey result file (J*.json, R*.json): script errors and the errors or the
    harness error, then the count that had none."""
    files = sorted((Path(root) / HARNESS).glob("[JR][0-9]*.json"))
    if not files:
        return [f"- MISSING: no journey results in `{HARNESS}/`"]
    out, ok = ["| journey | script errors | errors |", "|---|---|---|"], 0
    for f in files:
        d = json.loads(f.read_text())
        errs = d.get("errors") or ([d["harness_error"][:200]] if "harness_error" in d else [])
        n = d.get("script_errors", "harness error" if "harness_error" in d else "?")
        ok += n == 0
        out.append(f"| {f.stem} | {n} | {'; '.join(str(e)[:200] for e in errs) or '-'} |")
    return out + ["", f"- {ok} of {len(files)} result files with 0 script errors"]


def build_pack(root=ROOT, skip_tests=False, skip_hosted=False, run_harness=False, runner=run, today=None):
    """Takes the repository root and the switches. Returns the pack as one markdown string (sections 1-10)."""
    today = today or datetime.date.today().isoformat()
    harness_note = "results of the run just made (--run-harness)" if run_harness else "committed result files"
    if run_harness:
        code, _ = runner(["make", "harness"], root, 3600)
        harness_note += f", `make harness` exit {code}"
    sections = [
        ("1. Repository", git_section(root, runner)),
        ("2. Tests", tests_section(root, skip_tests, runner)),
        ("3. Hosted check (GET only)", hosted_section(root, skip_hosted, runner)),
        ("4. Counts", counts_section(root)),
        ("5. Ledger, last 10 rows", ledger_section(root)),
        ("6. Benchmark D on the CS-9 graphs", compare_section(root)),
        ("7. Downloads", downloads_section(root)),
        ("8. Pages and endpoints", routes_section(root)),
        ("9. jsdom click gate", click_gate_section(root)),
        (f"10. Journeys, offline ({harness_note})", journeys_section(root)),
    ]
    text = [f"# Review evidence pack, {today}", "",
            "Written by `scripts/review_pack.py` (FINAL_SPRINT_PLAN section 6.1). Numbers are copied from the named files "
            "and command outputs; nothing here is computed by a model.", ""]
    for title, lines in sections:
        text += [f"## {title}", ""] + lines + [""]
    return "\n".join(text)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", default="")
    p.add_argument("--skip-tests", action="store_true")
    p.add_argument("--skip-hosted", action="store_true")
    p.add_argument("--run-harness", action="store_true")
    a = p.parse_args(argv)
    today = datetime.date.today().isoformat()
    out = Path(a.out) if a.out else ROOT / "outputs" / "review" / f"pack_{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_pack(ROOT, a.skip_tests, a.skip_hosted, a.run_harness, today=today))
    print(f"review pack written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
