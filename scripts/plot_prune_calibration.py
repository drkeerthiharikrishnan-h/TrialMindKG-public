"""Figure: Open Targets corroboration of Pharos gene-disease edges by tenth of pruner confidence (hand-off 2026-11-11, S01).

    .venv/bin/python scripts/plot_prune_calibration.py [--cal outputs/pharos/prune_calibration.json]
        [--out docs/figures/prune_calibration_deciles.png] [--note-decile 2] [--note-source "UniProt Disease"]

Reads  outputs/pharos/prune_calibration.json (written by pipelines/prune_calibration.py; nothing is recomputed here)
Writes docs/figures/prune_calibration_deciles.png (300 dpi)

Every number in the figure is read from the JSON: the two rates per tenth (by_decile), the confidence ranges, the edge
counts, the pruned count (by_verdict) and the note on one tenth (by_decile_source, by_source). The sentence title is
used only if it is true of the data (the most-confident tenth has the highest rate on both lines); otherwise the
title is a plain description. The figure carries no file path, run tag or graph code (rule R11).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOCAL, COMPARATOR = "#1f5fa8", "#8a8a8a"
CLAIM_TITLE = "Open Targets agrees most often with the edges the pruner is most confident in"
PLAIN_TITLE = "Open Targets corroboration by tenth of pruner confidence"
STYLE = {"font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8, "xtick.labelsize": 6, "ytick.labelsize": 6,
         "axes.spines.top": False, "axes.spines.right": False, "xtick.direction": "out", "ytick.direction": "out",
         "savefig.dpi": 300, "pdf.fonttype": 42, "ps.fonttype": 42}


def series(cal):
    """Calibration JSON -> (deciles sorted, % in Open Targets at any score, % at score >= threshold)."""
    dec = sorted(cal["by_decile"], key=lambda d: d["decile"])
    return dec, [100 * d["in_ot"] for d in dec], [100 * d["ot_strong"] for d in dec]


def title_for(any_, strong):
    """The sentence title if the last (most-confident) tenth is the strict maximum of both lines, else the plain one."""
    top = all(len(v) > 1 and v[-1] > max(v[:-1]) for v in (any_, strong))
    return CLAIM_TITLE if top else PLAIN_TITLE


def decile_note(cal, decile, source):
    """'tenth <d> includes <n> of the <N> <source> edges' from by_decile_source and by_source; None when the JSON has
    no such row (older files have no by_decile_source table)."""
    n = next((r["n"] for r in cal.get("by_decile_source", []) if r["decile"] == decile and r["source"] == source), None)
    total = next((r["n"] for r in cal.get("by_source", []) if r["source"] == source), None)
    if not n or not total:
        return None
    return f"tenth {decile} includes {int(n):,} of the {int(total):,}\n{source} edges"


def footer(cal, dec):
    """The n statement under the axes, computed from the JSON."""
    ns = sorted({int(d["n"]) for d in dec})
    per = f"{ns[0]:,} per tenth" if len(ns) == 1 else f"{ns[0]:,} to {ns[-1]:,} per tenth"
    line2 = "The pruner never reads Open Targets."
    pruned = next((int(r["n"]) for r in cal.get("by_verdict", []) if r["verdict"] == "prune"), None)
    if pruned is not None:
        line2 = f"{pruned:,} of these edges are pruned. " + line2
    return f"{int(cal['n_non_text']):,} gene\u2013disease edges from sources other than text mining; {per}.\n{line2}"


def plot_deciles(cal, out, note_decile=2, note_source="UniProt Disease"):
    """Draws the figure from a calibration dict and saves it to `out`. Returns {'title', 'note', 'footer', 'any',
    'strong'} (what was drawn), so a test can check the figure against the data."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    dec, any_, strong = series(cal)
    x = [d["decile"] for d in dec]
    thr = cal["ot_threshold"]
    title, note, foot = title_for(any_, strong), decile_note(cal, note_decile, note_source), footer(cal, dec)
    top = max(any_ + strong)
    ymax = 10 * (int(top // 10) + 1)
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(7.0, 3.9))
        ax.plot(x, any_, color=COMPARATOR, lw=1.2, marker="o", ms=3.5, zorder=2)
        ax.plot(x, strong, color=FOCAL, lw=1.8, marker="o", ms=4.5, zorder=3)
        ax.set_xlim(x[0] - 0.4, x[-1] + 0.75)
        ax.set_ylim(0, ymax)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{d['decile']}\n{d['conf_min']:.2f}\u2013{d['conf_max']:.2f}" for d in dec])
        ax.set_xlabel("Tenth of edges by pruner confidence (1 = least confident), with its confidence range")
        ax.set_ylabel("Gene\u2013disease pairs also in Open Targets (%)")
        ax.set_title(title, loc="left")
        # series identity next to the last segment of each line: grey above it, blue below it
        ax.text(x[-1] - 0.7, any_[-1] - 0.12 * ymax, "any Open Targets score", color=COMPARATOR, ha="right", va="center", fontsize=7)
        ax.text(x[-1] + 0.72, strong[-2] - 0.07 * ymax, f"Open Targets score \u2265 {thr}", color=FOCAL, ha="right", va="top", fontsize=7)
        ax.annotate(f"{strong[0]:.1f}%", (x[0], strong[0]), xytext=(0, -9), textcoords="offset points", ha="center",
                    color=FOCAL, fontsize=7)
        ax.annotate(f"{strong[-1]:.0f}%", (x[-1], strong[-1]), xytext=(7, 0), textcoords="offset points", ha="left",
                    va="center", color=FOCAL, fontsize=7)
        if note and note_decile in x:
            i = x.index(note_decile)
            ax.annotate(note, (note_decile, any_[i]), xytext=(note_decile + 0.45, min(any_[i] + 0.2 * ymax, 0.9 * ymax)),
                        ha="left", va="center", fontsize=7, color="#333333",
                        arrowprops=dict(arrowstyle="-", lw=0.6, color="#666666", shrinkA=1, shrinkB=3))
        fig.text(0.012, 0.012, foot, fontsize=6, color="#444444", ha="left", va="bottom", linespacing=1.3)
        fig.tight_layout(rect=(0, 0.075, 1, 1))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=300)
        plt.close(fig)
    return {"title": title, "note": note, "footer": foot, "any": any_, "strong": strong}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cal", default=str(ROOT / "outputs" / "pharos" / "prune_calibration.json"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "figures" / "prune_calibration_deciles.png"))
    ap.add_argument("--note-decile", type=int, default=2)
    ap.add_argument("--note-source", default="UniProt Disease")
    a = ap.parse_args(argv)
    drawn = plot_deciles(json.loads(Path(a.cal).read_text()), a.out, a.note_decile, a.note_source)
    print(f"{a.out}\ntitle: {drawn['title']}\nnote: {drawn['note']!r}\nfooter: {drawn['footer']!r}")
    print("any score %:", [round(v, 1) for v in drawn["any"]])
    print("score >= threshold %:", [round(v, 1) for v in drawn["strong"]])


if __name__ == "__main__":
    sys.exit(main())
