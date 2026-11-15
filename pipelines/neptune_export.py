"""
The WHOLE assembled graph as Amazon Neptune openCypher bulk-load CSVs, written by streaming (packet CC-17; owner decision
2026-11-05: the export holds everything, not the kinase family of CC-9, which stays available in neptune_subgraph.py).

DELIVERABLE, NOT A SERVICE (decision of 2026-11-05 21:00): no Neptune cluster runs on our AWS account (Neptune Database cannot be
created there), so these files are shipped as the graph in a load-ready form together with <out>/README.md, a plain note on what
the files are, why Neptune is not hosted, and how anyone with a Neptune Database (or any openCypher loader) can load and check
them. The app serves the same graph from its local card index. aws/neptune_load.sh and aws/neptune_create.sh stay in the kit for
whoever has a cluster; nothing here requires one.

    python pipelines/neptune_export.py [G4] [--out outputs/neptune/full] [--chunk 2000000] [--rows-per-file 1000000]

Recipe: make_graphs.RECIPES[graph] (default G4 = Pharos + Open Targets + ChEMBL 37 + HPO / GO / MONDO hierarchies + the trial
layer, verdict != prune). Layers that are not built are skipped and listed in the manifest.

Memory: node tables are read whole (1.3 million rows, a few narrow columns); EDGES ARE NEVER HELD. Each layer's edge file is read
in chunks (default 2,000,000 rows). Edge ids already written (the same id in an earlier layer or chunk wins, as in
make_graphs.combine) are remembered as 64-bit hashes in one sorted numpy array (8 bytes per edge, 0.25 GB for 30 million);
each layer's verdicts are kept as sorted (hash, verdict, confidence) arrays (13 bytes per edge). A 64-bit hash collision would
drop one edge in about 10^7 builds at this size; the manifest records the number of ids seen so a count check catches it.

Nodes:  one row per id; each FIELD takes the first non-empty value over the layers (pharos first), as pharos_app.merge_node_frames
        (CC-15); one class per id prefix (make_graphs.harmonise_categories); ChEMBL compounds are named from
        data/reference/chembl_synonyms.tsv.gz (pharos_app.chembl_names). Label = the class without its prefix (the trial
        layer's own tmkg: class when it has one). Trial-layer ids are renamed as make_graphs.rename_ids does for G4
        (CHEMBL: -> CHEMBL.COMPOUND:, ENSEMBL gene -> NCBIGene through HGNC), so one database answers Pharos and trial questions;
        a `same_as` edge is written only if the original id is ALSO a node of another layer (expected 0; counted).
Fixes:  the export fixes of export_graph_kgx.py (rules in the source cards' `export_overrides`) are applied as in the KGX tables and
        the card index: corrected identifiers (before the layers are merged) and classes on the nodes, corrected ends and
        knowledge_level / agent_type on the edges. The node files have no `type` column, so the gene types are not written here.
        What each rule touched is in the manifest (`export_overrides`).
Edges:  every edge whose verdict is not `prune` (an empty verdict = a layer that was never pruned = kept) whose two ends are nodes
        (the others are counted in the manifest; expect 0). :TYPE = the predicate without its prefix (the trial layer's own
        tmkg: predicate when it has one, the BioLink one is kept as biolink_predicate).
Files:  <out>/opencypher/nodes_000.csv.gz ... edges_000.csv.gz, at most --rows-per-file rows each, gzip, byte-deterministic.
        Neptune Database accepts gzip (or bzip2) compressed CSV for the bulk loader when each archive holds ONE text file with a
        .gz extension (docs.aws.amazon.com/neptune/latest/userguide/bulk-load-tutorial-format.html); Neptune Analytics does not.
        The system columns are the openCypher ones (:ID, :LABEL, :START_ID, :END_ID, :TYPE), format=opencypher, as in CC-9.
README:   <out>/README.md, written from the manifest (every number in it is read from it; the reason text is NOT_HOSTED_REASON).
Manifest: <out>/manifest.json (NOT inside the CSV folder: the loader reads every file under its prefix) with per-file rows and
        sha256, counts {nodes, edges} (the keys aws/neptune_load.sh compares with the cluster), totals per label and per type,
        dropped counts, inputs with size and mtime, and parity questions chosen by rule from the data (not kinase specific)
        with the digest of their evidence edge ids, computed on the 2-hop neighbourhood of the questions' anchors.

  merged_nodes(root, layers)                -> (nodes frame, info)         the node table as written
  kept_edge_chunks(root, layers, node_ids, chunk, info) -> generator of edge frames (output columns), counts in info
  SplitWriter(out_dir, prefix, header, cap) -> writer of numbered .csv.gz parts
  export(root, out, graph, chunk, rows_per_file, parity) -> manifest dict
  validate_export(opencypher_dir)           -> [problems]    streaming validator (header, duplicates, ends, numbers)
  parity_questions(...)                     -> [{id, question, cypher}]
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_graphs as mg  # noqa: E402
import pharos_app  # noqa: E402
import export_graph_kgx as xk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NODE_IN = ["id", "category", "tmkg_category", "name", "symbol", "provided_by", "idg_family", "tdl"]
NODE_PROPS = ["category", "name", "symbol", "provided_by", "idg_family", "tdl"]
EDGE_IN = ["id", "subject", "predicate", "object", "original_predicate", "source", "primary_knowledge_source",
           "knowledge_level", "agent_type", "source_version"]
EDGE_PROPS = ["predicate", "biolink_predicate", "source", "primary_knowledge_source", "knowledge_level", "agent_type",
              "layer", "source_version", "verdict", "confidence"]
NODE_HEADER = [":ID", ":LABEL", "id:String"] + [f"{c}:String" for c in NODE_PROPS]
EDGE_HEADER = [":ID", ":START_ID", ":END_ID", ":TYPE", "id:String"] + [f"{c}:{'Double' if c == 'confidence' else 'String'}"
                                                                      for c in EDGE_PROPS]
OUT_DIR = Path("outputs") / "neptune" / "full"
# The one sentence on why the files are not loaded anywhere; edit it here if the situation changes (Claude Science / the owner).
NOT_HOSTED_REASON = ("The AWS account used for this project was created with AWS's 'Sign up for AWS (new)' option. Its AWS-managed "
                     "organization policy does not include Amazon Neptune, and AWS Support confirmed that Neptune is out of scope for this "
                     "account type, so no graph database is hosted. The hosted app answers from the same graph through its local card "
                     "index; these files load the pruned graph with the trial layer (G4) into any Amazon Neptune Database cluster.")


def _local(series):
    return series.fillna("").str.partition(":")[2].where(series.fillna("").str.contains(":"), series.fillna(""))


def _flat(series):
    """One line per record for the bulk loader: newlines written as \\n."""
    return series.fillna("").astype(str).str.replace("\r\n", "\\n", regex=False).str.replace("\n", "\\n", regex=False)


def _hash(ids):
    return pd.util.hash_array(np.asarray(ids, dtype=object))


# ------------------------------------------------------------------------------------------------ layers and nodes
def layer_files(root, layers):
    """Takes the repo root and layer names. Returns ([(layer, nodes path, edges path, verdicts path or None)], [missing])."""
    used, missing = [], []
    for layer in layers:
        d = mg.layer_dir(layer, root)
        ext = ".tsv" if layer == mg.TRIAL_LAYER else ".tsv.gz"
        if d is None or not (d / f"nodes{ext}").exists() or not (d / f"edges{ext}").exists():
            missing.append(layer)
            continue
        vp = d / "edge_verdicts.tsv.gz"
        used.append((layer, d / f"nodes{ext}", d / f"edges{ext}", vp if vp.exists() else None))
    return used, missing


def _read_nodes(path, layer, root):
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, usecols=lambda c: c in NODE_IN)
    for c in NODE_IN:
        if c not in df:
            df[c] = ""
    df["category"] = df["category"].str.split("|").str[0]
    df["layer"] = layer
    return df[NODE_IN + ["layer"]]


def merged_nodes(root, layers):
    """Takes the repo root and the layers to use (RECIPES[graph]['layers']). Returns (nodes, info): one row per node id with
    the columns NODE_IN + label, merged per field in layer order, one class per id prefix, ChEMBL compounds named;
    info = {used, missing, inputs [paths], retyped, named_from_chembl, trial_renamed, same_as [(original, renamed)],
    trial_ids {original id: renamed id}, overrides (the export rules), renames {id: corrected id}, rename_counts,
    node_fixes (fix_nodes' counts)}. The export fixes rename ids before the merge and correct classes after it."""
    root = Path(root)
    used, missing = layer_files(root, layers)
    frames, info, inputs = [], dict(used=[u[0] for u in used], missing=missing), []
    trial_map, same_as = {}, []
    for layer, npath, epath, vpath in used:
        df = _read_nodes(npath, layer, root)
        inputs += [npath, epath] + ([vpath] if vpath else [])
        if layer == mg.TRIAL_LAYER:
            mapping = mg.trial_id_map(root)
            renamed, _, n = mg.rename_ids(df, pd.DataFrame({"subject": [], "object": []}), mapping)
            trial_map = {o: n_ for o, n_ in zip(df["id"], df["id"].map(
                lambda v: mapping.get(v) or ("CHEMBL.COMPOUND:" + v[len("CHEMBL:"):] if v.startswith("CHEMBL:") else v)))
                         if o != n_}
            info["trial_renamed"] = n
            df = df.assign(id=df["id"].map(lambda v: trial_map.get(v, v)))
        frames.append(df)
    info["inputs"], info["trial_ids"] = inputs, trial_map
    if not frames:
        raise FileNotFoundError("no built layer to export")
    ov = xk.load_overrides(root, [u[0] for u in used])
    problems = xk.check_overrides(ov, root)
    if problems:
        raise ValueError("export rules name a term that is not in its reference file: " + "; ".join(problems))
    renames, rename_classes, info["rename_counts"] = xk.id_renames(pd.concat([f["id"] for f in frames]), ov, root)
    info["overrides"], info["renames"] = ov, renames
    allf = pd.concat([xk.rename_node_ids(f, renames, rename_classes) for f in frames], ignore_index=True)
    for orig, new in trial_map.items():                      # the original id is also a node of ANOTHER layer: keep both, link them
        other = allf[(allf["layer"] != mg.TRIAL_LAYER) & (allf["id"] == orig)]
        if len(other):
            same_as.append((orig, new))
    allf, info["retyped"] = mg.harmonise_categories(allf)
    nodes = pharos_app.merge_node_frames([allf.drop(columns="layer")]).reset_index(drop=True)
    chembl = pharos_app.chembl_names(root)
    nodes, info["node_fixes"] = xk.fix_nodes(nodes, ov, chembl=chembl)   # classes, names (no `type` column here: no gene types)
    if chembl["path"]:
        info["inputs"].append(root / chembl["path"])
    recs = {r["id"]: r for r in nodes.to_dict("records")}
    pharos_app.name_compounds(recs, chembl)
    nodes["name"] = nodes["id"].map(lambda i: recs[i].get("name", ""))
    info["named_from_chembl"] = sum(1 for n in chembl["names"] if n in recs)
    cls = nodes["tmkg_category"].where(nodes["tmkg_category"] != "", nodes["category"])
    nodes["label"] = _local(cls)
    info["same_as"] = same_as
    return nodes.sort_values("id", kind="mergesort", ignore_index=True), info


# ------------------------------------------------------------------------------------------------ edges (streamed)
class _Verdicts:
    """One layer's verdicts as sorted arrays: hash of the edge id, verdict code (0 keep, 1 prune), confidence."""

    def __init__(self, path, chunk):
        hs, vs, cs = [], [], []
        if path is not None:
            for c in pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, usecols=["id", "verdict", "confidence"],
                                 chunksize=chunk):
                hs.append(_hash(c["id"]))
                vs.append((c["verdict"] == "prune").astype(np.int8).to_numpy() + np.where(c["verdict"].isin(["keep", "prune"]), 0, 2))
                cs.append(pd.to_numeric(c["confidence"], errors="coerce").to_numpy(dtype=np.float64))
        if hs:
            h, v, c = np.concatenate(hs), np.concatenate(vs), np.concatenate(cs)
            o = np.argsort(h, kind="stable")
            self.h, self.v, self.c = h[o], v[o], c[o]
        else:
            self.h, self.v, self.c = np.zeros(0, np.uint64), np.zeros(0, np.int8), np.zeros(0)

    def lookup(self, ids):
        """-> (verdict text array: 'keep' | 'prune' | '', confidence array) for edge ids."""
        h = _hash(ids)
        pos = np.searchsorted(self.h, h)
        pos[pos >= len(self.h)] = 0
        hit = (len(self.h) > 0) & (self.h[pos] == h) if len(self.h) else np.zeros(len(h), bool)
        code = np.where(hit, self.v[pos] if len(self.h) else 0, 9)
        verdict = np.select([code == 0, code == 1], ["keep", "prune"], default="")
        return verdict, np.where(hit, self.c[pos] if len(self.h) else np.nan, np.nan)


def kept_edge_chunks(root, layers, node_ids, chunk=2_000_000, info=None, renames=None):
    """Takes the repo root, the layers, the set of node ids, the chunk size and merged_nodes' renames (corrected ids). Yields
    DataFrames with the columns id, subject, predicate (the :TYPE), object, + EDGE_PROPS, one per chunk of an edge file, holding
    only edges to write, with the export fixes applied (corrected ends and labels). info (a dict, filled in): edges_read,
    duplicate_ids, pruned, no_node, kept, same_as_written, ids_seen, edge_fixes {layer: {rule: rows}}."""
    info = info if info is not None else {}
    ov = xk.load_overrides(root, layers)
    info["edge_fixes"] = {}
    for k in ("edges_read", "duplicate_ids", "pruned", "no_node", "kept", "pruned_by_layer", "kept_by_layer"):
        info[k] = {} if k.endswith("_by_layer") else 0
    used, _ = layer_files(root, layers)
    trial_map = mg.trial_id_map(root) if any(u[0] == mg.TRIAL_LAYER for u in used) else {}
    seen = np.zeros(0, np.uint64)
    ids = node_ids
    for layer, _, epath, vpath in used:
        verdicts = _Verdicts(vpath, chunk)
        for c in pd.read_csv(epath, sep="\t", dtype=str, keep_default_na=False, chunksize=chunk,
                             usecols=lambda col: col in EDGE_IN):
            for col in EDGE_IN:
                if col not in c:
                    c[col] = ""
            info["edges_read"] += len(c)
            if layer == mg.TRIAL_LAYER:
                _, c, _ = mg.rename_ids(pd.DataFrame({"id": []}), c, trial_map)
            c = xk.fix_edges(c, layer, ov, renames, info["edge_fixes"].setdefault(layer, {}), labels=False)   # ends first
            h = _hash(c["id"])
            first = ~pd.Series(h).duplicated().to_numpy()
            fresh = first & ~np.isin(h, seen)
            info["duplicate_ids"] += int((~fresh).sum())
            seen = np.sort(np.concatenate([seen, h[fresh]]))
            c, h = c[fresh], h[fresh]
            verdict, conf = verdicts.lookup(c["id"])
            keep = verdict != "prune"
            info["pruned"] += int((~keep).sum())
            info["pruned_by_layer"][layer] = info["pruned_by_layer"].get(layer, 0) + int((~keep).sum())
            c = c.assign(verdict=verdict, confidence=conf)[keep]
            ends = c["subject"].isin(ids) & c["object"].isin(ids)
            info["no_node"] += int((~ends).sum())
            c = xk.fix_edges(c[ends].copy(), layer, ov, None, info["edge_fixes"][layer])   # labels on the rows written
            ptype = c["original_predicate"].where(c["original_predicate"] != "", c["predicate"])
            out = pd.DataFrame({"id": c["id"], "subject": c["subject"], "object": c["object"], "predicate": ptype, "biolink_predicate": c["predicate"], "source": c["source"],
                "primary_knowledge_source": c["primary_knowledge_source"], "knowledge_level": c["knowledge_level"],
                "agent_type": c["agent_type"], "layer": layer, "source_version": c["source_version"],
                "verdict": c["verdict"], "confidence": c["confidence"]})
            info["kept"] += len(out)
            info["kept_by_layer"][layer] = info["kept_by_layer"].get(layer, 0) + len(out)
            yield out
    info["ids_seen"] = int(len(seen))


# ------------------------------------------------------------------------------------------------ writing
class SplitWriter:
    """Writes rows to <dir>/<prefix>_000.csv.gz, _001 ... starting a new file after `cap` rows. Each file has the header;
    gzip with mtime 0 and no stored name, so equal rows give equal bytes. Counts rows per file."""

    def __init__(self, out_dir, prefix, header, cap=1_000_000):
        self.dir, self.prefix, self.header, self.cap = Path(out_dir), prefix, header, cap
        self.dir.mkdir(parents=True, exist_ok=True)
        self.files, self._fh, self._n = [], None, 0

    def _open(self):
        self.close()
        path = self.dir / f"{self.prefix}_{len(self.files):03d}.csv.gz"
        raw = open(path, "wb")
        self._fh = (gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0), raw)
        self._fh[0].write((",".join(self.header) + "\n").encode())
        self.files.append(dict(file=path.name, rows=0))
        self._n = 0

    def write(self, df):
        """df: a frame whose columns are in header order. Splits at the cap."""
        i = 0
        while i < len(df):
            if self._fh is None or self._n >= self.cap:
                self._open()
            part = df.iloc[i:i + self.cap - self._n]
            buf = io.StringIO()
            part.to_csv(buf, header=False, index=False, lineterminator="\n")
            self._fh[0].write(buf.getvalue().encode())
            self._n += len(part)
            self.files[-1]["rows"] += len(part)
            i += len(part)

    def close(self):
        if self._fh is not None:
            self._fh[0].close()
            self._fh[1].close()
            self._fh = None

    def finish(self):
        """Closes the last file; returns [{file, rows, sha256}]."""
        self.close()
        for f in self.files:
            f["sha256"] = hashlib.sha256((self.dir / f["file"]).read_bytes()).hexdigest()
        return self.files


def _node_rows(nodes):
    cols = {":ID": nodes["id"], ":LABEL": nodes["label"], "id:String": nodes["id"]}
    for c in NODE_PROPS:
        cols[f"{c}:String"] = _flat(nodes[c])
    return pd.DataFrame(cols)


def _edge_rows(e):
    cols = {":ID": e["id"], ":START_ID": e["subject"], ":END_ID": e["object"], ":TYPE": _local(e["predicate"]),
            "id:String": e["id"]}
    for c in EDGE_PROPS:
        cols[f"{c}:{'Double' if c == 'confidence' else 'String'}"] = (
            e[c] if c == "confidence" else _flat(e[c]))
    return pd.DataFrame(cols)


# ------------------------------------------------------------------------------------------------ validation
def _rows(path):
    csv.field_size_limit(sys.maxsize)
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        yield from csv.reader(f)


def validate_export(oc_dir, max_rows=1_000_000):
    """Takes the opencypher folder. Streams every node and edge file. Returns the problems (empty = loadable): header,
    cells per row = header cells, empty or duplicate :ID, empty :LABEL / :TYPE, an edge end that is not a node, a confidence
    that is not a number, a file over max_rows."""
    oc_dir, problems = Path(oc_dir), []
    nfiles, efiles = sorted(oc_dir.glob("nodes_*.csv.gz")), sorted(oc_dir.glob("edges_*.csv.gz"))
    if not nfiles or not efiles:
        return ["no nodes_*.csv.gz or edges_*.csv.gz in " + str(oc_dir)]
    ids, dup = set(), 0
    for p in nfiles:
        it = _rows(p)
        head = next(it)
        if head != NODE_HEADER:
            problems.append(f"{p.name}: header {head[:4]}... is not the node header")
            continue
        n = 0
        for r in it:
            n += 1
            if len(r) != len(head):
                problems.append(f"{p.name} row {n + 1}: {len(r)} cells, header has {len(head)}")
            elif not r[0] or not r[1]:
                problems.append(f"{p.name} row {n + 1}: empty :ID or :LABEL")
            else:
                dup += r[0] in ids
                ids.add(r[0])
        if n > max_rows:
            problems.append(f"{p.name}: {n} rows, over {max_rows}")
    if dup:
        problems.append(f"nodes: {dup:,} duplicate :ID")
    hashes, conf_i = [], EDGE_HEADER.index("confidence:Double")
    for p in efiles:
        it = _rows(p)
        head = next(it)
        if head != EDGE_HEADER:
            problems.append(f"{p.name}: header {head[:5]}... is not the edge header")
            continue
        eids, n = [], 0
        for r in it:
            n += 1
            if len(r) != len(head):
                problems.append(f"{p.name} row {n + 1}: {len(r)} cells, header has {len(head)}")
                continue
            if not r[0] or not r[3]:
                problems.append(f"{p.name} row {n + 1}: empty :ID or :TYPE")
            if r[1] not in ids or r[2] not in ids:
                problems.append(f"{p.name} row {n + 1}: an end of {r[0]} is not a node")
            if r[conf_i]:
                try:
                    float(r[conf_i])
                except ValueError:
                    problems.append(f"{p.name} row {n + 1}: confidence {r[conf_i]!r} is not a number")
            eids.append(r[0])
        hashes.append(_hash(eids))
        if n > max_rows:
            problems.append(f"{p.name}: {n} rows, over {max_rows}")
    allh = np.concatenate(hashes) if hashes else np.zeros(0, np.uint64)
    if len(np.unique(allh)) != len(allh):
        problems.append(f"edges: {len(allh) - len(np.unique(allh)):,} duplicate :ID")
    return problems[:50]


# ------------------------------------------------------------------------------------------------ parity questions
def _arrow(direction, rtype):
    return f"-[r:{rtype}]->" if direction == "out" else f"<-[r:{rtype}]-"


def parity_questions(trial_drugs, incident, labels, n_drugs=3, n_targets=3):
    """Takes the trial drug ids (renamed), {node id: [kept edge dicts touching it]} for the drugs and their targets, and the
    label of every node in those edges. Chooses by rule: the first n_drugs drugs (sorted) that have an edge to a Gene or Protein
    outside the trial layer; for each, the most frequent (type, direction, class) to a Gene / Protein = question 'targets of
    this drug'; the first n_targets distinct such targets and, for each, the most frequent (type, direction) to a Disease =
    'diseases of this target'; and for the first drug a 2-hop question drug -> target -> (the most frequent other class).
    Nothing is typed by hand about the data. Returns [{id, question, cypher, anchors}]."""
    def best(node, classes, skip=()):
        count = {}
        for e in incident.get(node, []):
            if e["layer"] == mg.TRIAL_LAYER:
                continue
            out = e["subject"] == node
            other = e["object"] if out else e["subject"]
            k = (e["predicate"].partition(":")[2] or e["predicate"], "out" if out else "in", labels.get(other, ""))
            if k[2] in classes and other not in skip:
                count[k] = count.get(k, 0) + 1
        return max(sorted(count), key=lambda k: count[k]) if count else None

    out, drugs = [], []
    for d in sorted(trial_drugs):
        b = best(d, {"Gene", "Protein"})
        if b:
            drugs.append((d, b))
        if len(drugs) == n_drugs:
            break
    for i, (d, (t, direc, cls)) in enumerate(drugs, 1):
        out.append(dict(id=f"F{i:02d}", anchors=[d], question=f"Which {cls} nodes does the trial drug {d} connect to by {t}?",
                        cypher=f"MATCH (d:{labels[d]} {{id:'{d}'}}){_arrow(direc, t)}(x:{cls}) RETURN d.id AS drug, x.id AS target, "
                               f"r.id AS evidence ORDER BY drug, target, evidence LIMIT 500"))
    targets = []
    for d, (t, direc, cls) in drugs:
        for e in incident.get(d, []):
            other = e["object"] if e["subject"] == d else e["subject"]
            if e["layer"] != mg.TRIAL_LAYER and labels.get(other) == cls and other not in targets:
                targets.append(other)
    n = 0
    for tg in sorted(targets):
        b = best(tg, {"Disease"})
        if not b or n >= n_targets:
            continue
        n += 1
        t, direc, cls = b
        out.append(dict(id=f"F{len(out) + 1:02d}", anchors=[tg], question=f"Which Disease nodes is {tg} linked to by {t}?",
                        cypher=f"MATCH (g:{labels[tg]} {{id:'{tg}'}}){_arrow(direc, t)}(x:Disease) RETURN g.id AS target, x.id AS disease, "
                               f"r.id AS evidence ORDER BY target, disease, evidence LIMIT 500"))
    if drugs:
        d, (t1, d1, c1) = drugs[0]
        mids = sorted({(e["object"] if e["subject"] == d else e["subject"]) for e in incident.get(d, [])
                       if e["layer"] != mg.TRIAL_LAYER and labels.get(e["object"] if e["subject"] == d else e["subject"]) == c1})
        second = None
        for m in mids:
            second = best(m, set(labels.values()) - {"SmallMolecule", c1, "Disease"}, skip={d})
            if second:
                break
        if second:
            t2, d2, c2 = second
            out.append(dict(id=f"F{len(out) + 1:02d}", anchors=[d], question=f"Two hops from the trial drug {d}: drug, {c1}, {c2}.",
                            cypher=f"MATCH (d:{labels[d]} {{id:'{d}'}}){_arrow(d1, t1).replace('r:', 'r1:')}(m:{c1})"
                                   f"{_arrow(d2, t2).replace('r:', 'r2:')}(x:{c2}) RETURN d.id AS drug, m.id AS via, x.id AS other, "
                                   f"r1.id AS e1, r2.id AS e2 ORDER BY drug, via, other, e1, e2 LIMIT 500"))
    return out


class _Collector:
    """Collects the kept edges that touch a set of seed ids while the edges stream by."""

    def __init__(self, seeds):
        self.seeds, self.rows = set(seeds), {}

    def add(self, e):
        hit = e[e["subject"].isin(self.seeds) | e["object"].isin(self.seeds)]
        for r in hit.to_dict("records"):
            self.rows.setdefault(r["id"], r)

    def incident(self):
        inc = {}
        for r in self.rows.values():
            for end in (r["subject"], r["object"]):
                if end in self.seeds:
                    inc.setdefault(end, []).append(r)
        return inc


def _digest(result):
    import neptune_subgraph as ns
    return ns.evidence_digest(result)


def _parity(root, layers, nodes, trial_drugs, first_pass_edges, chunk, renames=None):
    """Parity questions and their evidence digests. first_pass_edges = the _Collector filled during the export pass (edges of
    the trial drugs); a second streaming pass collects the edges of those drugs' targets so the 2-hop answers are complete."""
    import querier
    import neptune_subgraph as ns
    labels = dict(zip(nodes["id"], nodes["label"]))
    inc1 = first_pass_edges.incident()
    first = parity_questions(trial_drugs, inc1, labels)           # drugs + their targets (the targets' own edges come next)
    targets = {e for d in {a for q in first for a in q["anchors"]} for r in inc1.get(d, [])
               for e in (r["subject"], r["object"]) if e != d and labels.get(e) in ("Gene", "Protein")}
    col2 = _Collector(set(targets))
    for c in kept_edge_chunks(root, layers, set(nodes["id"]), chunk, renames=renames):
        col2.add(c)
    inc = {**col2.incident(), **inc1}
    qs = parity_questions(trial_drugs, inc, labels)
    rows = {**first_pass_edges.rows, **col2.rows}
    ids = {x for r in rows.values() for x in (r["subject"], r["object"])}
    sub_n = {i: dict(id=i, category=f"neptune:{labels[i]}") for i in ids}
    sub_e = {k: dict(id=k, subject=r["subject"], object=r["object"], predicate=f"neptune:{(r['predicate'].partition(':')[2] or r['predicate'])}")
             for k, r in rows.items()}
    idx = ns._Index(sub_n, sub_e)
    out = {}
    for q in qs:
        out[q["id"]] = dict(question=q["question"], cypher=q["cypher"], anchors=q["anchors"],
                            **_digest(querier.run_local(q["cypher"], idx)))
    return out


def export(root=ROOT, out=None, graph="G4", chunk=2_000_000, rows_per_file=1_000_000, parity=True, log=print):
    """Takes the repo root, the output folder (default outputs/neptune/full), the recipe name, the edge chunk size, the file row
    cap and whether to compute the parity questions (a second pass over the edges). Writes nodes_*.csv.gz and edges_*.csv.gz under
    <out>/opencypher/, <out>/manifest.json and <out>/README.md. Returns the manifest."""
    root = Path(root)
    out = Path(out) if out else root / OUT_DIR
    oc = out / "opencypher"
    for old in list(oc.glob("nodes_*.csv.gz")) + list(oc.glob("edges_*.csv.gz")):
        old.unlink()                                                # a previous export must not leave extra files in the load prefix
    layers = mg.RECIPES[graph]["layers"]
    nodes, ninfo = merged_nodes(root, layers)
    log(f"nodes: {len(nodes):,} ({', '.join(ninfo['used'])}; missing: {', '.join(ninfo['missing']) or 'none'})")
    nw = SplitWriter(oc, "nodes", NODE_HEADER, rows_per_file)
    for i in range(0, len(nodes), 200_000):
        nw.write(_node_rows(nodes.iloc[i:i + 200_000]))
    nfiles = nw.finish()
    trial_drugs = set(nodes.loc[(nodes["label"] == "SmallMolecule") & nodes["id"].isin(set(_trial_ids(root, layers))), "id"])
    ids = set(nodes["id"])
    ew, einfo, labels_n, types_n = SplitWriter(oc, "edges", EDGE_HEADER, rows_per_file), {}, {}, {}
    col = _Collector(trial_drugs) if parity else None
    for c in kept_edge_chunks(root, layers, ids, chunk, einfo, renames=ninfo["renames"]):
        ew.write(_edge_rows(c))
        for k, n in _local(c["predicate"]).value_counts().items():
            types_n[k] = types_n.get(k, 0) + int(n)
        if col is not None:
            col.add(c)
        log(f"  edges written: {sum(f['rows'] for f in ew.files):,}")
    same_as = [(o, n) for o, n in ninfo["same_as"]]
    if same_as:
        ew.write(_edge_rows(pd.DataFrame([dict(id=f"same_as:{o}", subject=o, object=n, predicate="biolink:same_as",
                                                 biolink_predicate="biolink:same_as", source="trial layer id mapping",
                                                 primary_knowledge_source="infores:trialmindkg", knowledge_level="knowledge_assertion",
                                                 agent_type="automated_agent", layer=mg.TRIAL_LAYER, source_version="",
                                                 verdict="", confidence=np.nan) for o, n in same_as])))
        types_n["same_as"] = types_n.get("same_as", 0) + len(same_as)
    efiles = ew.finish()
    for k, n in nodes["label"].value_counts().items():
        labels_n[k] = int(n)
    inputs = [dict(path=str(Path(p).relative_to(root)), bytes=Path(p).stat().st_size, mtime=Path(p).stat().st_mtime)
              for p in ninfo["inputs"]]
    edge_fixes = {k: dict(v) for k, v in einfo["edge_fixes"].items() if v}     # copied now: the parity pass streams the edges again
    parity_out = _parity(root, layers, nodes, trial_drugs, col, chunk, ninfo["renames"]) if col is not None else {}
    n_edges = sum(f["rows"] for f in efiles)
    man = dict(graph=graph, counts=dict(nodes=len(nodes), edges=n_edges),
               files=dict(nodes=nfiles, edges=efiles), rows_per_file=rows_per_file, chunk=chunk,
               totals=dict(labels=labels_n, types=dict(sorted(types_n.items()))),
               layers=dict(used=ninfo["used"], missing=ninfo["missing"]), named_from_chembl=ninfo["named_from_chembl"],
               dropped=dict(pruned=einfo["pruned"], pruned_by_layer=einfo["pruned_by_layer"], duplicate_edge_ids=einfo["duplicate_ids"],
                            edge_end_not_a_node=einfo["no_node"]),
               edges_read=einfo["edges_read"], edge_ids_seen=einfo["ids_seen"], kept_by_layer=einfo["kept_by_layer"],
               trial=dict(ids_renamed=len(ninfo["trial_ids"]), same_as_edges=len(same_as)),
               retyped_nodes=ninfo["retyped"], inputs=inputs, parity=parity_out, format="opencypher, gzip, one text file per archive",
               export_overrides=xk.overrides_report(f"neptune {graph}", ninfo["overrides"], ninfo["node_fixes"], edge_fixes,
                                                    ninfo["rename_counts"], totals=dict(nodes=len(nodes), edges=n_edges),
                                                    renames=ninfo["renames"]))
    (out / "manifest.json").write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")
    (out / "README.md").write_text(readme_markdown(man))
    log(f"manifest: {man['counts']}")
    return man


def readme_markdown(man):
    """Takes the manifest dict. Returns the text of <out>/README.md: what the files are, why Neptune is not hosted, how to load
    and check them, the counts, the files and the parity questions. Every number comes from the manifest; no date is written, so
    equal manifests give equal text."""
    c, t = man["counts"], man["totals"]
    nfiles, efiles = man["files"]["nodes"], man["files"]["edges"]
    top = lambda d, n=8: ", ".join(f"{k} {v:,}" for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n])   # noqa: E731
    d = man["dropped"]
    L = [f"# The pruned graph {man['graph']} as Neptune load files", "",
         f"{c['nodes']:,} nodes and {c['edges']:,} edges of the assembled graph {man['graph']} "
         f"({', '.join(man['layers']['used'])}), pruned edges left out, written as Amazon Neptune openCypher bulk-load CSV: "
         f"{len(nfiles)} node file(s) and {len(efiles)} edge file(s) of at most {man['rows_per_file']:,} rows each, gzip, "
         "in `opencypher/`.", "",
         "## Not loaded anywhere", "", NOT_HOSTED_REASON, "",
         "These files are the deliverable: they contain every node and edge the app serves, and the manifest lets you check the load.", "",
         "## What is in them", "",
         f"- Nodes by class: {top(t['labels'])}.", f"- Edges by type: {top(t['types'])}.",
         f"- Left out: {d['pruned']:,} edges with verdict `prune` (kept in the full graph, marked); {d['duplicate_edge_ids']:,} repeated "
         f"edge ids; {d['edge_end_not_a_node']:,} edges whose end is not a node.",
         f"- Layers used: {', '.join(man['layers']['used'])}; not built: {', '.join(man['layers']['missing']) or 'none'}.",
         f"- Every edge carries its source, knowledge level, agent type, layer and the pruning verdict and confidence. "
         f"{man.get('named_from_chembl', 0):,} ChEMBL compounds are named from the ChEMBL names file.",
         "- The relationship type (`:TYPE`) is the source's own relation name where the layer keeps one (`subClassOf` for the "
         "ontology hierarchies); the BioLink predicate of every edge is in the property `biolink_predicate` (`biolink:subclass_of` "
         "for those edges), the same term the KGX tables use.",
         *(["- Corrections made when the files were written (the layer files and the verdict file keep their original values):"]
           + ["  " + x for x in xk.fixes_markdown(man["export_overrides"])] if xk.fixes_markdown(man.get("export_overrides") or {}) else []),
         "",
         "## How to load them (Neptune Database)", "",
         "1. Copy the `opencypher/` folder to an S3 bucket in the cluster's region and give the cluster an IAM role that can read it.",
         "2. Start the bulk loader with `format` set to `opencypher` and the folder as `source`; Neptune Database reads gzip files "
         "when each archive holds one CSV (AWS: *Load data formats*). `failOnError` TRUE makes a bad row stop the load.",
         "3. When it finishes, check `MATCH (n) RETURN count(n)` against the node count and `MATCH ()-[r]->() RETURN count(r)` "
         "against the edge count in `manifest.json`; both must be equal.",
         "4. Run the questions below and compare the evidence digest.", "",
         "`aws/neptune_load.sh --full` does steps 2 and 3 and exits 1 on a count mismatch; it is only for someone who has a cluster.", "",
         "## Files", "", "| file | rows | sha256 |", "|---|---|---|"]
    L += [f"| {f['file']} | {f['rows']:,} | `{f['sha256'][:16]}...` |" for f in nfiles + efiles]
    if man.get("parity"):
        L += ["", "## Questions to check a load", "",
              "Each question is built from the data by rule (not typed by hand). `evidence_sha256` is the sha256 of the sorted edge ids "
              "(joined by newlines) the question must return; the number of rows and of distinct edge ids is listed too.", ""]
        for qid, q in sorted(man["parity"].items()):
            L += [f"**{qid}** {q['question']}", "", "```cypher", q["cypher"], "```",
                  f"{q['n_rows']:,} rows, {q['n_evidence']:,} edge ids, `{q['evidence_sha256']}`", ""]
    L += ["## Where they came from", "",
          "Built by `python pipelines/neptune_export.py` from the committed layer files; the manifest lists every input file with its "
          "size and modification time. The graph versions and the pruning rules are described in `docs/PIPELINE.md` and `docs/MAINTAIN.md`.", ""]
    return "\n".join(L)


def _trial_ids(root, layers):
    """Renamed ids of the trial layer's nodes (empty when it is not part of the recipe or not built)."""
    used, _ = layer_files(root, [layer for layer in layers if layer == mg.TRIAL_LAYER])
    if not used:
        return []
    df = pd.read_csv(used[0][1], sep="\t", dtype=str, keep_default_na=False, usecols=["id"])
    mapping = mg.trial_id_map(root)
    return [mapping.get(v) or ("CHEMBL.COMPOUND:" + v[len("CHEMBL:"):] if v.startswith("CHEMBL:") else v) for v in df["id"]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("graph", nargs="?", default="G4", choices=sorted(mg.RECIPES))
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--out", default="")
    ap.add_argument("--chunk", type=int, default=2_000_000)
    ap.add_argument("--rows-per-file", type=int, default=1_000_000)
    ap.add_argument("--no-parity", action="store_true", help="skip the parity questions (they need a second pass over the edges)")
    a = ap.parse_args(argv)
    man = export(a.root, a.out or None, a.graph, a.chunk, a.rows_per_file, not a.no_parity)
    problems = validate_export(Path(a.out or Path(a.root) / OUT_DIR) / "opencypher", a.rows_per_file)
    for p in problems:
        print("PROBLEM:", p)
    print(json.dumps(man["counts"]))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
