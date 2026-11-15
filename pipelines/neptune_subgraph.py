"""
Kinase-family subgraph of an ASSEMBLED Pharos graph as Amazon Neptune openCypher bulk-load CSVs (packet CC-9).

Why a subgraph: the whole assembled graph is tens of millions of edges; the hosted demo needs a graph that loads in
minutes and costs little, and the Pharos question the brief cares about (kinases: drugs, diseases, pathways) is
self-contained. The selection is a rule on the data, not a list typed by hand:

  seeds      = every node whose `idg_family` property equals the family (default "Kinase"; the Pharos "IDG Family")
  edges      = every edge with a seed at either end whose verdict is not "prune" (an empty verdict means the layer was
               never pruned and is kept; nothing is labelled keep that the pruner did not look at)
  nodes      = the seeds plus the other end of each kept edge (1 hop)

    python pipelines/neptune_subgraph.py [G3] [--family Kinase] [--out DIR]       (reads data/kg via export_graph_kgx)

  select_subgraph(nodes, edges, family)    -> (nodes, edges, report)         tables in, tables out (pandas, strings)
  write_opencypher(nodes, edges, out_dir)  -> (nodes.csv, edges.csv)         Neptune openCypher format, byte-deterministic
  validate_opencypher(nodes_csv, edges_csv)-> [problems]                     the load-format gate (empty = loadable)
  load_into_index(nodes_csv, edges_csv)    -> object querier.run_local can query (what Neptune would hold)
  parity_questions(nodes, edges)           -> [{id, question, cypher}]       same questions for local and Neptune
  write_manifest(...)                      -> dict, also written as ../manifest.json; aws/neptune_load.sh checks its counts

The manifest also stores, per parity question, the evidence edge ids the in-memory subgraph returns (their count and a
sha256 of the sorted ids). Claude Science runs the same openCypher on the loaded cluster and compares: identical ids
mean the Neptune copy answers exactly like the local graph. Nothing here has run against a cluster.
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
FAMILY_COLUMN = "idg_family"
NODE_PROPS = ["category", "name", "provided_by", FAMILY_COLUMN, "tdl", "symbol"]
EDGE_PROPS = ["predicate", "primary_knowledge_source", "aggregator_knowledge_source", "knowledge_level", "agent_type",
              "source", "source_record", "layer", "verdict", "confidence", "reasons"]
DOUBLE_COLUMNS = {"confidence"}


def _local(term):
    return str(term).split(":", 1)[1] if ":" in str(term) else str(term)


def select_subgraph(nodes, edges, family="Kinase"):
    """Takes the two assembled tables (export_graph_kgx.assemble_kgx; strings, a `verdict` column on the edges) and the
    IDG family. Returns (nodes, edges, report): the seeds' 1-hop neighbourhood over non-pruned edges. report: seeds,
    kept / pruned edge counts, edges skipped because the far endpoint has no node row, the family column used."""
    if FAMILY_COLUMN not in nodes:
        raise ValueError(f"nodes have no {FAMILY_COLUMN!r} column: the Pharos layer is not part of this graph")
    seeds = set(nodes.loc[nodes[FAMILY_COLUMN].fillna("").str.lower() == family.lower(), "id"])
    touching = edges["subject"].isin(seeds) | edges["object"].isin(seeds)
    pruned = edges.get("verdict", pd.Series("", index=edges.index)).fillna("") == "prune"
    cand = edges[touching & ~pruned]
    have = set(nodes["id"])
    ok = cand["subject"].isin(have) & cand["object"].isin(have)
    kept = cand[ok].sort_values("id", kind="mergesort")
    ids = seeds | set(kept["subject"]) | set(kept["object"])
    sub_nodes = nodes[nodes["id"].isin(ids)].sort_values("id", kind="mergesort")
    rep = {"family": family, "seeds": len(seeds), "edges_touching_seeds": int(touching.sum()),
           "edges_pruned_excluded": int((touching & pruned).sum()), "edges_missing_endpoint": int((~ok).sum()),
           "nodes": len(sub_nodes), "edges": len(kept)}
    return sub_nodes.reset_index(drop=True), kept.reset_index(drop=True), rep


def _cell(v):
    return "" if v is None or (isinstance(v, float) and v != v) else str(v).replace("\r\n", "\\n").replace("\n", "\\n")


def write_opencypher(nodes, edges, out_dir):
    """Takes the subgraph tables and a folder. Writes nodes.csv and edges.csv in the Neptune openCypher bulk-load
    format (system columns :ID/:LABEL and :ID/:START_ID/:END_ID/:TYPE, typed property columns, 'id' repeated as a
    property so n.id / r.id work as they do in querier.run_local). Label = the BioLink class without its prefix;
    :TYPE = the predicate without its prefix. Columns absent from the tables are left out. Returns the two paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nprops = [c for c in NODE_PROPS if c in nodes]
    eprops = [c for c in EDGE_PROPS if c in edges]
    npath, epath = out_dir / "nodes.csv", out_dir / "edges.csv"
    typed = lambda c: f"{c}:Double" if c in DOUBLE_COLUMNS else f"{c}:String"   # noqa: E731
    with open(npath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([":ID", ":LABEL", "id:String"] + [typed(c) for c in nprops])
        for _, r in nodes.iterrows():
            w.writerow([r["id"], _local(str(r["category"]).split("|")[0]), r["id"]] + [_cell(r.get(c)) for c in nprops])
    with open(epath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow([":ID", ":START_ID", ":END_ID", ":TYPE", "id:String"] + [typed(c) for c in eprops])
        for _, r in edges.iterrows():
            w.writerow([r["id"], r["subject"], r["object"], _local(r["predicate"]), r["id"]] +
                       [_cell(r.get(c)) for c in eprops])
    return npath, epath


def _read(path):
    csv.field_size_limit(sys.maxsize)
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def validate_opencypher(nodes_csv, edges_csv):
    """Takes the two written files. Returns the problems that would make the Neptune loader fail or load a wrong
    graph: system columns, empty / duplicate ids, empty labels or types, an edge endpoint that is not a node,
    unparseable Double cells, a cell with a raw newline, a different number of cells than the header."""
    problems = []
    nrows, erows = _read(nodes_csv), _read(edges_csv)
    nh, eh = nrows[0], erows[0]
    if nh[:2] != [":ID", ":LABEL"]:
        problems.append(f"nodes header must start :ID,:LABEL, got {nh[:2]}")
    if eh[:4] != [":ID", ":START_ID", ":END_ID", ":TYPE"]:
        problems.append(f"edges header must start :ID,:START_ID,:END_ID,:TYPE, got {eh[:4]}")
    if problems:
        return problems
    for label, head in (("nodes", nh), ("edges", eh)):
        for c in head:
            if not c.startswith(":") and c.count(":") != 1:
                problems.append(f"{label}: column {c!r} is not name:Type")
            elif not c.startswith(":") and c.split(":")[1] not in {"String", "Double", "Long", "Bool", "Int"}:
                problems.append(f"{label}: column {c!r} has an unknown type")
    ids = [r[0] for r in nrows[1:]]
    if len(set(ids)) != len(ids):
        problems.append(f"nodes: {len(ids) - len(set(ids)):,} duplicate :ID")
    if "" in ids:
        problems.append("nodes: empty :ID")
    nodeset = set(ids)
    eids = [r[0] for r in erows[1:]]
    if len(set(eids)) != len(eids):
        problems.append(f"edges: {len(eids) - len(set(eids)):,} duplicate :ID")
    for i, r in enumerate(nrows[1:], 2):
        if len(r) != len(nh):
            problems.append(f"nodes row {i}: {len(r)} cells, header has {len(nh)}")
        elif not r[1]:
            problems.append(f"nodes row {i}: empty :LABEL")
    doubles = [j for j, c in enumerate(eh) if c.endswith(":Double")]
    for i, r in enumerate(erows[1:], 2):
        if len(r) != len(eh):
            problems.append(f"edges row {i}: {len(r)} cells, header has {len(eh)}")
            continue
        if not r[3]:
            problems.append(f"edges row {i}: empty :TYPE")
        for end, name in ((r[1], ":START_ID"), (r[2], ":END_ID")):
            if end not in nodeset:
                problems.append(f"edges row {i}: {name} {end!r} is not a node")
        for j in doubles:
            try:
                if r[j] != "":
                    float(r[j])
            except ValueError:
                problems.append(f"edges row {i}: {eh[j]} value {r[j]!r} is not a number")
    return problems[:50]


class _Index:
    """The few attributes querier.run_local reads: nodes, edges, by_subject, by_object (same shape as KGIndex)."""

    def __init__(self, nodes, edges):
        self.nodes, self.edges = nodes, edges
        self.by_subject, self.by_object = {}, {}
        for e in edges.values():
            self.by_subject.setdefault(e["subject"], []).append(e)
            self.by_object.setdefault(e["object"], []).append(e)


def index_from_tables(nodes, edges):
    """The in-memory subgraph as run_local expects it: node category 'biolink:X', edge predicate kept as written."""
    n = {r["id"]: {k: v for k, v in r.items() if v not in ("", None)} for r in nodes.to_dict("records")}
    e = {r["id"]: {k: v for k, v in r.items() if v not in ("", None)} for r in edges.to_dict("records")}
    return _Index(n, e)


def load_into_index(nodes_csv, edges_csv):
    """Reads the two written files back the way Neptune stores them (label, type and the typed property columns) into
    an object querier.run_local can query. The node category is rebuilt as 'biolink:' + label and the predicate from
    the property column, so a query returns what it would return on the loaded cluster."""
    nrows, erows = _read(nodes_csv), _read(edges_csv)
    nh, eh = [c.split(":")[0] if not c.startswith(":") else c for c in nrows[0]], \
        [c.split(":")[0] if not c.startswith(":") else c for c in erows[0]]
    nodes, edges = {}, {}
    for r in nrows[1:]:
        d = {k: v for k, v in zip(nh, r) if v != "" and not k.startswith(":")}
        d["category"] = d.get("category") or f"biolink:{r[1]}"
        nodes[r[0]] = d
    for r in erows[1:]:
        d = {k: v for k, v in zip(eh, r) if v != "" and not k.startswith(":")}
        d.update(id=r[0], subject=r[1], object=r[2])
        d["predicate"] = d.get("predicate") or r[3]
        edges[r[0]] = d
    return _Index(nodes, edges)


def parity_questions(nodes, edges, family="Kinase"):
    """Takes the subgraph tables. Returns up to four fixed openCypher questions built from the classes and predicates
    that really occur between a seed and its neighbours (nothing typed by hand about the data): per class pair, the
    most frequent predicate. Each is {id, question, cypher}; every cypher returns a relationship id as `evidence`."""
    cat = {r["id"]: _local(str(r["category"]).split("|")[0]) for r in nodes.to_dict("records")}
    fam = nodes.get(FAMILY_COLUMN, pd.Series("", index=nodes.index)).fillna("")
    seeds = set(nodes.loc[fam.str.lower() == family.lower(), "id"])
    counts = {}
    for r in edges.to_dict("records"):
        if r["subject"] in seeds:
            k = (cat[r["subject"]], _local(r["predicate"]), cat[r["object"]], "out")
        elif r["object"] in seeds:
            k = (cat[r["object"]], _local(r["predicate"]), cat[r["subject"]], "in")
        else:
            continue
        counts[k] = counts.get(k, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    seen, out = set(), []
    for (s, p, o, d), _ in ranked:
        if (p, o) in seen:
            continue
        seen.add((p, o))
        qid = f"K{len(out) + 1:02d}"
        arrow = f"(a:{s} {{{FAMILY_COLUMN}: \"{family}\"}})-[r:{p}]->(b:{o})" if d == "out" else \
            f"(b:{o})-[r:{p}]->(a:{s} {{{FAMILY_COLUMN}: \"{family}\"}})"
        out.append({"id": qid, "question": f"Which {o} nodes are linked to a {family} {s} by {p}?",
                    "cypher": f"MATCH {arrow} RETURN a.id AS target, b.id AS other, r.id AS evidence ORDER BY target, other, evidence LIMIT 500"})
        if len(out) == 4:
            break
    return out


def evidence_digest(result):
    """Takes a run_local answer. Returns {n_rows, n_evidence, evidence_sha256} (sha256 of the sorted edge ids joined
    by newlines): what Claude Science compares with the same query on the loaded cluster."""
    ev = sorted(result["evidence"])
    return {"n_rows": len(result["rows"]), "n_evidence": len(ev),
            "evidence_sha256": hashlib.sha256("\n".join(ev).encode()).hexdigest()}


def write_manifest(out_dir, rep, nodes, edges, graph="G3", extra=None):
    """Writes <out_dir>/../manifest.json (one level up: the Neptune loader reads every file under the CSV folder and
    would choke on a .json): {'counts': {'nodes','edges'}} (the keys aws/neptune_load.sh compares with the
    Neptune counts), the selection report, file sha256s and, per parity question, its evidence digest from the
    in-memory subgraph. Returns the dict."""
    import querier
    out_dir = Path(out_dir)
    idx = index_from_tables(nodes, edges)
    parity = {}
    for q in parity_questions(nodes, edges, rep.get("family", "Kinase")):
        parity[q["id"]] = {"question": q["question"], "cypher": q["cypher"],
                           **evidence_digest(querier.run_local(q["cypher"], idx))}
    man = {"graph": graph, "counts": {"nodes": len(nodes), "edges": len(edges)}, "selection": rep,
           "files": {n: hashlib.sha256((out_dir / n).read_bytes()).hexdigest() for n in ("nodes.csv", "edges.csv")},
           "parity": parity, **(extra or {})}
    (out_dir.parent / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
    return man


def build(graph="G3", family="Kinase", out=None, root=ROOT):
    """End to end on the real data: assemble the graph (pruned edges kept and marked), select, write, validate,
    write the manifest. Returns (manifest, problems). Default out = outputs/neptune_kinase/opencypher/."""
    import export_graph_kgx as xk
    nodes, edges, rep = xk.assemble_kgx(graph, root, include_pruned=True)
    if nodes is None:
        raise SystemExit(f"{graph}: layers missing ({', '.join(rep['missing'])}); build them first")
    sub_n, sub_e, srep = select_subgraph(nodes, edges, family)
    out = Path(out or root / "outputs" / "neptune_kinase" / "opencypher")
    npath, epath = write_opencypher(sub_n, sub_e, out)
    problems = validate_opencypher(npath, epath)
    return write_manifest(out, srep, sub_n, sub_e, graph), problems


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("graph", nargs="?", default="G3")
    p.add_argument("--family", default="Kinase")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    man, problems = build(a.graph, a.family, a.out)
    print(json.dumps({"counts": man["counts"], "selection": man["selection"]}, indent=2))
    for q in problems:
        print("PROBLEM:", q)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
