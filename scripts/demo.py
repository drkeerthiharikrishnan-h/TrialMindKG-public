"""
`make demo`: build a SMALL graph end to end and run the frozen benchmark CLI on it, in a few minutes on a laptop.

    python scripts/demo.py [--epochs 20] [--out outputs/demo]

DEMO, NOT THE REPORTED NUMBERS. The input is the committed fixture tests/fixtures/demo/ (Pharos 3.19.2 rows of the four
EGFR/ERBB receptor tyrosine kinases, written by scripts/make_demo_fixture.py), not the 26M-edge Pharos graph, and the
GNN trains for a few epochs instead of 300. It shows that every step runs and what each step writes; the numbers in
docs/EXPERIMENTS_LINKPRED.md come from the full graphs and are not reproduced here.

Steps (each is the repository's own command, unchanged):
  1. build_kg.py sources/pharos.yaml and sources/dto.yaml        -> data/kg/{pharos,dto}/
  2. prune_kg.py for both                                         -> edge_verdicts + prune_report.md
  3. make_graphs.py G0,G2 --allow-missing                         -> G0 = Pharos; G2 = Pharos + DTO (the other
                                                                     enrichment layers are not in the fixture; the
                                                                     G2 summary lists them as missing)
  4. python -m pipelines.linkpred.run with the frozen flags (scripts/frozen_flags.txt), --epochs reduced, --tag demo
  5. python -m pipelines.linkpred.compare --base G0@demo         -> compare.md

The pipeline scripts write under the checkout they live in (their ROOT is their own location), so the steps run in a
private copy, <out>/root/ (pipelines/ and sources/ copied, data/reference/ linked, the fixture tables as data/pharos/):
the real data/ and outputs/ are never touched. Every step runs with PYTHONPATH = <out>/root only (child_env), and
a check before step 1 refuses to run if pipelines.linkpred would load from anywhere else. Results are copied to <out>/
and a summary is printed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "demo"
DEMO_EPOCHS = 20
TAG = "demo"
LABEL = "DEMO (EGFR/ERBB fixture, reduced epochs): demo, not the reported numbers (those are in docs/EXPERIMENTS_LINKPRED.md)"
FLAGS_FILE = ROOT / "scripts" / "frozen_flags.txt"     # the one file with the frozen flags (scripts/benchmark_frozen.sh)


def frozen_flags(path=FLAGS_FILE):
    """Takes the flags file. Returns its flags as an argv list (comment lines skipped), --epochs included."""
    lines = [ln for ln in Path(path).read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return " ".join(lines).split()


def demo_flags(epochs, path=FLAGS_FILE):
    """Takes the demo's epoch count. Returns the frozen flags with only the --epochs value replaced."""
    flags = frozen_flags(path)
    flags[flags.index("--epochs") + 1] = str(epochs)
    return flags


CARDS = ["pharos", "dto"]


def make_workdir(work, root=ROOT, fixture=FIXTURE):
    """Takes the private root to create (a Path) and the repository root. (Re)creates work/ with copies of pipelines/
    and the CARDS source cards, a link to data/reference/, and the fixture tables as data/pharos/. Returns work."""
    work = Path(work)
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(root / "pipelines", work / "pipelines", ignore=shutil.ignore_patterns("__pycache__"))
    (work / "sources").mkdir(parents=True)
    for c in CARDS:
        shutil.copy2(root / "sources" / f"{c}.yaml", work / "sources" / f"{c}.yaml")
    (work / "data").mkdir()
    os.symlink(root / "data" / "reference", work / "data" / "reference")
    shutil.copytree(fixture / "pharos", work / "data" / "pharos")
    return work


def commands(python, epochs=DEMO_EPOCHS):
    """Takes the Python executable and the epoch count. Returns the ordered list of (step name, argv) the demo runs
    inside its private root."""
    steps = []
    for c in CARDS:
        steps.append((f"build {c}", [python, "pipelines/build_kg.py", f"sources/{c}.yaml"]))
    for c in CARDS:
        steps.append((f"prune {c}", [python, "pipelines/prune_kg.py", f"sources/{c}.yaml"]))
    steps.append(("graphs G0,G2", [python, "pipelines/make_graphs.py", "G0,G2", "--allow-missing"]))
    steps.append(("benchmark (demo)", [python, "-m", "pipelines.linkpred.run", "--graphs", "G0,G2"]
                  + demo_flags(epochs) + ["--tag", TAG]))
    steps.append(("compare", [python, "-m", "pipelines.linkpred.compare", "--base", f"G0@{TAG}"]))
    return steps


def child_env(work, base=None):
    """Takes the private root and the caller's environment (default os.environ). Returns the environment for every demo
    step: PYTHONPATH is the private root ONLY and PYTHONSAFEPATH is removed, so `python -m pipelines...` and the scripts
    import the copied pipelines/ whatever the caller exported (with PYTHONSAFEPATH=1 + PYTHONPATH=<repo> the benchmark
    imported the repo's pipelines/, trained on the real G0 and wrote into outputs/linkpred/; session 27, 10 Nov).
    Snapshots are offline; no bytecode is written."""
    env = dict(os.environ if base is None else base)
    env.pop("PYTHONSAFEPATH", None)
    env.update(PYTHONPATH=str(Path(work).resolve()), TMKG_SNAPSHOT_MODE="offline", PYTHONDONTWRITEBYTECODE="1")
    return env


def import_check(python, work, env):
    """Takes the Python executable, the private root and child_env(). Imports pipelines.linkpred the way step 4 does
    (cwd = work) and returns None when it comes from work/, else a one-line error naming where it came from."""
    r = subprocess.run([python, "-c", "import pipelines.linkpred as m; print(m.__file__)"], cwd=work, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return f"cannot import pipelines.linkpred in {work}: {r.stderr.strip().splitlines()[-1:]}"
    got = Path(r.stdout.strip()).resolve()
    if Path(work).resolve() not in got.parents:
        return f"pipelines.linkpred would load from {got}, not from the private copy {work}: refusing to run"
    return None


def collect(work, out):
    """Copies the demo's reports from the private root to out/: compare.md, the two result JSONs, the graph summaries
    and the prune reports. Returns the list of copied paths (relative to out)."""
    work, out = Path(work), Path(out)
    picks = {"compare.md": work / "outputs/linkpred/compare.md"}
    for g in ("G0", "G2"):
        picks[f"{g}_B_kfold@{TAG}.json"] = work / f"outputs/linkpred/{g}_B_kfold@{TAG}.json"
        picks[f"{g.lower()}_summary.md"] = work / f"outputs/{g.lower()}/summary.md"
    for c in CARDS:
        picks[f"{c}_prune_report.md"] = work / f"outputs/{c}/prune_report.md"
        picks[f"{c}_build_summary.md"] = work / f"outputs/{c}/summary.md"
    copied = []
    for name, src in picks.items():
        if src.exists():
            shutil.copy2(src, out / name)
            copied.append(name)
    return copied


def size_note(out, small=200):
    """Takes out/ with the result JSONs. Returns one sentence on how far the AUROCs can be read, computed from the
    smallest test-pair count per fold (CLAUDE.md rule 7)."""
    n = []
    for g in ("G0", "G2"):
        p = Path(out) / f"{g}_B_kfold@{TAG}.json"
        if p.exists():
            v = (json.loads(p.read_text()).get("summary") or {}).get("n_test")
            v = v.get("mean") if isinstance(v, dict) else v
            if v is not None:
                n.append(v)
    if not n:
        return "no test pairs reported"
    if min(n) < small:
        return (f"only {min(n):.0f} test pairs per fold (< {small}): these AUROCs are too noisy to compare graphs; "
                "the demo shows that every step runs")
    return f"{min(n):.0f} test pairs per fold"


def summary_lines(out, timings):
    """Takes out/ (with the copied result JSONs) and {step: seconds}. Returns about ten lines for the terminal: the
    label, the graph sizes, test pairs, the GNN and meta-path AUROC per graph, and the run time. Every number is read
    from the files (CLAUDE.md rule 7)."""
    out = Path(out)
    lines = [LABEL]
    for g in ("G0", "G2"):
        p = out / f"{g}_B_kfold@{TAG}.json"
        if not p.exists():
            lines.append(f"{g}: no result file")
            continue
        s = json.loads(p.read_text()).get("summary") or {}
        def f(k, fmt="{:.4f}"):
            v = s.get(k)
            v = v.get("mean") if isinstance(v, dict) else v      # run.py summaries are {mean, ci95} over seeds
            return "n/a" if v is None else fmt.format(v)
        lines.append(f"{g}: GNN AUROC {f('auroc')} | meta-path AUROC {f('auroc_metapath')} "
                     f"(coverage {f('metapath_coverage', '{:.0%}')}) | test pairs per fold {f('n_test', '{:.0f}')}")
    lines.append(size_note(out))
    lines.append("steps: " + ", ".join(f"{k} {v:.0f}s" for k, v in timings.items()))
    lines.append(f"total: {sum(timings.values()) / 60:.1f} min | paired comparison: {out / 'compare.md'}")
    lines.append(LABEL)
    return lines


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--epochs", type=int, default=DEMO_EPOCHS)
    p.add_argument("--out", default=str(ROOT / "outputs" / "demo"))
    a = p.parse_args(argv)
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = make_workdir(out / "root")
    env = child_env(work)
    err = import_check(sys.executable, work, env)
    if err:
        print(f"FAILED before step 1: {err}", file=sys.stderr)
        return 1
    timings = {}
    log = open(out / "demo.log", "w")
    print(LABEL)
    for name, argv_ in commands(sys.executable, a.epochs):
        print(f"--> {name}", flush=True)
        t = time.time()
        r = subprocess.run(argv_, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
        timings[name] = time.time() - t
        if r.returncode != 0:
            log.close()
            print(f"FAILED at step '{name}' (exit {r.returncode}); last lines of {out / 'demo.log'}:", file=sys.stderr)
            print("\n".join((out / "demo.log").read_text().splitlines()[-25:]), file=sys.stderr)
            return 1
    log.close()
    collect(work, out)
    text = "\n".join(summary_lines(out, timings))
    (out / "SUMMARY.txt").write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
