"""E009 control graph G2R: G2 with exactly as many edges removed per (edge type, layer) as the pruner removed for G3,
chosen uniformly at random (seed 0) instead of by verdict. Pre-registered in loop/ledger.tsv (E009) before this ran.

    python scripts/make_random_pruned.py          -> outputs/g2r/heterodata.pt + outputs/g2r/g2r_report.json

Test pairs always come from G0 (run.REFERENCE_GRAPH), so G2R is benchmarked on the same test set as G2/G3.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from kgbuilder.pyg_export import load_pyg, save_pyg  # noqa: E402
from linkpred.run import subset_edges  # noqa: E402  (imported, not edited: run.py is frozen)

SEED = 0


def layer_counts(store):
    lay = np.asarray(store.edge_layer)
    u, k = np.unique(lay, return_counts=True)
    return dict(zip(u.tolist(), k.tolist())), lay


def main():
    g2 = load_pyg(ROOT / "outputs/g2/heterodata.pt")
    g3 = load_pyg(ROOT / "outputs/g3/heterodata.pt")
    rng = np.random.default_rng(SEED)
    report, removed_by_layer = [], Counter()
    for et in list(g2.edge_types):
        n = g2[et].edge_index.size(1)
        c2, lay = layer_counts(g2[et])
        c3 = layer_counts(g3[et])[0] if et in g3.edge_types else {}
        keep = np.ones(n, dtype=bool)
        for layer, k2 in c2.items():
            drop = k2 - c3.get(layer, 0)
            if drop < 0:
                raise SystemExit(f"{et} {layer}: G3 has more edges than G2")
            if drop:
                idx = np.flatnonzero(lay == layer)
                keep[rng.choice(idx, size=drop, replace=False)] = False
                removed_by_layer[layer] += int(drop)
        if not keep.all():
            subset_edges(g2[et], np.flatnonzero(keep), n)
        after = layer_counts(g2[et])[0] if g2[et].edge_index.size(1) else {}
        assert after == {k: v for k, v in c3.items() if v}, f"count mismatch for {et}"
        report.append({"edge_type": "|".join(et), "g2": n, "g2r": int(keep.sum()), "g3": int(sum(c3.values()))})
    out = ROOT / "outputs/g2r"
    out.mkdir(parents=True, exist_ok=True)
    save_pyg(g2, out / "heterodata.pt")
    summary = {"seed": SEED, "removed_total": int(sum(removed_by_layer.values())), "removed_by_layer": dict(removed_by_layer),
               "per_edge_type": report, "note": "E009 random-pruning control; counts per (edge type, layer) equal G3"}
    (out / "g2r_report.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in ("removed_total", "removed_by_layer")}))


if __name__ == "__main__":
    main()
