"""
Paired comparison of link-prediction results (docs/ENRICHMENT_DIAGNOSIS_PLAN.md, wave G: "the mean of the PAIRED fold
differences with a 95% CI, not two unpaired means").

    python -m pipelines.linkpred.compare [--base G0] [--task B] [--split kfold] [--metric auroc_metapath,auroc]

Reads every outputs/linkpred/<graph>_<task>_<split>[@tag].json, pairs each run with the base run fold by fold and
seed by seed (the folds are assigned by a hash of the pair ids, so fold 1 holds the same test pairs in every graph;
the result files' test-set digests must match), and writes outputs/linkpred/compare.md: per variant the mean
difference, its 95% confidence interval (t distribution over the paired folds), how many folds it won, and a
sentence computed from those numbers. Needs result files written after 2026-11-01 (they hold per-fold values).

  load_result(path)                 -> (label, dict) of one result file
  fold_values(result, metric)       -> {(seed, fold): value}
  paired_delta(a, b, metric)        -> {n, mean, ci_low, ci_high, wins, digest_match}
  verdict(d, a_label, b_label)      -> one sentence from the numbers (CLAUDE.md rule 7)
  compare_metrics(...)              -> compare.md with one section per metric (default: meta-path AUROC, meta-path
                                       coverage, GNN AUROC), so the meta-path result is reported by default
  write_report(out_dir)             -> report.md of the tagged frozen / diagnostic runs only; untagged and smoke
                                       (@dm, @wired, ...) result files are listed as excluded, never tabulated
"""
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipelines"))   # write_report imports linkpred.evaluate (as run.py does)
OUT = ROOT / "outputs" / "linkpred"
DEFAULT_METRICS = ("auroc_metapath", "metapath_coverage", "auroc")
EXPLORATORY = {"auroc_metapath": "exploratory until the confound controls are reported (controls.py)",
               "metapath_coverage": "share of test pairs with at least one meta-path; differs between graphs, so read "
                                    "it next to the meta-path AUROC",
               "auroc": "the GNN: the primary frozen metric"}
AUTHORITATIVE_TAG = re.compile(r"^(frozen|diag)")      # tags of the frozen-protocol and diagnostic runs


def load_result(path):
    """Takes a result file path '<graph>_<task>_<split>[@tag].json'. Returns (label, parsed JSON), where label is
    '<graph>' or '<graph>@<tag>'."""
    path = Path(path)
    graph, task, split = path.stem.split("_", 2)
    tag = split.split("@", 1)[1] if "@" in split else ""
    return (f"{graph}@{tag}" if tag else graph), json.loads(path.read_text())


def fold_values(result, metric="auroc"):
    """Takes a parsed result. Returns {(seed index, fold index): value of metric} from per_seed[*]['folds'];
    {} when the file predates per-fold storage."""
    out = {}
    for s, seed in enumerate(result.get("per_seed", [])):
        for f, fold in enumerate(seed.get("folds", [])):
            if metric in fold:
                out[(s, f)] = float(fold[metric])
    return out


def t_quantile(df, q=0.975):
    """Two-sided t critical value for df degrees of freedom (scipy when present, else a normal approximation
    with the Cornish-Fisher correction, within 1% for df >= 3)."""
    try:
        from scipy.stats import t
        return float(t.ppf(q, df))
    except ImportError:
        z = 1.959963984540054
        return z + (z ** 3 + z) / (4 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2)


def paired_delta(a, b, metric="auroc"):
    """Takes two parsed results (a = variant, b = base). Returns {'n': paired folds, 'mean': mean of a - b,
    'ci_low', 'ci_high': 95% CI (None when n < 2), 'wins': folds where a > b, 'digest_match': whether both were
    scored on the same test set}. n = 0 when the files share no folds."""
    va, vb = fold_values(a, metric), fold_values(b, metric)
    keys = sorted(set(va) & set(vb))
    d = [va[k] - vb[k] for k in keys]
    da = a.get("settings", {}).get("test_set", {}).get("digest")
    db = b.get("settings", {}).get("test_set", {}).get("digest")
    out = {"n": len(d), "mean": None, "ci_low": None, "ci_high": None, "wins": sum(x > 0 for x in d),
           "digest_match": da is not None and da == db}
    if not d:
        return out
    mean = sum(d) / len(d)
    out["mean"] = mean
    if len(d) >= 2:
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1))
        half = t_quantile(len(d) - 1) * sd / math.sqrt(len(d))
        out["ci_low"], out["ci_high"] = mean - half, mean + half
    return out


def verdict(d, a_label, b_label, metric="auroc"):
    """Takes a paired_delta result and the two labels. Returns one sentence whose wording follows the numbers:
    higher / lower when the CI excludes 0, 'no clear difference' when it includes 0, and a warning when the test
    sets differ or fewer than 2 folds pair up."""
    if not d["digest_match"]:
        return f"{a_label} and {b_label} were not scored on the same test pairs (digests differ): not comparable."
    if d["n"] < 2 or d["ci_low"] is None:
        return f"Only {d['n']} paired fold(s): too few for an interval."
    diff = f"{d['mean']:+.4f} {metric} (95% CI {d['ci_low']:+.4f} to {d['ci_high']:+.4f}; {d['wins']} of {d['n']} folds higher)"
    if d["ci_low"] > 0:
        return f"{a_label} scores higher than {b_label}: {diff}."
    if d["ci_high"] < 0:
        return f"{a_label} scores lower than {b_label}: {diff}."
    return f"No clear difference between {a_label} and {b_label}: {diff}; the interval includes 0."


def compare_all(out_dir=OUT, base="G0", task="B", split="kfold", metric="auroc", all_tags=False, variants=None):
    """Takes the results folder, the base label, task, split and metric. Returns (markdown text, rows): one row per
    other result of that task and split with the SAME tag as the base (G0@wired is compared with G2@wired, G3@wired;
    a different tag means different settings), or every result with all_tags=True, or exactly the labels in
    `variants` (e.g. ['G2@frozen_a', 'G3@frozen_a'] against the cap-matched base G0@frozen_a_g0_1m), in that order.
    Raises FileNotFoundError when the base result or a named variant is missing."""
    out_dir = Path(out_dir)
    bgraph, _, btag = base.partition("@")
    bpath = out_dir / f"{bgraph}_{task}_{split}{'@' + btag if btag else ''}.json"
    if not bpath.exists():
        raise FileNotFoundError(f"base result {bpath.name} not found in {out_dir}")
    _, b = load_result(bpath)
    rows = []
    if variants:
        for v in variants:
            vg, _, vt = v.partition("@")
            vpath = out_dir / f"{vg}_{task}_{split}{'@' + vt if vt else ''}.json"
            if not vpath.exists():
                raise FileNotFoundError(f"variant result {vpath.name} not found in {out_dir}")
            label, a = load_result(vpath)
            d = paired_delta(a, b, metric)
            rows.append({"variant": label, **d, "verdict": verdict(d, label, base, metric)})
    for path in ([] if variants else sorted(out_dir.glob(f"*_{task}_{split}*.json"))):
        stem_split = path.stem.split("_", 2)[2]
        if path == bpath or stem_split.split("@", 1)[0] != split:
            continue
        label, a = load_result(path)
        if not all_tags and label.partition("@")[2] != btag:
            continue
        d = paired_delta(a, b, metric)
        rows.append({"variant": label, **d, "verdict": verdict(d, label, base, metric)})
    fmt = lambda v: "-" if v is None else f"{v:+.4f}"
    lines = [f"# Paired comparison against {base} (task {task}, split {split}, {metric})", "",
             "Each variant minus the base on the same folds and seeds (same test pairs when the digests match).", "",
             "| Variant | paired folds | mean difference | 95% CI | folds higher | same test set |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        ci = "-" if r["ci_low"] is None else f"{r['ci_low']:+.4f} to {r['ci_high']:+.4f}"
        lines.append(f"| {r['variant']} | {r['n']} | {fmt(r['mean'])} | {ci} | {r['wins']} | "
                     f"{'yes' if r['digest_match'] else 'NO'} |")
    lines += [""] + [f"- {r['verdict']}" for r in rows]
    return "\n".join(lines) + "\n", rows


def compare_metrics(out_dir=OUT, base="G0", task="B", split="kfold", metrics=DEFAULT_METRICS, all_tags=False,
                    variants=None):
    """As compare_all for several metrics. Returns (markdown text: one section per metric, {metric: rows}). A metric
    no result file holds (older files) gets a section saying so instead of an empty table."""
    parts, rows_by = [], {}
    for m in metrics:
        text, rows = compare_all(out_dir, base, task, split, m, all_tags, variants)
        usable = [r for r in rows if r["n"]]
        if not usable:
            text = (f"# Paired comparison against {base} (task {task}, split {split}, {m})\n\n"
                    f"No result file with per-fold values of {m} to pair with {base}.\n")
        note = EXPLORATORY.get(m)
        if note:
            head, _, rest = text.partition("\n\n")
            text = f"{head}\n\n_{m}: {note}._\n\n{rest}"
        parts.append(text.rstrip("\n").replace("# Paired", "## Paired", 1))
        rows_by[m] = rows
    return "# Paired comparisons\n\n" + "\n\n".join(parts) + "\n", rows_by


def split_results(out_dir=OUT):
    """Takes the results folder. Returns (kept, excluded): kept = {graph: {task: {split: summary}}} for the result
    files whose tag starts with 'frozen' or 'diag' (the shape evaluate.report_markdown takes), excluded = sorted file
    names of every other result file (untagged and smoke variants such as @dm, @wired, or an unreadable file)."""
    kept, excluded = {}, []
    for path in sorted(Path(out_dir).glob("*_*_*.json")):
        graph, task, split = path.stem.split("_", 2)
        tag = split.split("@", 1)[1] if "@" in split else ""
        try:
            summary = json.loads(path.read_text())["summary"]
        except (ValueError, KeyError, TypeError):
            excluded.append(path.name)
            continue
        if not AUTHORITATIVE_TAG.match(tag):
            excluded.append(path.name)
            continue
        kept.setdefault(graph, {}).setdefault(task, {})[split] = summary
    return kept, excluded


def write_report(out_dir=OUT):
    """Writes out_dir/report.md from split_results: the tagged frozen / diagnostic runs, then the excluded file names
    with the reason. Returns (path, kept, excluded)."""
    from linkpred.evaluate import report_markdown
    kept, excluded = split_results(out_dir)
    text = report_markdown(kept)
    text += ("\n## Not tabulated (untagged or smoke result files: sampled graphs, 50 epochs, old test set; do not cite)"
             "\n\n" + ("\n".join(f"- {n}" for n in excluded) if excluded else "- none") + "\n")
    path = Path(out_dir) / "report.md"
    path.write_text(text)
    return path, kept, excluded


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base", default="G0", help="base label: a graph (G0) or a tagged variant (G2@noot)")
    p.add_argument("--task", default="B")
    p.add_argument("--split", default="kfold")
    p.add_argument("--metric", default=",".join(DEFAULT_METRICS),
                   help="one metric or a comma-separated list (default: meta-path AUROC, meta-path coverage, GNN AUROC)")
    p.add_argument("--out-dir", default=str(OUT))
    p.add_argument("--all-tags", action="store_true",
                   help="compare with every result of the split, not only those with the base's tag")
    p.add_argument("--variants", default="",
                   help="comma-separated labels to compare with the base, e.g. G2@frozen_a,G3@frozen_a (overrides the tag rule)")
    args = p.parse_args(argv)
    metrics = [m for m in args.metric.split(",") if m]
    variants = [v for v in args.variants.split(",") if v] or None
    if len(metrics) == 1:
        text, _ = compare_all(args.out_dir, args.base, args.task, args.split, metrics[0], args.all_tags, variants)
    else:
        text, _ = compare_metrics(args.out_dir, args.base, args.task, args.split, metrics, args.all_tags, variants)
    path = Path(args.out_dir) / "compare.md"
    path.write_text(text)
    print(text)
    print(f"written to {path}")
    rpath, kept, excluded = write_report(args.out_dir)
    print(f"written to {rpath} ({sum(len(t) for t in kept.values())} graphs tabulated, {len(excluded)} files excluded)")


if __name__ == "__main__":
    sys.exit(main())
