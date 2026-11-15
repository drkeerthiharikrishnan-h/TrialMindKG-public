"""
Choose the pruning settings on a SEPARATE set of injected errors (docs/BACKUP_PLAN.md, B2).

    python pipelines/prune_compare.py sources/pharos.yaml [--seed 1] [--n-injected 50000]

Scores the built graph (data/kg/<name>/edges.tsv.gz) with each variant below, using injected errors drawn with
--seed (default 1; prune_kg.py measures the final numbers with seed 0, so the choice is not made on the errors the
reported metrics come from). Prints a table and writes outputs/<name>/prune_compare.md. Nothing in the graph or the
verdict file changes: put the chosen settings into the card's `prune:` section, then run prune_kg.py.

Variants: v1 (the card's settings as they were: one SVD of 32 dimensions, trust weight 0.2), then one change at a
time and all changes together: trust weight 0, 64 dimensions, plausibility per predicate group.

  variants(base)                -> [(label, cfg)]
  compare(edges, nodes, base, seed, n_injected) -> list of {label, auprc_errors, auprc_learned, recall, pruned, ...}
  markdown(name, rows, seed)    -> text
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kgbuilder import engine, prune  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
V11_KEYS = ("version", "trust_weight", "svd_k", "plausibility_by", "protect_curated_corroborated")


def variants(base):
    """Takes the card's prune section. Returns [(label, cfg)]: v1 (the v1.1 keys removed) and the v1.1 changes one
    at a time, then all together. The source-specific keys (score columns, pair groups, generic terms) are kept."""
    v1 = {k: v for k, v in base.items() if k not in V11_KEYS}
    return [("v1 (k 32, trust 0.2, one matrix)", v1),
            ("trust weight 0", {**v1, "trust_weight": 0.0}),
            ("SVD 64 dimensions", {**v1, "svd_k": 64}),
            ("plausibility per predicate group", {**v1, "plausibility_by": "predicate_group"}),
            ("all three", {**v1, "trust_weight": 0.0, "svd_k": 64, "plausibility_by": "predicate_group"})]


def compare(edges, nodes, base, seed=1, n_injected=None, log=print):
    """Scores every variant on the same injected set (same seed). Returns one dict of metrics per variant."""
    rows = []
    for label, cfg in variants(base):
        _, m = prune.score(edges, nodes, cfg, seed=seed, n_injected=n_injected)
        row = {"label": label, "auprc_errors": m["auprc_errors"], "auprc_learned": m["auprc_learned"],
               "auprc_plausibility": m["auprc_plausibility"], "precision": m["precision"], "recall": m["recall"],
               "pruned_real_fraction": m["pruned_real_fraction"], "base_rate": m["error_base_rate"],
               "recall_gene_condition": m["recall_by_predicate"].get("biolink:gene_associated_with_condition")}
        log(f"  {label}: AUPRC {row['auprc_errors']:.3f}, recall {row['recall']:.3f}, "
            f"real edges pruned {row['pruned_real_fraction']:.3f}")
        rows.append(row)
    return rows


def markdown(name, rows, seed):
    """The comparison table; the sentence under it names the variant with the highest AUPRC, computed from the
    rows, and says how far it is from v1."""
    lines = [f"# Pruning settings compared - {name} (injected errors seed {seed})", "",
             "Every variant scored on the same injected errors; prune_kg.py reports the final numbers on a "
             "different set (seed 0).", "",
             "| variant | AUPRC (combined) | AUPRC (learned) | AUPRC (plausibility) | precision | recall | "
             "recall gene-condition | real edges pruned |", "|---|---|---|---|---|---|---|---|"]
    f = lambda v: "-" if v is None else f"{v:.3f}"
    for r in rows:
        lines.append(f"| {r['label']} | {f(r['auprc_errors'])} | {f(r['auprc_learned'])} | "
                     f"{f(r['auprc_plausibility'])} | {f(r['precision'])} | {f(r['recall'])} | "
                     f"{f(r['recall_gene_condition'])} | {f(r['pruned_real_fraction'])} |")
    best = max(rows, key=lambda r: r["auprc_errors"])
    d = best["auprc_errors"] - rows[0]["auprc_errors"]
    lines += ["", f"Random = base rate {rows[0]['base_rate']:.3f}. Highest combined AUPRC: {best['label']} "
              f"({best['auprc_errors']:.3f}, {d:+.3f} vs v1). Trust alone is invisible to this test (injected errors keep "
              "the original source and score), so the trust-weight rows show only how trust interacts with the other "
              "signals; whether trust helps on real errors is decided by the owner's spot check."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("card")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--n-injected", type=int, default=0)
    a = p.parse_args(argv)
    card = engine.load_card(a.card)
    kg = ROOT / "data" / "kg" / card["name"]
    edges = pd.read_csv(kg / "edges.tsv.gz", sep="\t", dtype=str, low_memory=False)
    nodes = pd.read_csv(kg / "nodes.tsv.gz", sep="\t", dtype=str, usecols=["id", "category"])
    rows = compare(edges, nodes, card.get("prune") or {}, seed=a.seed, n_injected=a.n_injected or None)
    text = markdown(card["name"], rows, a.seed)
    out = ROOT / "outputs" / card["name"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "prune_compare.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
