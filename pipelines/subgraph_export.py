"""
Subgraph download for a Pharos answer (NEXT_STEPS 11b; BUILD_ROLES section 3 point 3: "the download (subgraph as
KGX + PyG)"): the nodes and edges behind one answer, as one zip a reviewer can open in a spreadsheet, load with KGX
tools, or turn into a PyTorch Geometric HeteroData in three lines. No torch needed to write it.

  collect(graph, edge_ids)        -> (nodes {id: dict}, edges [dict]) for the given edge ids (unknown ids skipped)
                                     from a graph with .edges / .nodes / .verdict (pharos_app.PharosGraph)
  kgx_tables(nodes, edges)        -> (nodes_tsv, edges_tsv) text: KGX columns first, then the other properties,
                                     verdict / confidence / reasons on every edge that has a pruning verdict
  pyg_tables(nodes, edges)        -> {file name: csv text}: pyg/nodes_<Type>.csv (index, id, name) per node type and
                                     pyg/edges_<Subject>__<predicate>__<Object>.csv (src, dst, edge_id) per edge type
  zip_bytes(nodes, edges, title)  -> the zip: kgx/nodes.tsv, kgx/edges.tsv, pyg/*.csv, pyg/load_pyg.py, README.txt
"""
import csv
import io
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kg_schema import local  # noqa: E402

NODE_FIRST = ["id", "category", "name", "symbol"]
EDGE_FIRST = ["id", "subject", "predicate", "object", "source", "source_record", "primary_knowledge_source",
              "aggregator_knowledge_source", "knowledge_level", "agent_type", "layer", "source_version", "n_records"]
VERDICT = ["verdict", "confidence", "reasons"]

LOAD_PYG = '''"""Builds a torch_geometric HeteroData from the pyg/ tables of this zip.
    python pyg/load_pyg.py           (needs torch and torch_geometric)"""
import csv, glob, os
import torch
from torch_geometric.data import HeteroData

here = os.path.dirname(os.path.abspath(__file__))
data = HeteroData()
for path in sorted(glob.glob(os.path.join(here, "nodes_*.csv"))):
    nt = os.path.basename(path)[len("nodes_"):-len(".csv")]
    rows = list(csv.DictReader(open(path)))
    data[nt].num_nodes = len(rows)
    data[nt].node_id = [r["id"] for r in rows]
for path in sorted(glob.glob(os.path.join(here, "edges_*.csv"))):
    s, p, o = os.path.basename(path)[len("edges_"):-len(".csv")].split("__")
    rows = list(csv.DictReader(open(path)))
    data[s, p, o].edge_index = torch.tensor([[int(r["src"]) for r in rows], [int(r["dst"]) for r in rows]], dtype=torch.long)
    data[s, p, o].edge_id = [r["edge_id"] for r in rows]
print(data)
'''


def collect(graph, edge_ids):
    """Takes a graph (PharosGraph or anything with .edges, .nodes and .verdict(id)) and edge ids. Returns
    (nodes, edges): the edges that exist, each with its pruning verdict fields when there is one, and both end
    nodes of every edge. Order: edge ids as given, duplicates removed."""
    nodes, edges, seen = {}, [], set()
    for eid in edge_ids:
        if eid in seen or eid not in graph.edges:
            continue
        seen.add(eid)
        e = dict(graph.edges[eid])
        v = graph.verdict(eid) if hasattr(graph, "verdict") else None
        for k in VERDICT:
            if v and v.get(k) not in (None, ""):
                e[k] = v[k]
        edges.append(e)
        for n in (e["subject"], e["object"]):
            nodes.setdefault(n, {"id": n, **graph.nodes.get(n, {})})
    return nodes, edges


def _tsv(rows, first):
    cols = [c for c in first if any(c in r for r in rows)]
    cols += sorted({k for r in rows for k in r} - set(cols))
    buf = io.StringIO()
    w = csv.DictWriter(buf, cols, delimiter="\t", lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in cols})
    return buf.getvalue()


def kgx_tables(nodes, edges):
    """Takes the collected nodes and edges. Returns (nodes_tsv, edges_tsv) as text in KGX column order."""
    return _tsv(list(nodes.values()), NODE_FIRST), _tsv(edges, EDGE_FIRST + VERDICT)


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_]+", "_", name) or "Thing"


def pyg_tables(nodes, edges):
    """Takes the collected nodes and edges. Returns {file name: csv text}: one node table per node type (local
    BioLink class name; index = row number used in the edge tables) and one edge table per (subject type,
    predicate, object type)."""
    ntype = {n: _safe(local(d.get("category", "")) or "Thing") for n, d in nodes.items()}
    index, files = {}, {}
    for nt in sorted(set(ntype.values())):
        ids = sorted(n for n, t in ntype.items() if t == nt)
        index.update({n: i for i, n in enumerate(ids)})
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["index", "id", "name"])
        for i, n in enumerate(ids):
            w.writerow([i, n, nodes[n].get("name") or nodes[n].get("symbol") or ""])
        files[f"pyg/nodes_{nt}.csv"] = buf.getvalue()
    by_type = {}
    for e in edges:
        key = (ntype[e["subject"]], _safe(local(e["predicate"])), ntype[e["object"]])
        by_type.setdefault(key, []).append(e)
    for (s, p, o), es in sorted(by_type.items()):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["src", "dst", "edge_id"])
        for e in es:
            w.writerow([index[e["subject"]], index[e["object"]], e["id"]])
        files[f"pyg/edges_{s}__{p}__{o}.csv"] = buf.getvalue()
    return files


def zip_bytes(nodes, edges, title):
    """Takes the collected nodes and edges and a one-line title (the question). Returns the zip file as bytes."""
    n_tsv, e_tsv = kgx_tables(nodes, edges)
    pyg = pyg_tables(nodes, edges)
    n_pruned = sum(e.get("verdict") == "prune" for e in edges)
    readme = (f"TrialMindKG subgraph: {title}\n"
              f"{len(nodes)} nodes, {len(edges)} edges ({n_pruned} of them pruned: kept here, marked verdict=prune).\n\n"
              "kgx/nodes.tsv, kgx/edges.tsv  KGX tables (BioLink classes and predicates, provenance on every edge)\n"
              "pyg/nodes_<Type>.csv          node index per type (index, id, name)\n"
              "pyg/edges_<S>__<p>__<O>.csv   edge index per edge type (src, dst, edge_id)\n"
              "pyg/load_pyg.py               builds a torch_geometric HeteroData from the pyg/ tables\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme)
        z.writestr("kgx/nodes.tsv", n_tsv)
        z.writestr("kgx/edges.tsv", e_tsv)
        for name, text in pyg.items():
            z.writestr(name, text)
        z.writestr("pyg/load_pyg.py", LOAD_PYG)
    return buf.getvalue()
