"""Judge's card figure: AUROC per fold, Pharos alone vs the agent-built graph vs the pruned graph (hand-off 2026-11-11, S10).

    export PYTHONPATH=<repo>
    .venv/bin/python scripts/plot_judges_card.py [--out docs/figures/judges_card_paired_folds.png]

Reads (nothing is recomputed; the pairing and the interval come from pipelines.linkpred.compare, the code that writes
outputs/linkpred/compare_cs9_gnnpath.md and the README headline):
    outputs/linkpred/G0_B_kfold@diag_gnnpath_1m_gpu.json   (Pharos alone)
    outputs/linkpred/G2_B_kfold@diag_gnnpath_1m_cs9.json   (agent-built graph, shipped)
    outputs/linkpred/G3_B_kfold@diag_gnnpath_1m_cs9.json   (agent-built graph after pruning, shipped)
Writes docs/figures/judges_card_paired_folds.png (300 dpi) and prints the numbers it drew.

Metric auroc_gnnpath (the GNN that also counts paths). The figure refuses to draw when the three files were not scored on
the same test set (digest) or do not share every fold. The sentence title is used only when every fold is higher on both
agent-built graphs; otherwise a plain description. The figure carries no file path, run tag or graph code (rule R11).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipelines.linkpred import compare  # noqa: E402

METRIC = "auroc_gnnpath"
RUNS = [("Pharos alone", "G0_B_kfold@diag_gnnpath_1m_gpu.json"),
        ("Agent-built graph", "G2_B_kfold@diag_gnnpath_1m_cs9.json"),
        ("Agent-built, pruned", "G3_B_kfold@diag_gnnpath_1m_cs9.json")]
FOCAL, COMPARATOR, FOLD_LINE = "#1f5fa8", "#8a8a8a", "#c4c4c4"
CLAIM_TITLE = "Same model, same held-out gene\u2013disease pairs: both agent-built graphs score higher than Pharos alone in all {n} folds"
PLAIN_TITLE = "AUROC per fold: Pharos alone, the agent-built graph and the pruned graph"
# apply_figure_style() defaults (figure-style skill, sizes 8/7/6), copied so the script runs under .venv alone
STYLE = {"font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "legend.fontsize": 7, "xtick.labelsize": 6,
         "ytick.labelsize": 6, "axes.titlelocation": "left", "axes.linewidth": 0.6, "axes.spines.top": False,
         "axes.spines.right": False, "xtick.major.size": 3, "ytick.major.size": 3, "xtick.major.width": 0.6,
         "ytick.major.width": 0.6, "lines.linewidth": 1.2, "legend.frameon": False, "savefig.dpi": 300,
         "savefig.bbox": "tight", "pdf.fonttype": 42, "ps.fonttype": 42}


def series(results, metric=METRIC):
    """[(label, parsed result)] with the base first -> (folds, values, deltas).
    folds: sorted (seed, fold) keys shared by every run; values: {label: [value per fold]};
    deltas: {label: compare.paired_delta(run, base)} for every run after the base.
    Raises ValueError when the runs were scored on different test sets or do not share the same folds."""
    base_label, base = results[0]
    keys = [set(compare.fold_values(r, metric)) for _, r in results]
    if not keys[0] or any(k != keys[0] for k in keys):
        raise ValueError("the runs do not share the same folds")
    deltas = {}
    for label, r in results[1:]:
        d = compare.paired_delta(r, base, metric)
        if not d["digest_match"]:
            raise ValueError(f"{label} was scored on a different test set than {base_label}")
        deltas[label] = d
    folds = sorted(keys[0])
    values = {label: [compare.fold_values(r, metric)[k] for k in folds] for label, r in results}
    return folds, values, deltas


def title_for(deltas):
    """The sentence title if every paired fold is higher for every agent-built run, else the plain one."""
    ok = deltas and all(d["n"] >= 2 and d["wins"] == d["n"] for d in deltas.values())
    n = {d["n"] for d in deltas.values()}
    return CLAIM_TITLE.format(n=n.pop()) if ok and len(n) == 1 else PLAIN_TITLE


def delta_label(d):
    """'+0.124 vs Pharos alone\\n95% CI +0.117 to +0.130' (three decimals, as the README headline quotes them)."""
    return f"{d['mean']:+.3f} vs Pharos alone\n(95% CI {d['ci_low']:+.3f} to {d['ci_high']:+.3f})"


def footer(result, n_folds):
    """n statement from the base run's per-fold test counts (the same test pairs in every run: digest checked)."""
    ns = [int(f["n_test"]) for s in result.get("per_seed", []) for f in s.get("folds", []) if "n_test" in f]
    if len(ns) != n_folds:
        return f"n = {n_folds} folds; thin lines join the same fold"
    return (f"n = {n_folds} folds of {sum(ns):,} curated gene\u2013disease pairs ({min(ns):,} to {max(ns):,} per fold); "
            f"thin lines join the same fold")


def draw(results, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update(STYLE)
    folds, values, deltas = series(results)
    labels = [lab for lab, _ in results]
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    xs = list(range(len(labels)))
    for i in range(len(folds)):
        ax.plot(xs, [values[lab][i] for lab in labels], color=FOLD_LINE, lw=0.7, zorder=1)
    for x, lab in zip(xs, labels):
        c = COMPARATOR if x == 0 else FOCAL
        v = values[lab]
        ax.scatter([x] * len(v), v, s=14, color=c, zorder=3, linewidths=0)
        m = sum(v) / len(v)
        ax.plot([x - 0.16, x + 0.16], [m, m], color=c, lw=2.0, zorder=2, solid_capstyle="butt")
    for x, lab in zip(xs[1:], labels[1:]):
        ax.annotate(delta_label(deltas[lab]), (x, max(values[lab])), xytext=(0, 6), textcoords="offset points",
                    ha="center", va="bottom", fontsize=7, color=FOCAL)
    ax.set_xticks(xs, labels)
    ax.set_xlim(-0.45, len(labels) - 0.55)
    lo = min(min(v) for v in values.values())
    hi = max(max(v) for v in values.values())
    ax.set_ylim(lo - 0.01, hi + 0.03)
    ax.set_ylabel("AUROC (higher = better)")
    ax.set_title(title_for(deltas), wrap=True)
    fig.text(0.0, -0.13, footer(results[0][1], len(folds)), fontsize=6, color="#555555", ha="left", va="top",
             transform=ax.transAxes)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return folds, values, deltas


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default=str(ROOT / "outputs" / "linkpred"))
    p.add_argument("--out", default=str(ROOT / "docs" / "figures" / "judges_card_paired_folds.png"))
    a = p.parse_args(argv)
    results = [(lab, json.loads((Path(a.dir) / f).read_text())) for lab, f in RUNS]
    folds, values, deltas = draw(results, Path(a.out))
    for lab in values:
        v = values[lab]
        print(f"{lab}: folds {[round(x, 4) for x in v]} mean {sum(v) / len(v):.4f}")
    for lab, d in deltas.items():
        print(f"{lab} - Pharos alone: {d['mean']:+.4f} (95% CI {d['ci_low']:+.4f} to {d['ci_high']:+.4f}; "
              f"{d['wins']} of {d['n']} folds higher; same test set {d['digest_match']})")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
