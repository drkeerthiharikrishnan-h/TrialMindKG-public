"""
Join audit (docs/ENRICHMENT_DIAGNOSIS_PLAN.md, experiment A3): how much of each enrichment layer actually touches
the Pharos part of a graph. A layer whose edges never meet a node that has a Pharos edge cannot help the task,
whatever the model; it is dead weight (or a sign that ids did not join: EFO: beside MONDO:, MP: beside HP:).

    python -m pipelines.linkpred.join_audit G2          -> outputs/linkpred/join_audit_G2.md

audit(node_ids, edges, l0_layer)  pure-Python core (tested without torch):
    node_ids: {node type: [ids]}; edges: {edge type: (src indices, dst indices, layer per edge or None)}
    returns {"edges": rows per (edge type, layer), "nodes": rows per node type}
markdown(report)                  the report as a table
"""
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipelines"))

L0_LAYER = "pharos"


def _prefix(nid):
    return nid.split(":", 1)[0] if ":" in nid else "(no prefix)"


def audit(node_ids, edges, l0_layer=L0_LAYER):
    """See the module docstring. Edge rows: edge type, layer, n edges, share with at least one endpoint on a
    Pharos-touched node, share with both. Node rows: node type, n nodes, n touched by Pharos, n touched by any
    edge, n isolated, id prefixes (count per prefix)."""
    touched = {nt: np.zeros(len(ids), dtype=bool) for nt, ids in node_ids.items()}
    any_edge = {nt: np.zeros(len(ids), dtype=bool) for nt, ids in node_ids.items()}
    for et, (s, d, layers) in edges.items():
        s, d = np.asarray(s), np.asarray(d)
        any_edge[et[0]][s] = True
        any_edge[et[2]][d] = True
        if layers is None:
            l0 = np.ones(len(s), dtype=bool)
        else:
            l0 = np.fromiter((l == l0_layer for l in layers), dtype=bool, count=len(s))
        touched[et[0]][s[l0]] = True
        touched[et[2]][d[l0]] = True
    edge_rows = []
    for et, (s, d, layers) in edges.items():
        s, d = np.asarray(s), np.asarray(d)
        groups = {None: np.ones(len(s), dtype=bool)} if layers is None else {
            layer: np.fromiter((l == layer for l in layers), dtype=bool, count=len(s)) for layer in sorted(set(layers))}
        for layer, m in groups.items():
            ts, td = touched[et[0]][s[m]], touched[et[2]][d[m]]
            n = int(m.sum())
            edge_rows.append({"edge_type": "|".join(et), "layer": layer or "(all)", "n_edges": n,
                              "share_one_end_on_pharos": float((ts | td).mean()) if n else 0.0,
                              "share_both_ends_on_pharos": float((ts & td).mean()) if n else 0.0})
    node_rows = []
    for nt, ids in node_ids.items():
        node_rows.append({"node_type": nt, "n_nodes": len(ids), "n_touched_by_pharos": int(touched[nt].sum()),
                          "n_with_any_edge": int(any_edge[nt].sum()), "n_isolated": int((~any_edge[nt]).sum()),
                          "prefixes": dict(Counter(_prefix(i) for i in ids).most_common(6))})
    return {"edges": edge_rows, "nodes": node_rows}


def markdown(report, graph=""):
    lines = [f"# Join audit {graph}".rstrip(), "",
             "Share of each layer's edges that touch a node holding at least one Pharos edge. A layer at 0 is not "
             "joined to Pharos (ids did not match) and cannot inform the task.", "",
             "| edge type | layer | edges | one end on Pharos | both ends |", "|---|---|---:|---:|---:|"]
    for r in report["edges"]:
        lines.append(f"| {r['edge_type']} | {r['layer']} | {r['n_edges']:,} | {r['share_one_end_on_pharos']:.1%} | "
                     f"{r['share_both_ends_on_pharos']:.1%} |")
    lines += ["", "| node type | nodes | touched by Pharos | with any edge | isolated | id prefixes |",
              "|---|---:|---:|---:|---:|---|"]
    for r in report["nodes"]:
        pre = ", ".join(f"{k} {v:,}" for k, v in r["prefixes"].items())
        lines.append(f"| {r['node_type']} | {r['n_nodes']:,} | {r['n_touched_by_pharos']:,} | {r['n_with_any_edge']:,} "
                     f"| {r['n_isolated']:,} | {pre} |")
    return "\n".join(lines) + "\n"


def from_heterodata(data):
    """HeteroData -> the (node_ids, edges) inputs of audit()."""
    node_ids = {nt: list(data[nt].node_id) for nt in data.node_types}
    edges = {}
    for et in data.edge_types:
        ei = data[et].edge_index.numpy()
        edges[et] = (ei[0], ei[1], getattr(data[et], "edge_layer", None))
    return node_ids, edges


def main(argv):
    from linkpred.run import load_graph
    out_dir = ROOT / "outputs" / "linkpred"
    out_dir.mkdir(parents=True, exist_ok=True)
    for graph in (argv or ["G2"]):
        rep = audit(*from_heterodata(load_graph(graph)))
        path = out_dir / f"join_audit_{graph}.md"
        path.write_text(markdown(rep, graph))
        print(f"{graph}: {path}")
        print(markdown(rep, graph))


if __name__ == "__main__":
    main(sys.argv[1:])
