"""
Build-time card index for the Pharos screen (BUILD_ROLES.md section 2, Claude Code item 2): everything the entity
card and the search box need, written ONCE to a small SQLite file, so the app answers a card from a few indexed rows
instead of reading the 26M-edge table at request time.

    python pipelines/pharos_index.py [--per-kind 40] [--n-pred 20] [--chunk 2000000] [--out PATH]

Reads (the same files PharosGraph loads, LAYERS order, Pharos first):
  data/kg/<layer>/{nodes,edges,edge_verdicts}.tsv.gz      every built layer
  outputs/linkpred/predictions.tsv.gz                     when linkpred/predict.py has written it
Writes data/kg/pharos_index/card_index.sqlite (git-ignored, like all of data/kg; --out defaults to the newest of
card_index_v3.sqlite / card_index_v2.sqlite that exists, see index_path), tables:
  nodes(id, category, label, name, symbol, props, lid, lname, lsymbol)     search + the card's "about"
  synonyms(node, syn, lsyn)                       ChEMBL synonyms (data/reference/chembl_synonyms.tsv.gz) the search also reads
  kinds(node, kind, n, n_kept, n_pruned)          evidence counts per node and kind (ALL edges, not only the top)
  rows(node, kind, pos, id, subject, predicate, object, other, outgoing, layer, source, source_version,
       knowledge_level, verdict, confidence)      the top --per-kind rows per node and kind, in card order
  predictions(node, pos, subject, object, other, score, rank, graph, task, known_in, paths)   top --n-pred
  edges, demo (index_graph.py)                    the edges behind "why", the tested questions and ask; the tested questions' stored answers
  mechanism(node, payload), mechanism_part(key, payload)   mechanism neighbours per gene / protein / disease (CC-23;
                                                  mechanism_neighbours.pack: family member rows stored once)
  meta(key, value)                                build time, layers, inputs with size and mtime, settings,
                                                  graph_status (PharosGraph.status()), schema (labels, patterns)

Functions (each takes / returns plain Python or pandas objects):
  edge_kinds(frame)                    -> Series of evidence kinds (personas.evidence_kind per distinct combination)
  build_index(root, out, ...)          -> stats dict; writes the SQLite file atomically (tmp file, then rename)
  index_path(root)                     -> default Path of the index
  CardIndex(path)                      read side, used by app.py when the file exists:
    .graph_status()                    same dict as PharosGraph.status() (+ card_index), computed at build time
    .schema_card()                     labels and (class, predicate, class, n) patterns, as PharosGraph.schema_card
    .search(q, limit)                  same result as PharosGraph.search
    .card(nid, n_pred, per_kind)       same dict as PharosGraph.card (+ served_from), or None for an unknown id
    .stale(root)                       input files changed since the build (the app reports them)
    .status()                          the meta table as a dict

REBUILD (CC-19): the index now also answers explain / node / predict / demo / ask / subgraph, so the app never loads the 26M-edge live
graph while an index exists (TMKG_PHAROS_INDEX=off brings the old behaviour back). Claude Science rebuilds it on the Mac after this
merges: `python3 pipelines/pharos_index.py`. The build makes up to four extra streaming passes over the edge files (the edges the cards
use, their same-pair siblings, the tested questions' neighbourhoods, verdict rows); expect roughly 10-15 minutes instead of ~4, and a
larger file (the edges table is zlib-compressed). Peak memory stays at the card build's level: edges are never held, only hashes.

Identical cards: the row wording, ordering and card assembly are pharos_app.card_row / card_row_order /
assemble_card, shared with the live card; tests/test_pharos_index.py checks card-for-card equality on the fixture.
Memory: edges are streamed per layer in chunks; the verdicts of all layers are held as one id -> (verdict,
confidence) table (26.5M rows on the Mac). Edge ids are assumed unique across layers (PharosGraph keeps the first).

EXPORT FIXES (export_graph_kgx.py, block "export fixes"; rules in the source cards' `export_overrides`): the index is
written with the same corrections as the KGX tables: the Sequence Ontology `type` of every Gene node, corrected
knowledge_level / agent_type on the score-based protein-protein rows, corrected classes and identifiers, list-like text
cells as plain text. The layer files are read unchanged; what each rule touched is stored in meta `export_overrides` and
written to outputs/export_overrides/card_index.json. The value sets (--refresh-values, --write-overlay) use the same
corrected labels, so the query validator accepts what the index holds.
"""
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_graphs  # noqa: E402
import personas  # noqa: E402
import pharos_app  # noqa: E402
import index_graph  # noqa: E402
import mechanism_neighbours as mechn  # noqa: E402
import export_graph_kgx as xk  # noqa: E402
from pharos_app import card_row, card_row_order, assemble_card, NO_PREDICTIONS_NOTE  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EDGE_COLS = ["id", "subject", "predicate", "object", "source", "primary_knowledge_source", "knowledge_level",
             "source_version"]
MECH_COLS = ["mechanism", "action_type", "max_phase", "max_phase_for_ind"]   # edge properties mechanism_neighbours reads
ROW_COLS = ["node", "kind", "pos", "id", "subject", "predicate", "object", "other", "outgoing", "layer", "source",
            "source_version", "knowledge_level", "verdict", "confidence", "potency"]
SCHEMA = """
CREATE TABLE nodes(id TEXT PRIMARY KEY, category TEXT, label TEXT, name TEXT, symbol TEXT, props TEXT,
                   lid TEXT, lname TEXT, lsymbol TEXT);
CREATE TABLE synonyms(node TEXT, syn TEXT, lsyn TEXT);
CREATE TABLE kinds(node TEXT, kind TEXT, n INTEGER, n_kept INTEGER, n_pruned INTEGER);
CREATE TABLE rows(node TEXT, kind TEXT, pos INTEGER, id TEXT, subject TEXT, predicate TEXT, object TEXT, other TEXT,
                  outgoing INTEGER, layer TEXT, source TEXT, source_version TEXT, knowledge_level TEXT, verdict TEXT,
                  confidence REAL, potency TEXT);
CREATE TABLE predictions(node TEXT, pos INTEGER, subject TEXT, object TEXT, other TEXT, score REAL, rank INTEGER,
                         graph TEXT, task TEXT, known_in TEXT, paths TEXT, model TEXT, tpos INTEGER);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE mechanism(node TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE mechanism_part(key TEXT PRIMARY KEY, payload TEXT);
"""
INDEXES = ["CREATE INDEX kinds_node ON kinds(node)", "CREATE INDEX syn_lsyn ON synonyms(lsyn)",
           "CREATE INDEX syn_node ON synonyms(node)", "CREATE INDEX rows_node ON rows(node, kind, pos)",
           "CREATE INDEX pred_node ON predictions(node, pos)", "CREATE INDEX pred_task ON predictions(node, task, tpos)"]


# newest first; v3 = the index of the shipped graph (9 November rebuild, the hosted one); v2 and v1 are earlier builds
INDEX_NAMES = ("card_index_v3.sqlite", "card_index_v2.sqlite", "card_index.sqlite")


def index_path(root=ROOT):
    """Takes the repo root. Returns the default index file in data/kg/pharos_index/: the newest that exists of
    card_index_v3.sqlite (the shipped graph, the one the hosted app serves), card_index_v2.sqlite and card_index.sqlite;
    card_index.sqlite when none exists (the path a fresh build writes).
    Used as the default of --out for the build, --refresh-demo, --refresh-values and --write-overlay, and by the app."""
    d = Path(root) / "data" / "kg" / "pharos_index"
    for name in INDEX_NAMES:
        if (d / name).is_file():
            return d / name
    return d / INDEX_NAMES[-1]


def _read(path, usecols=None, chunksize=None):
    cols = None if usecols is None else (lambda c: c in usecols)
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, usecols=cols, chunksize=chunksize)


def edge_kinds(frame):
    """Takes an edge frame with predicate, knowledge_level, source (primary_knowledge_source optional). Returns a
    Series (same index) of evidence kinds, calling personas.evidence_kind once per distinct combination."""
    cols = [c for c in ("predicate", "knowledge_level", "source", "primary_knowledge_source") if c in frame]
    key = frame[cols].astype(str)
    combos = key.drop_duplicates()
    kinds = {tuple(r): personas.evidence_kind(dict(zip(cols, r))) for r in combos.itertuples(index=False)}
    return pd.Series([kinds[t] for t in key.itertuples(index=False)], index=frame.index, dtype=object)


def _layers(root):
    """[(layer, folder)] of the built layers in pharos_app.LAYERS order, and the names of the missing ones."""
    used, missing = [], []
    for layer in pharos_app.LAYERS:
        d = make_graphs.layer_dir(layer, root)
        (used.append((layer, d)) if d is not None and (d / "edges.tsv.gz").exists() else missing.append(layer))
    return used, missing


def _potency_json(c):
    """Takes an edge chunk. Returns a Series: pharos_app.potency of each edge as JSON, None where the edge has no value
    (only rows with a potency column filled are converted, so the 26M-edge build pays for the ligand rows only)."""
    out = pd.Series([None] * len(c), index=c.index, dtype=object)
    cols = [x for x in ("activity", "pchembl_max") if x in c]
    if not cols:
        return out
    has = np.zeros(len(c), dtype=bool)
    for x in cols:
        has |= (c[x].astype(str) != "").to_numpy()
    sub = c.loc[has, [x for x in pharos_app.POTENCY_COLS if x in c]]
    out.loc[has] = [json.dumps(p) if p else None for p in (pharos_app.potency(r) for r in sub.to_dict("records"))]
    return out


def _sides(chunk, labels, cats):
    """Takes an edge chunk (with layer, verdict, confidence, kind) and the label / category maps. Returns the long
    frame: one row per (card node, edge), outgoing for the subject and incoming for the object, with the sentence
    the card shows (for ordering)."""
    phrase = chunk["predicate"].map(personas.predicate_phrase)
    out = chunk.assign(node=chunk["subject"], other=chunk["object"], outgoing=True)
    inn = chunk.assign(node=chunk["object"], other=chunk["subject"], outgoing=False)
    both = pd.concat([out.assign(phrase=phrase), inn.assign(phrase=phrase)], ignore_index=True)
    nl = both["node"].map(labels).fillna(both["node"])
    ol = both["other"].map(labels).fillna(both["other"])
    both["sentence"] = np.where(both["outgoing"], nl + " " + both["phrase"] + " " + ol, ol + " " + both["phrase"] + " " + nl)
    both["word"] = np.where(both["verdict"] == "keep", "kept", np.where(both["verdict"] == "prune", "pruned", "unscored"))
    return both


def _top(frame, per_kind):
    """Takes the long frame. Returns its first per_kind rows per (node, kind) in card order: card_row_order (kept
    first, higher confidence, sentence), ties as the live card leaves them (outgoing first, predicate, other end,
    file position)."""
    f = frame.assign(_k=(frame["word"] != "kept"), _c=-frame["confidence"].fillna(0.0), _o=~frame["outgoing"])
    f = f.sort_values(["node", "kind", "_k", "_c", "sentence", "_o", "predicate", "other", "seq"], kind="mergesort")
    return f.groupby(["node", "kind"], sort=False).head(per_kind).drop(columns=["_k", "_c", "_o"])


def build_index(root=ROOT, out=None, per_kind=40, n_pred=20, chunk=2_000_000, log=print):
    """Takes the repo root, the output path (default index_path(root)), rows kept per node and kind, predictions
    kept per node and the edge chunk size. Reads every built layer once, streaming. Returns a stats dict (nodes,
    edges, rows, predictions, seconds, path). Raises FileNotFoundError when no layer is built."""
    t0 = time.time()
    root = Path(root)
    out = Path(out) if out else index_path(root)
    used, missing = _layers(root)
    if not used:
        raise FileNotFoundError(f"no built Pharos layer under {root / 'data' / 'kg'}")
    inputs = []

    # nodes: per FIELD the first non-empty value over the layers (as in PharosGraph), then ChEMBL names and synonyms.
    # Export fixes: identifier corrections before the merge, classes / gene types / text cells after it.
    ov = xk.load_overrides(root, [layer for layer, _ in used])
    problems = xk.check_overrides(ov, root)
    if problems:
        raise ValueError("export rules name a term that is not in its reference file: " + "; ".join(problems))
    frames = [(layer, _read(d / "nodes.tsv.gz")) for layer, d in used]
    renames, rename_classes, rename_counts = xk.id_renames(pd.concat([f["id"] for _, f in frames]), ov, root)
    nodes = pharos_app.harmonised_nodes([(layer, xk.rename_node_ids(f, renames, rename_classes)) for layer, f in frames])   # one class per id prefix (CC-27)
    chembl = pharos_app.chembl_names(root)
    nodes, node_counts = xk.fix_nodes(nodes, ov, chembl=chembl)       # names here, so the counts are those of the index
    edge_counts = {}
    inputs += [d / "nodes.tsv.gz" for _, d in used]
    recs = [pharos_app._clean(r) for r in nodes.to_dict("records")]
    synonyms = pharos_app.name_compounds({r["id"]: r for r in recs}, chembl)
    if chembl["path"]:
        inputs.append(root / chembl["path"])
    labels = pd.Series({r["id"]: r.get("name") or r.get("symbol") or r["id"] for r in recs}, dtype=object)
    cats = pd.Series({r["id"]: r.get("category", "") for r in recs}, dtype=object)
    log(f"nodes: {len(recs):,}; named from ChEMBL: {sum(1 for n in chembl['names'] if n in labels.index):,}; "
        f"synonyms: {sum(len(v) for v in synonyms.values()):,}")

    # verdicts of every layer: id -> verdict, confidence
    vframes = []
    for layer, d in used:
        vp = d / "edge_verdicts.tsv.gz"
        if vp.exists():
            inputs.append(vp)
            vframes.append(_read(vp, usecols={"id", "verdict", "confidence"}))
    if vframes:
        v = pd.concat(vframes, ignore_index=True).drop_duplicates("id", keep="first").set_index("id")
        v["verdict"] = v["verdict"].astype("category")
        v["confidence"] = pd.to_numeric(v.get("confidence", pd.Series(dtype=str)), errors="coerce").astype("float64")
    else:
        v = None
    log(f"verdicts: {0 if v is None else len(v):,}")

    # predictions: the top n_pred per node, and the nodes / pairs whose paths and layers we must collect
    pfile, pmodel = pharos_app.predictions_source(root)       # meta-path file (with its own paths) before the GNN file
    preds = None
    if pfile is not None:
        inputs.append(pfile)
        p = _read(pfile)
        for end in ("subject", "object"):                       # the predictions name nodes by the ids of the layer files
            p[end] = p[end].map(lambda x: renames.get(x, x))
        p["score"] = p["score"].astype(float)
        p = p.sort_values(["score", "subject", "object"], ascending=[False, True, True], kind="mergesort")
        per_node = []
        for side, other in (("subject", "object"), ("object", "subject")):
            per_node.append(p.assign(node=p[side], other=p[other]))
        long = pd.concat(per_node, ignore_index=True)
        long = long.sort_values(["node", "score", "subject", "object"], ascending=[True, False, True, True], kind="mergesort")
        # `pos` = rank of the row among a node's distinct pairs (best score first, any task); `tpos` = rank among the distinct pairs of
        # its task. A row is kept when either rank is below n_pred, so a card (pos) and a task filter (tpos) both read exact top-n lists.
        dup_all = long.duplicated(["node", "subject", "object"])
        dup_task = long.duplicated(["node", "task", "subject", "object"])
        long["pos"] = long[~dup_all].groupby("node", sort=False).cumcount().reindex(long.index)
        long["tpos"] = long[~dup_task].groupby(["node", "task"], sort=False).cumcount().reindex(long.index)
        preds = long[(long["pos"] < n_pred) | (long["tpos"] < n_pred)].copy()
        pred_nodes = set(preds["subject"]) | set(preds["object"])
        pred_pairs = pd.MultiIndex.from_arrays([preds["subject"], preds["object"]]).unique()
    log(f"predictions kept: {0 if preds is None else len(preds):,}")

    counts, tops, n_edges = [], [], 0
    by_layer, by_pred, patterns, versions = {}, [], [], set()
    import querier
    value_sets = {}                                               # CC-31c: the values the query validator checks literals against
    from kg_schema import local
    lcat = cats.map(local)
    neigh_out, neigh_in, pair_layers, mparts = [], [], [], []
    for layer, d in used:
        inputs.append(d / "edges.tsv.gz")
        for c in _read(d / "edges.tsv.gz", usecols=set(EDGE_COLS) | set(MECH_COLS) | set(pharos_app.POTENCY_COLS), chunksize=chunk):
            for col in EDGE_COLS:
                if col not in c:
                    c[col] = ""
            c = xk.fix_edges(c, layer, ov, renames, edge_counts.setdefault(layer, {}))   # corrected ends and labels
            mparts.append(mechn.collect(c.drop_duplicates("id")))
            c = c.drop(columns=[x for x in MECH_COLS if x in c])
            c = c.drop_duplicates("id")
            c["potency"] = _potency_json(c)                     # CC-34: shown on binding rows of the card
            n_edges += len(c)
            c["layer"] = layer
            c["seq"] = np.arange(n_edges - len(c), n_edges)          # file position: the last tie-breaker
            if v is not None:
                vv = v.reindex(c["id"])
                c["verdict"] = vv["verdict"].astype(object).where(vv["verdict"].notna(), None).values
                c["confidence"] = vv["confidence"].astype(float).values
            else:
                c["verdict"], c["confidence"] = None, np.nan
            c["kind"] = edge_kinds(c).values
            by_layer[layer] = by_layer.get(layer, 0) + len(c)
            querier.add_value_sets(value_sets, c.drop(columns=["verdict"]))
            by_pred.append(c["predicate"].value_counts())
            patterns.append(pd.DataFrame({"s": c["subject"].map(lcat), "p": c["predicate"].map(local),
                                          "o": c["object"].map(lcat)}).value_counts())
            versions |= set(c["source_version"]) - {""}
            both = _sides(c, labels, cats)
            counts.append(both.assign(n=1, n_kept=(both["word"] == "kept").astype(int),
                                      n_pruned=(both["word"] == "pruned").astype(int))
                          .groupby(["node", "kind"], as_index=False, sort=False)[["n", "n_kept", "n_pruned"]].sum())
            tops.append(_top(both, per_kind))
            if preds is not None:
                keep = ["node", "other", "id", "subject", "predicate", "object"]
                neigh_out.append(both[both["outgoing"] & both["node"].isin(pred_nodes)][keep])
                neigh_in.append(both[~both["outgoing"] & both["node"].isin(pred_nodes)][keep])
                hit = pd.MultiIndex.from_arrays([c["subject"], c["object"]]).isin(pred_pairs)
                pair_layers.append(c.loc[hit, ["subject", "object", "layer"]])
            log(f"  {layer}: {n_edges:,} edges read")

    kinds = pd.concat(counts, ignore_index=True).groupby(["node", "kind"], as_index=False)[["n", "n_kept", "n_pruned"]].sum()
    rows = _top(pd.concat(tops, ignore_index=True), per_kind)
    rows["pos"] = rows.groupby(["node", "kind"], sort=False).cumcount()

    # mechanism neighbours (CC-23): small tables collected while streaming, one payload per gene / protein / disease
    dto_path = root / mechn.DTO_TABLE
    if dto_path.exists():
        inputs.append(dto_path)
    contexts = mechn.build_contexts(mechn.concat_parts(mparts), labels, mechn.read_dto(dto_path))
    log(f"mechanism neighbours: {len(contexts):,} cards")

    pred_rows = []
    if preds is not None:
        nb = pd.concat(neigh_out + neigh_in, ignore_index=True).drop_duplicates(["node", "other"], keep="first")
        nmap = {n: dict(zip(f["other"], f[["id", "subject", "predicate", "object"]].to_dict("records")))
                for n, f in nb.groupby("node", sort=False)}
        pl = pd.concat(pair_layers, ignore_index=True) if pair_layers else pd.DataFrame(columns=["subject", "object", "layer"])
        known = pl.groupby(["subject", "object"])["layer"].agg(lambda x: sorted(set(x))).to_dict()
        for r in preds.to_dict("records"):
            a, b = r["subject"], r["object"]
            na, nbb = nmap.get(a, {}), nmap.get(b, {})
            shared = sorted((set(na) & set(nbb)) - {a, b}, key=lambda x: (labels.get(x, x), x))[:3]
            paths = [dict(via=x, edges=[na[x], nbb[x]]) for x in shared]
            if pmodel == "metapath":                                  # the file carries the paths and their edge ids
                paths = [{k: x[k] for k in ("via", "shape", "edges")} for x in json.loads(r.get("paths") or "[]")[:3]]
            rank = r.get("rank", "")
            pred_rows.append((r["node"], None if pd.isna(r["pos"]) else int(r["pos"]), a, b, r["other"], float(r["score"]),
                              int(rank) if str(rank).isdigit() else None, r.get("graph"), r.get("task"),
                              json.dumps(known.get((a, b), [])), json.dumps(paths),
                              pharos_app.MODEL_WORDS[pmodel], None if pd.isna(r["tpos"]) else int(r["tpos"])))

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    props = lambda r: json.dumps({k: v for k, v in r.items() if k not in ("id", "category", "name", "symbol")})
    con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r["id"], r.get("category", ""), labels[r["id"]], r.get("name"), r.get("symbol"), props(r),
                      r["id"].lower(), (r.get("name") or "").lower(), (r.get("symbol") or "").lower()) for r in recs])
    con.executemany("INSERT INTO synonyms VALUES (?,?,?)",
                    [(nid, x, x.lower()) for nid, xs in synonyms.items() for x in xs])
    con.executemany("INSERT INTO kinds VALUES (?,?,?,?,?)", kinds[["node", "kind", "n", "n_kept", "n_pruned"]]
                    .astype(object).itertuples(index=False, name=None))
    rr = rows.assign(outgoing=rows["outgoing"].astype(int))[ROW_COLS].astype(object)
    rr = rr.where(pd.notna(rr), None)
    con.executemany(f"INSERT INTO rows VALUES ({','.join('?' * len(ROW_COLS))})", rr.itertuples(index=False, name=None))
    con.executemany("INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", pred_rows)
    # ---- the edges the page's "why", tested questions, ask and download need (CC-19; module docstring of index_graph)
    con.executescript(index_graph.SCHEMA)
    sel_ids = set(rows["id"]) | mechn.edge_ids(contexts)
    mnodes, mparts_ = mechn.pack(contexts)
    con.executemany("INSERT INTO mechanism VALUES (?,?)", [(k, json.dumps(v)) for k, v in sorted(mnodes.items())])
    con.executemany("INSERT INTO mechanism_part VALUES (?,?)", [(k, json.dumps(v)) for k, v in sorted(mparts_.items())])
    for pr in pred_rows:
        for path in json.loads(pr[10]):
            sel_ids.update(e["id"] for e in path["edges"])
    selected_hash = np.unique(index_graph.hash_ids(sel_ids))
    plans = index_graph.demo_plan({r["id"]: r for r in recs})

    def edge_chunks():
        seq = 0
        for layer, d in used:
            for c in _read(d / "edges.tsv.gz", chunksize=chunk):
                c = xk.fix_edges(c, layer, ov, renames)             # the same corrections as the pass above (not counted twice)
                c["layer"] = layer                                  # PharosGraph names the layer by its card, whatever the file says
                c["seq"] = np.arange(seq, seq + len(c))
                seq += len(c)
                yield layer, c

    log("edges for explain / demo / ask ...")
    fe = index_graph.fill_edges(con, edge_chunks, selected_hash, plans, log=log)
    stored_hash = np.unique(index_graph.hash_ids(r[0] for r in con.execute("SELECT id FROM edges")))

    def verdict_chunks():
        for layer, d in used:
            vp = d / "edge_verdicts.tsv.gz"
            if vp.exists():
                for c in _read(vp, chunksize=chunk):
                    c = c[np.isin(index_graph.hash_ids(c["id"]), stored_hash)]
                    if len(c):
                        yield c.assign(layer=layer)

    index_graph.fill_verdicts(con, verdict_chunks())
    inputs += [d / "edge_verdicts.tsv.gz" for _, d in used if (d / "edge_verdicts.tsv.gz").exists() and (d / "edge_verdicts.tsv.gz") not in inputs]
    for stmt in INDEXES + index_graph.INDEXES:
        con.execute(stmt)
    n_stored = con.execute("SELECT count(*) FROM edges").fetchone()[0]
    log(f"edges stored: {n_stored:,} in {fe['passes']} pass(es)")
    by_pred = pd.concat(by_pred).groupby(level=0).sum()
    patterns = pd.concat(patterns).groupby(level=[0, 1, 2]).sum()
    prop_sets = {}
    for r in recs:
        prop_sets.setdefault(local(r.get("category", "")), set()).update(k for k in r if k != "category")
    graph_status = dict(
        layers=[l for l, _ in used], layers_missing=missing, nodes=len(recs), edges=n_edges,
        edges_by_layer={k: int(n) for k, n in by_layer.items()},
        nodes_by_category={k: int(n) for k, n in cats.value_counts().items()},
        edges_by_predicate={k: int(n) for k, n in by_pred.sort_values(ascending=False, kind="mergesort").items()},
        verdicts={} if v is None else {k: int(n) for k, n in v["verdict"].value_counts().items() if n},
        predictions=None if preds is None else dict(rows=int(len(p)), model=pmodel, graphs=sorted(set(p["graph"])),
                                                    tasks=sorted(set(p["task"]))),
        source_versions=sorted(versions))
    schema = dict(labels={k: sorted(x) for k, x in sorted(prop_sets.items())},
                  patterns=[(a, b, c_, int(n)) for (a, b, c_), n in patterns.sort_index().items()])
    fixes = xk.overrides_report("card index", ov, node_counts, {k: v for k, v in edge_counts.items() if v}, rename_counts,
                                totals=dict(nodes=len(recs), edges=n_edges), renames=renames)
    for line in xk.fixes_markdown(fixes):
        log("export fixes " + line)
    meta = dict(built_at=time.strftime("%Y-%m-%d %H:%M:%S"), graph_status=json.dumps(graph_status),
                schema=json.dumps(schema), layers=json.dumps([l for l, _ in used]),
                layers_missing=json.dumps(missing), per_kind=str(per_kind), n_pred=str(n_pred),
                predictions_available=json.dumps(preds is not None), edges=str(n_edges),
                rel_props=json.dumps(fe["rel_props"]), edges_stored=str(n_stored),
                rel_values=json.dumps(querier.finish_value_sets(value_sets)),
                export_overrides=json.dumps(fixes, sort_keys=True),
                demo_notes="; ".join(f"{q} needs more neighbours than {index_graph.MAX_FRONTIER:,}" for q in fe["skipped"]) +
                           "; ".join(f"{p['id']} has no anchored node" for p in plans if p["depth"] is None),
                names_source=json.dumps(dict(path=chembl["path"], source=chembl["source"],
                                             named=sum(1 for n in chembl["names"] if n in labels.index))),
                inputs=json.dumps([dict(path=str(p.relative_to(root)), bytes=p.stat().st_size, mtime=p.stat().st_mtime)
                                   for p in inputs]))
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    ci = CardIndex(tmp)                                           # the tested questions, answered once, from the edges just stored
    demo = index_graph.IndexGraph(ci).compute_demo()
    ci.con.close()
    con.executemany("INSERT INTO demo VALUES (?,?)", [(k, json.dumps(v)) for k, v in demo.items()])
    con.commit()
    con.close()
    os.replace(tmp, out)
    xk.write_report(root, "card_index", fixes)                    # small, committed: the schema card reads it
    stats = dict(path=str(out), nodes=len(recs), edges=n_edges, rows=len(rows), kinds=len(kinds),
                 predictions=len(pred_rows), seconds=round(time.time() - t0, 1), bytes=out.stat().st_size)
    log(f"card index written: {stats}")
    return stats


class CardIndex:
    """Read side of the card index. Takes the SQLite path (FileNotFoundError when it does not exist). Cards and
    search results are the same dicts PharosGraph returns, read from a few indexed rows."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"no card index at {self.path}: run python pipelines/pharos_index.py")
        self._local = threading.local()
        self.meta = {r["key"]: r["value"] for r in self.con.execute("SELECT key, value FROM meta")}

    @property
    def con(self):
        """A read-only SQLite connection to the index, one per thread (opened on the thread's first use). The web app answers
        requests on many threads; with one shared connection a slow read (a typed question) held up every card and search of
        every other visitor (review finding SEC-01). Setting `con` (tests) pins one connection for all threads."""
        fixed = self.__dict__.get("_con")
        if fixed is not None:
            return fixed
        local = self.__dict__.setdefault("_local", threading.local())
        con = getattr(local, "con", None)
        if con is None:
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            con.row_factory = sqlite3.Row
            local.con = con
        return con

    @con.setter
    def con(self, value):
        self.__dict__["_con"] = value

    def status(self):
        """The meta table: built_at, layers, layers_missing, per_kind, n_pred, edges, predictions_available, inputs."""
        m = dict(self.meta)
        for k in ("layers", "layers_missing", "inputs", "predictions_available"):
            m[k] = json.loads(m[k])
        return m

    def graph_status(self):
        """The same dict as PharosGraph.status() (layers, counts by layer / category / predicate, verdict counts,
        predictions, source versions), computed at build time, plus card_index = built_at and the file path."""
        st = json.loads(self.meta["graph_status"])
        st["card_index"] = dict(built_at=self.meta["built_at"], path=str(self.path))
        return st

    def schema_card(self):
        """{labels: class -> properties, patterns: [(subject class, predicate, object class, n)]} as in
        PharosGraph.schema_card (without rel_props / enums), computed at build time."""
        sc = json.loads(self.meta["schema"])
        return dict(labels=sc["labels"], patterns=[tuple(x) for x in sc["patterns"]])

    def stale(self, root=ROOT):
        """Takes the repo root. Returns the input files whose size or mtime changed (or that vanished) since the
        build: [] means the index matches the files on disk."""
        out = []
        for f in json.loads(self.meta["inputs"]):
            p = Path(root) / f["path"]
            if not p.exists() or p.stat().st_size != f["bytes"] or abs(p.stat().st_mtime - f["mtime"]) > 1:
                out.append(f["path"])
        return out

    def _node(self, nid):
        r = self.con.execute("SELECT * FROM nodes WHERE id = ?", (nid,)).fetchone()
        if r is None:
            return None
        d = {"id": r["id"], "category": r["category"]}
        for k in ("name", "symbol"):
            if r[k]:
                d[k] = r[k]
        d.update(json.loads(r["props"]))
        return d, r["label"]

    def _labels(self, ids):
        ids = list(set(ids))
        out = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = f"SELECT id, label, category FROM nodes WHERE id IN ({','.join('?' * len(part))})"
            out.update({r["id"]: (r["label"], r["category"]) for r in self.con.execute(q, part)})
        return out

    def suggest(self, q, n=3):
        """As PharosGraph.suggest: names / symbols of about the same length and the same first character, compared with
        pharos_app.close_matches."""
        q = q.lower().strip()
        if not q:
            return []
        lo, hi = pharos_app.length_window(q)
        rows = self.con.execute("SELECT id, lname, lsymbol, label, category FROM nodes WHERE (substr(lname, 1, 1) = ?1 AND "
                                "length(lname) BETWEEN ?2 AND ?3) OR (substr(lsymbol, 1, 1) = ?1 AND length(lsymbol) BETWEEN ?2 AND ?3)",
                                (q[:1], lo, hi)).fetchall()
        info = {r["id"]: (r["label"], r["category"]) for r in rows}
        cands = [(t, r["id"]) for r in rows for t in (r["lname"], r["lsymbol"]) if t and t[:1] == q[:1] and lo <= len(t) <= hi]
        return [dict(id=nid, label=info[nid][0], category=info[nid][1], did_you_mean=True, score=round(s, 3))
                for s, nid, _ in pharos_app.close_matches(q, cands, n)]

    def search(self, q, limit=20):
        """Nodes whose id, name, symbol or ChEMBL synonym contains q (case-insensitive); an exact match (the whole id, name,
        symbol or a synonym) first, then shorter names first (PharosGraph.search)."""
        q = q.lower().strip()
        if not q:
            return []
        sql = ("WITH hit(id) AS (SELECT id FROM nodes WHERE instr(lid, ?1) > 0 OR instr(lname, ?1) > 0 OR "
               "instr(lsymbol, ?1) > 0 UNION SELECT node FROM synonyms WHERE instr(lsyn, ?1) > 0) "
               "SELECT n.id, n.label, n.category, (n.lid = ?1 OR n.lname = ?1 OR n.lsymbol = ?1 OR EXISTS "
               "(SELECT 1 FROM synonyms s WHERE s.node = n.id AND s.lsyn = ?1)) AS exact "
               "FROM hit JOIN nodes n ON n.id = hit.id "
               "ORDER BY exact DESC, length(coalesce(nullif(n.name, ''), n.id)), n.id LIMIT ?2")
        return [dict(id=r["id"], label=r["label"], category=r["category"]) for r in self.con.execute(sql, (q, limit))]

    def _predicted(self, preds, L, lab, statement):
        """The `predicted` rows of a card / predicted_links from stored prediction rows (same keys as PharosGraph)."""
        return [dict(subject=p["subject"], object=p["object"], other=p["other"], other_label=L(p["other"]),
                     other_category=lab.get(p["other"], ("", ""))[1], score=round(p["score"], 4),
                     rank=p["rank"], graph=p["graph"], task=p["task"], known_in=json.loads(p["known_in"]),
                     model=p["model"],
                     paths=[dict(via=x["via"], via_label=L(x["via"]), **({"shape": x["shape"]} if "shape" in x else {}),
                                 edges=[dict(id=e["id"], statement=statement(e)) for e in x["edges"]])
                            for x in json.loads(p["paths"])]) for p in preds]

    def predicted_links(self, nid, n=20, task=None):
        """As PharosGraph.predicted_links: {node, label, predictions: [...]} (best score first, at most n; at most the build's --n-pred
        per node and, with `task`, per task), or the no-predictions note, or None for an unknown node."""
        got = self._node(nid)
        if got is None:
            return None
        label = got[1]
        if not json.loads(self.meta["predictions_available"]):
            return dict(node=nid, label=label, predictions=[], note=NO_PREDICTIONS_NOTE)
        if task:
            preds = self.con.execute("SELECT * FROM predictions WHERE node = ? AND task = ? AND tpos < ? ORDER BY tpos",
                                     (nid, task, n)).fetchall()
        else:
            preds = self.con.execute("SELECT * FROM predictions WHERE node = ? AND pos < ? ORDER BY pos", (nid, n)).fetchall()
        ids = [nid]
        for p in preds:
            ids += [p["subject"], p["object"]]
            for path in json.loads(p["paths"]):
                ids += [path["via"]] + [x for e in path["edges"] for x in (e["subject"], e["object"])]
        lab = self._labels(ids)
        L = lambda x: lab.get(x, (x, ""))[0]
        statement = lambda e: f"{L(e['subject'])} -{pharos_app.path_predicate(e['predicate'])}-> {L(e['object'])}"
        return dict(node=nid, label=label, predictions=self._predicted(preds, L, lab, statement))

    def graph(self, overlay_dir=None):
        """The graph-shaped view over the index (index_graph.IndexGraph): node view, explain, tested questions, ask, subgraph.
        overlay_dir: the data root holding the optional overlay files (CC-38; index_graph.load_overlay)."""
        cache = self.__dict__.setdefault("_graphs", {})
        key = str(overlay_dir) if overlay_dir is not None else None
        if key not in cache:
            cache[key] = index_graph.IndexGraph(self, overlay_dir)
        return cache[key]

    def explain_edge(self, eid):
        """As PharosGraph.explain_edge, from the edges table: provenance, verdict and reasons, same-pair rows, dated timeline."""
        return self.graph().explain_edge(eid)

    def node_view(self, nid, limit=200):
        """As PharosGraph.node_view over the stored edges (the links a card shows, not all of the node's links)."""
        return self.graph().node_view(nid, limit)

    def demo(self, ids=None):
        """The tested questions with their stored answers (PharosGraph.demo)."""
        return self.graph().demo(ids)

    def subgraph(self, edge_ids):
        """(nodes, edges) behind an answer's evidence ids, as subgraph_export.collect gives for the live graph; ids not stored are skipped."""
        import subgraph_export
        return subgraph_export.collect(self.graph(), edge_ids)

    def card(self, nid, n_pred=10, per_kind=40, picture_edges=40):
        """Takes a node id. Returns the same dict as PharosGraph.card plus served_from='card index' (per_kind is
        capped at the build's --per-kind), or None when the node is not in the index."""
        got = self._node(nid)
        if got is None:
            return None
        node, label = got
        kinds = self.con.execute("SELECT kind, n, n_kept, n_pruned FROM kinds WHERE node = ?", (nid,)).fetchall()
        rows = self.con.execute("SELECT * FROM rows WHERE node = ? AND pos < ? ORDER BY kind, pos", (nid, per_kind)).fetchall()
        preds = self.con.execute("SELECT * FROM predictions WHERE node = ? AND pos < ? ORDER BY pos", (nid, n_pred)).fetchall()
        ids = [r["subject"] for r in rows] + [r["object"] for r in rows] + [nid]
        for p in preds:
            ids += [p["subject"], p["object"]]
            for path in json.loads(p["paths"]):
                ids += [path["via"]] + [x for e in path["edges"] for x in (e["subject"], e["object"])]
        lab = self._labels(ids)
        L = lambda x: lab.get(x, (x, ""))[0]
        statement = lambda e: f"{L(e['subject'])} -{pharos_app.path_predicate(e['predicate'])}-> {L(e['object'])}"
        by_kind = {}
        for r in rows:
            base = dict(id=r["id"], subject=r["subject"], predicate=r["predicate"], object=r["object"],
                        statement=statement(r), layer=r["layer"], source=r["source"] or None,
                        source_version=r["source_version"] or None, knowledge_level=r["knowledge_level"] or None,
                        verdict=r["verdict"], confidence=r["confidence"],
                        potency=json.loads(r["potency"]) if "potency" in r.keys() and r["potency"] else None)
            row = card_row(base, label, r["other"], L(r["other"]), lab.get(r["other"], ("", ""))[1],
                           bool(r["outgoing"]), r["kind"])
            by_kind.setdefault(r["kind"], []).append(row)
        known = [dict(kind=k["kind"], n=k["n"], n_kept=k["n_kept"], n_pruned=k["n_pruned"],
                      rows=sorted(by_kind.get(k["kind"], []), key=card_row_order)) for k in kinds]
        predicted = self._predicted(preds, L, lab, statement)
        note = None if json.loads(self.meta["predictions_available"]) else NO_PREDICTIONS_NOTE
        c = assemble_card(nid, node, label, known, predicted, note, n_known=sum(k["n"] for k in kinds),
                          picture_edges=picture_edges)
        c["mechanism_neighbours"] = self.mechanism(nid, node.get("category", ""))
        c["served_from"] = "card index"
        return c

    def mechanism(self, nid, category=""):
        """Takes a node id (and its category). Returns the mechanism-neighbours payload stored at build time
        (mechanism_neighbours.build_contexts), the empty-state payload for a gene / protein / disease without one,
        or None for other nodes and for an index built before CC-23."""
        try:
            r = self.con.execute("SELECT payload FROM mechanism WHERE node = ?", (nid,)).fetchone()
        except sqlite3.OperationalError:                      # index built before the table existed
            return None
        if r is None:
            return mechn.empty_context(category)
        part = lambda k: json.loads(self.con.execute("SELECT payload FROM mechanism_part WHERE key = ?", (k,)).fetchone()[0])
        return mechn.unpack(json.loads(r["payload"]), part)


def refresh_demo(path):
    """Takes the path of a built card index. Recomputes the tested questions' stored answers (index_graph.compute_demo on the
    edges already in the index) and replaces the demo table; nothing else changes. For a change of the demo Cypher or of the
    row order (CC-31a) without the full rebuild. Returns {question id: passed}."""
    ci = CardIndex(path)
    demo = index_graph.IndexGraph(ci).compute_demo()
    ci.con.close()
    con = sqlite3.connect(str(path))
    con.execute("DELETE FROM demo")
    con.executemany("INSERT INTO demo VALUES (?,?)", [(k, json.dumps(v)) for k, v in demo.items()])
    con.commit()
    con.close()
    return {k: v["passed"] for k, v in demo.items()}


def _value_sets(root, chunk=2_000_000):
    """Takes the repo root. Streams the enumerated relationship properties (querier.ENUM_REL_PROPS) of every built layer's
    edges, with the export fixes' label corrections applied (the columns the rules read are loaded too, then dropped), so
    the value sets are those of the index, not of the uncorrected layer files. Returns querier.finish_value_sets' dict."""
    import querier
    used = _layers(Path(root))[0]
    ov = xk.load_overrides(root, [layer for layer, _ in used])
    cols = set(querier.ENUM_REL_PROPS) - {"layer", "verdict"}
    acc = {}
    for layer, d in used:
        for c in _read(d / "edges.tsv.gz", usecols=cols | xk.rule_columns(ov, layer), chunksize=chunk):
            c = xk.fix_edges(c, layer, ov)
            querier.add_value_sets(acc, c[[x for x in c.columns if x in cols]].assign(layer=layer))
    return querier.finish_value_sets(acc)


def refresh_value_sets(path, root=ROOT, chunk=2_000_000):
    """Takes the path of a built card index and the repo root. Reads the enumerated relationship properties
    (querier.ENUM_REL_PROPS) of every built layer's edges (those columns only, streaming) and writes them to the index's meta
    as rel_values, which the query validator checks string literals against (CC-31c). Returns the value sets."""
    out = _value_sets(root, chunk)
    con = sqlite3.connect(str(path))
    con.execute("DELETE FROM meta WHERE key = 'rel_values'")
    con.execute("INSERT INTO meta VALUES (?,?)", ("rel_values", json.dumps(out)))
    con.commit()
    con.close()
    return out


def write_overlay(path, out_dir, root=None, chunk=2_000_000):
    """Takes a built card index, the folder to write to (the data root that is uploaded) and optionally the repo root whose
    layer files give the value sets. Writes demo_overlay.json (the tested questions answered on the index's edges with the
    current Cypher: index_graph.IndexGraph.compute_demo) and value_sets.json (the value sets from the layer files when root is
    given, else the index's meta rel_values). Returns {file name: bytes}. CC-38: upload both instead of the index."""
    ci = CardIndex(path)
    demo = index_graph.IndexGraph(ci).compute_demo()
    if root is not None and _layers(Path(root))[0]:
        values = _value_sets(root, chunk)
    else:
        values = json.loads(ci.meta.get("rel_values") or "{}")
    ci.con.close()
    made = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    q = {d["id"]: d["cypher"] for d in pharos_app.DEMO_QUESTIONS}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {index_graph.OVERLAY_DEMO: dict(made_at=made, index=Path(path).name,
                                            results={k: dict(cypher=q[k], result=v) for k, v in demo.items()}),
             index_graph.OVERLAY_VALUES: dict(made_at=made, value_sets=values)}
    sizes = {}
    for name, obj in files.items():
        (out / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        sizes[name] = (out / name).stat().st_size
    return sizes


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--out", default="", help="index file (default: the newest of card_index_v3 / _v2 / "
                                                       "card_index.sqlite in data/kg/pharos_index/ that exists)")
    ap.add_argument("--per-kind", type=int, default=40)
    ap.add_argument("--n-pred", type=int, default=20)
    ap.add_argument("--chunk", type=int, default=2_000_000)
    ap.add_argument("--refresh-demo", action="store_true", help="only recompute the tested questions' answers in the index at --out "
                                                                "(default path) and exit")
    ap.add_argument("--write-overlay", default="", metavar="DIR", help="write demo_overlay.json and value_sets.json for the "
                                                                       "index at --out (default path) into DIR and exit (CC-38)")
    ap.add_argument("--refresh-values", action="store_true", help="only write the relationship value sets the query validator "
                                                                  "checks literals against (meta rel_values) into the index and exit")
    a = ap.parse_args(argv)
    if a.refresh_demo:
        print(json.dumps(refresh_demo(a.out or index_path(a.root))))
        return 0
    if a.write_overlay:
        print(json.dumps(write_overlay(a.out or index_path(a.root), a.write_overlay, a.root, a.chunk)))
        return 0
    if a.refresh_values:
        print(json.dumps({k: len(v) for k, v in refresh_value_sets(a.out or index_path(a.root), a.root, a.chunk).items()}))
        return 0
    build_index(a.root, a.out or None, per_kind=a.per_kind, n_pred=a.n_pred, chunk=a.chunk)


if __name__ == "__main__":
    sys.exit(main())
