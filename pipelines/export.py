"""
Export a validated KG to every target format, plus a run manifest.

export_all(kg, report, versions, snap, out_dir) writes into out_dir and returns the manifest dict:
  kg_nodes.csv, kg_edges.csv         generic tables (edge `id` is stable across runs)
  seed_kg.graphml                    NetworkX / Gephi / Cytoscape
  neptune/gremlin/{nodes,edges}.csv  Amazon Neptune bulk loader, format=csv  (Gremlin)
  neptune/opencypher/{nodes,edges}.csv  Amazon Neptune bulk loader, format=opencypher
  kgx/{nodes,edges}.tsv              KGX TSV (BioLink categories/predicates; tmkg term kept as original_predicate /
                                     tmkg_category) - the format Translator / BioLink tools read
  load_neo4j.cypher                  Neo4j Desktop loader generated from kg_schema (always complete)
  resolution_report.csv              review queue (rows whose rule ends in NEEDS_REVIEW need a curator)
  run_manifest.json                  source versions, snapshot fingerprint, counts, sha256 of every output

Same KG in -> byte-identical files out (rows and columns sorted, fixed line endings).
"""
import csv, hashlib, json, subprocess
from pathlib import Path
import networkx as nx

from kg_schema import PROFILES, local, biolink_term

EDGE_HEAD = ["id", "subject", "predicate", "object", "source", "source_record", "evidence_type", "retrieved"]


def _write_csv(path, header, rows, delimiter=","):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n", delimiter=delimiter)
        w.writerow(header)
        w.writerows(rows)


def _cell(v):
    return "" if v is None else ("true" if v is True else "false" if v is False else str(v))


def _neptune_type(values):
    """Neptune column type from the Python values present in a column."""
    vals = [v for v in values if v is not None]
    if vals and all(isinstance(v, bool) for v in vals):
        return "Bool"
    if vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return "Long"
    if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "Double"
    return "String"


def _neptune_cell(v):
    # Neptune bulk loader: keep each record on one line
    return _cell(v).replace("\r\n", "\\n").replace("\n", "\\n")


def write_tables(kg, out):
    nodes, edges = kg.sorted_nodes(), kg.sorted_edges()
    nkeys = sorted({k for n in nodes for k in n} - {"id", "category"})
    ekeys = sorted({k for e in edges for k in e} - set(EDGE_HEAD))
    _write_csv(out / "kg_nodes.csv", ["id", "category"] + nkeys,
               [[_cell(n.get(k)) for k in ["id", "category"] + nkeys] for n in nodes])
    _write_csv(out / "kg_edges.csv", EDGE_HEAD + ekeys, [[_cell(e.get(k)) for k in EDGE_HEAD + ekeys] for e in edges])
    return nodes, edges, nkeys, ekeys


def labels(category):
    """Node labels for Neptune / Neo4j: own label, plus the BioLink parent when it differs
    ('tmkg:OutcomeMeasure' -> ['OutcomeMeasure', 'StudyResult'])."""
    parent = biolink_term(category)
    return [local(category)] + ([local(parent)] if parent != category else [])


# KGX TSV: these columns hold lists, written pipe-separated as KGX expects
KGX_LISTS = ("aggregator_knowledge_source", "supporting_data_source", "nct_ids", "derived_from",
             "clinical_trial_phases")


def _kgx_cell(k, v):
    if k in KGX_LISTS and isinstance(v, str) and v.startswith("["):
        v = "|".join(str(x) for x in json.loads(v))
    return _neptune_cell(v).replace("\t", " ")


def write_kgx(nodes, edges, out):
    """KGX TSV: category / predicate are BioLink terms; the graph's own tmkg term is kept in tmkg_category /
    original_predicate (BioLink slot); every provenance slot and property is carried over."""
    nkeys = sorted({k for n in nodes for k in n} - {"id", "category", "biolink_category"})
    _write_csv(out / "kgx" / "nodes.tsv", ["id", "category", "tmkg_category"] + nkeys,
               [[n["id"], biolink_term(n["category"]), n["category"] if n["category"].startswith("tmkg:") else ""] +
                [_kgx_cell(k, n.get(k)) for k in nkeys] for n in nodes], delimiter="\t")
    first = ["id", "subject", "predicate", "object", "original_predicate", "primary_knowledge_source",
             "aggregator_knowledge_source", "supporting_data_source", "knowledge_level", "agent_type",
             "mapping_confidence"]
    ekeys = sorted({k for e in edges for k in e} - set(first) - {"biolink_predicate"})
    _write_csv(out / "kgx" / "edges.tsv", first + ekeys,
               [[e["id"], e["subject"], biolink_term(e["predicate"]), e["object"]] +
                [_kgx_cell(k, e.get(k)) for k in first[4:] + ekeys] for e in edges], delimiter="\t")


def write_neptune(nodes, edges, nkeys, ekeys, out):
    ntypes = {k: _neptune_type([n.get(k) for n in nodes]) for k in ["category"] + nkeys}
    etypes = {k: _neptune_type([e.get(k) for e in edges]) for k in EDGE_HEAD[1:] + ekeys if k not in ("subject", "object")}
    nprops, eprops = ["category"] + nkeys, [k for k in etypes]
    # openCypher: ':ID' becomes Neptune's internal id (id(n)), so 'id' is also written as a property -> n.id / r.id
    # work the same on Neptune, Neo4j and in querier.run_local
    for fmt, nh, eh, idp in (("gremlin", ["~id", "~label"], ["~id", "~from", "~to", "~label"], []),
                             ("opencypher", [":ID", ":LABEL"], [":ID", ":START_ID", ":END_ID", ":TYPE"], ["id:String"])):
        x = len(idp)
        _write_csv(out / "neptune" / fmt / "nodes.csv", nh + idp + [f"{k}:{ntypes[k]}" for k in nprops],
                   [[n["id"], ";".join(labels(n["category"]))] + [n["id"]] * x + [_neptune_cell(n.get(k)) for k in nprops]
                    for n in nodes])
        _write_csv(out / "neptune" / fmt / "edges.csv", eh + idp + [f"{k}:{etypes[k]}" for k in eprops],
                   [[e["id"], e["subject"], e["object"], local(e["predicate"])] + [e["id"]] * x +
                    [_neptune_cell(e.get(k)) for k in eprops] for e in edges])


def neo4j_script(profile="seed"):
    CATEGORIES, PREDICATES = PROFILES[profile]
    lines = ["// TrialMindKG - Neo4j Desktop import. GENERATED by pipelines/export.py from kg_schema.py; do not edit.",
             "// Copy outputs/kg_nodes.csv + kg_edges.csv into the DBMS 'import' folder, then run this script.",
             "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE;", ""]
    for c in sorted(CATEGORIES):
        lines += [f"LOAD CSV WITH HEADERS FROM 'file:///kg_nodes.csv' AS row WITH row WHERE row.category = '{c}'",
                  f"MERGE (n:Entity {{id: row.id}}) SET n:{':'.join(labels(c))}, n += row;"]
    lines.append("")
    for p in sorted(PREDICATES):
        lines += [f"LOAD CSV WITH HEADERS FROM 'file:///kg_edges.csv' AS row WITH row WHERE row.predicate = '{p}'",
                  "MATCH (a:Entity {id: row.subject}), (b:Entity {id: row.object})",
                  f"MERGE (a)-[r:{local(p).upper()} {{id: row.id}}]->(b) SET r += row;"]
    if profile != "seed":
        return "\n".join(lines + [""])
    lines += ["", "// ---- Example questions ----",
              "// Which failed programs share a drug target?",
              "MATCH (p1:ClinicalTrialProgram)-[:HAS_INTERVENTION]->(:Drug)-[:INTERACTS_WITH]->(t)"
              "<-[:INTERACTS_WITH]-(:Drug)<-[:HAS_INTERVENTION]-(p2:ClinicalTrialProgram)",
              "WHERE p1.id < p2.id RETURN t.name AS target, collect(DISTINCT p1.name + '  |  ' + p2.name) AS program_pairs;",
              "// Primary outcomes of TULIP-1 with their statistical analyses",
              "MATCH (:ClinicalTrial {id:'NCT:NCT02446912'})-[:HAS_OUTCOME_MEASURE]->(o) WHERE o.kind = 'PRIMARY' "
              "RETURN o.name, o.analyses;", ""]
    return "\n".join(lines)


def write_graphml(nodes, edges, out):
    """Takes node dicts, edge dicts and an output folder; writes seed_kg.graphml. Always uses networkx's pure-Python
    (ElementTree) writer: nx.write_graphml silently switches to lxml when that library happens to be installed, and
    lxml indents the XML differently, so the same graph would hash differently from machine to machine."""
    G = nx.MultiDiGraph()
    for n in nodes:
        G.add_node(n["id"], **{k: _cell(v) for k, v in n.items() if k != "id"})
    for e in edges:
        G.add_edge(e["subject"], e["object"], key=e["id"],
                   **{k: _cell(v) for k, v in e.items() if k not in ("subject", "object")})
    nx.readwrite.graphml.write_graphml_xml(G, out / "seed_kg.graphml")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot_fingerprint(snap):
    """sha256 over the stored responses this run used -> identifies the exact data version."""
    h = hashlib.sha256()
    for src, key in sorted(snap.used):
        h.update(f"{src}/{key}:{sha256(snap.path(src, key))}\n".encode())
    return h.hexdigest()


def environment():
    """Python and package versions used for this build (outputs can differ across versions)."""
    import platform
    from importlib.metadata import version, PackageNotFoundError
    pk = {}
    for name in ("pandas", "networkx", "numpy", "requests", "PyYAML"):
        try:
            pk[name] = version(name)
        except PackageNotFoundError:
            pk[name] = None
    return {"python": platform.python_version(), "packages": pk}


def git_state(root):
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "pipelines", "data/curated"], cwd=root,
                                    capture_output=True, text=True).stdout.strip())
        return {"git_commit": commit or None, "uncommitted_changes": dirty}
    except Exception:
        return {"git_commit": None, "uncommitted_changes": None}


def export_all(kg, report, versions, snap, out, profile="seed"):
    out = Path(out)
    nodes, edges, nkeys, ekeys = write_tables(kg, out)
    write_neptune(nodes, edges, nkeys, ekeys, out)
    write_graphml(nodes, edges, out)
    write_kgx(nodes, edges, out)
    (out / "load_neo4j.cypher").write_text(neo4j_script(profile))
    report.to_csv(out / "resolution_report.csv", index=False, lineterminator="\n")
    files = ["kg_nodes.csv", "kg_edges.csv", "seed_kg.graphml", "resolution_report.csv", "load_neo4j.cypher",
             "neptune/gremlin/nodes.csv", "neptune/gremlin/edges.csv",
             "neptune/opencypher/nodes.csv", "neptune/opencypher/edges.csv", "kgx/nodes.tsv", "kgx/edges.tsv"]
    count = lambda key, rows: dict(sorted({r[key]: sum(1 for x in rows if x[key] == r[key]) for r in rows}.items()))
    manifest = {
        "counts": {"nodes": len(nodes), "edges": len(edges), "by_category": count("category", nodes),
                   "by_predicate": count("predicate", edges),
                   "needs_review": int(report["rule"].fillna("").str.contains("NEEDS_REVIEW").sum())},
        "sources": versions,
        "snapshot": {"directory": "data/snapshots", "responses_used": len(snap.used),
                     "fingerprint": snapshot_fingerprint(snap)},
        "outputs_sha256": {f: sha256(out / f) for f in files},
        "code": {**git_state(Path(__file__).resolve().parents[1]), **environment()},
        "snapshot_mode": snap.mode,
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
