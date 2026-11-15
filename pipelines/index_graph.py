"""
The part of the card index that answers "why is this linked", the tested questions, ask and the subgraph download WITHOUT the
26-million-edge live graph (packet CC-19). pharos_index.build_index writes the tables; this module reads and fills them.

Tables it adds to card_index.sqlite (all built while streaming, peak RAM stays at the card build's level):
  edges(id PK, seq, subject, predicate, object, layer, props BLOB, verdict BLOB)
        every edge that appears in a card row or a prediction path, every edge that states the same (subject, object) pair as one of
        those (so the corroboration and the timeline of "why" are complete), and the edges the tested demo questions need
        (the anchors' own edges, and for a query that walks two steps the edges of their neighbours). props = zlib JSON of all
        non-empty columns of the edge row exactly as PharosGraph holds it; verdict = zlib JSON of the verdict row (or NULL);
        seq = the edge's position in the layers' concatenation, so the order of same-pair edges equals the live graph's.
  demo(id PK, result)   the tested questions' answers (rows, columns, evidence ids, passed, note), computed at build time by running
                        the same openCypher the live graph runs, on this edges table (a superset of every query's neighbourhood).
  meta: rel_props (the edge columns that hold a value), demo_depth / demo_notes, edges_stored.

  IndexGraph(card_index)   a graph-shaped read-only view (nodes, edges, by_subject, by_object, verdict, label, same_pair, find_entities,
                           schema_card, candidates, demo, node_view, explain_edge) over those tables; pharos_app.GraphViews supplies
                           node_view / explain_edge / statement, so the output is the live graph's output. querier.Querier and
                           subgraph_export.collect accept it as they accept PharosGraph.
Limit that must be said out loud: by_subject / by_object hold the edges of the edges table, not all 29.6 million. A free-form question
answered from the index sees the links a card shows (the top rows per node and kind) plus the neighbourhoods of the tested
questions; for a node with more links than the card keeps, the answer is a subset (`from_index` in the answer says so). The tested
questions are exact.
"""
import json
import re
import sqlite3
import zlib
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

import pharos_app
from pharos_app import GraphViews, DEMO_QUESTIONS, ID_PATTERNS, NO_PREDICTIONS_NOTE, MODEL_WORDS

PAIR_SEP = "\x1f"
SCHEMA = """
CREATE TABLE edges(id TEXT PRIMARY KEY, seq INTEGER, subject TEXT, predicate TEXT, object TEXT, layer TEXT, props BLOB, verdict BLOB);
CREATE TABLE demo(id TEXT PRIMARY KEY, result TEXT);
"""
INDEXES = ["CREATE INDEX edges_subject ON edges(subject)", "CREATE INDEX edges_object ON edges(object)",
           "CREATE INDEX edges_pair ON edges(subject, object)"]
MAX_FRONTIER = 20_000          # a demo query that would need the edges of more neighbours than this is not precomputed


def blob(obj):
    """dict -> zlib-compressed JSON bytes."""
    return zlib.compress(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode(), 1)


def unblob(b):
    return json.loads(zlib.decompress(b)) if b is not None else None


def hash_ids(values):
    return pd.util.hash_array(np.asarray(list(values), dtype=object))


def hash_pairs(subjects, objects):
    return hash_ids(s + PAIR_SEP + o for s, o in zip(subjects, objects))


# ------------------------------------------------------------------------------------------------ demo planning
def demo_plan(recs, questions=None):
    """Takes {node id: record} and the demo questions. Returns [{id, cypher, anchors: set of node ids, depth}] where depth is the
    number of edge hops from the anchors that the query's matches can reach (1: every matched edge touches an anchor; 2: some matched
    edge only touches a neighbour of an anchor), or None when the query has no anchored node (it cannot be precomputed)."""
    import querier
    by_name = {}
    for nid, n in recs.items():
        for k in ("name", "symbol"):
            if n.get(k):
                by_name.setdefault(str(n[k]), []).append(nid)
    plans = []
    for d in (questions if questions is not None else DEMO_QUESTIONS):
        q = querier.parse(d["cypher"])
        anchored, anchors = set(), set()
        for i, n in enumerate(q.nodes):
            ids = []
            if "id" in n.props:
                ids = [n.props["id"]] if n.props["id"] in recs else []
            elif "name" in n.props:
                ids = [x for x in by_name.get(str(n.props["name"]), []) if not n.label or pharos_app.local(recs[x].get("category", "x:")) == n.label]
            if "id" in n.props or "name" in n.props:
                anchored.add(n.var)
                anchors.update(ids)
        if not anchored:
            plans.append(dict(id=d["id"], cypher=d["cypher"], anchors=set(), depth=None))
            continue
        dist = {v: 0 for v in anchored}
        for _ in range(len(q.nodes)):                                  # breadth-first over the pattern
            for r in q.rels:
                a, b = q.nodes[r.left].var, q.nodes[r.right].var
                if a in dist and dist.get(b, 99) > dist[a] + 1:
                    dist[b] = dist[a] + 1
                if b in dist and dist.get(a, 99) > dist[b] + 1:
                    dist[a] = dist[b] + 1
        need = max(min(dist.get(q.nodes[r.left].var, 99), dist.get(q.nodes[r.right].var, 99)) for r in q.rels) if q.rels else 0
        plans.append(dict(id=d["id"], cypher=d["cypher"], anchors=anchors, depth=None if need >= 99 else need + 1))
    return plans


# ------------------------------------------------------------------------------------------------ building the edges table
def fill_edges(con, edge_chunks, selected_hash, plans, log=print):
    """Takes the open sqlite connection (tables from SCHEMA), a function returning a fresh iterator of (layer, chunk frame with
    ALL edge columns, `layer` and a running `seq`), the sorted uint64 hash array of the edge ids that cards / predictions use, and
    demo_plan(). Streams the edges (up to four passes) and inserts what the module docstring lists. Returns {edges, rel_props,
    passes, frontier}. First occurrence of an id wins (INSERT OR IGNORE in stream order), as drop_duplicates(keep="first") does."""
    anchors = set().union(*[p["anchors"] for p in plans]) if plans else set()
    two = set().union(*[p["anchors"] for p in plans if p["depth"] == 2]) if plans else set()
    rel_cols, frontier, passes = set(), set(), 0

    def insert(layer_chunk):
        rows = [(r["id"], int(r["seq"]), r["subject"], r["predicate"], r["object"], r["layer"],
                 blob({k: v for k, v in r.items() if v != "" and k not in ("id", "seq")})) for r in layer_chunk.to_dict("records")]
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO edges(id, seq, subject, predicate, object, layer, props) VALUES (?,?,?,?,?,?,?)", rows)
        return con.total_changes - before

    def one_pass(pick, first=False):
        nonlocal frontier
        added = 0
        for layer, c in edge_chunks():
            if first:
                rel_cols.update(col for col in c.columns if col not in ("seq", "subject", "object") and (c[col] != "").any())
            sel = pick(c)
            if sel.any():
                part = c[sel]
                added += insert(part)
                if first and two:
                    t = part[part["subject"].isin(two) | part["object"].isin(two)]
                    frontier |= set(np.where(t["subject"].isin(two), t["object"], t["subject"]))
        con.commit()
        return added

    # pass 1: the edges cards and predictions use, and the anchors' own edges
    one_pass(lambda c: np.isin(hash_ids(c["id"]), selected_hash) | c["subject"].isin(anchors).to_numpy() | c["object"].isin(anchors).to_numpy(),
             first=True)
    passes += 1
    frontier -= anchors
    skipped = []
    if len(frontier) > MAX_FRONTIER:
        skipped = [p["id"] for p in plans if p["depth"] == 2]
        frontier = set()
    seen_pairs = np.zeros(0, np.uint64)
    # later passes: same-pair siblings of everything stored so far, and the neighbours' edges for the two-step queries
    for _ in range(3):
        s = pd.read_sql_query("SELECT subject, object FROM edges", con)
        pairs = np.unique(hash_pairs(s["subject"], s["object"]))
        new_pairs = np.setdiff1d(pairs, seen_pairs)
        if not len(new_pairs) and not frontier:
            break
        seen_pairs = pairs
        fr = frontier
        added = one_pass(lambda c: np.isin(hash_pairs(c["subject"], c["object"]), new_pairs) |
                         (c["subject"].isin(fr).to_numpy() | c["object"].isin(fr).to_numpy() if fr else False))
        passes += 1
        frontier = set()
        if not added:
            break
    return dict(rel_props=sorted(rel_cols), passes=passes, skipped=skipped)


def fill_verdicts(con, verdict_chunks):
    """verdict_chunks: iterator of frames (ALL verdict columns + `layer`) in layer order. Sets edges.verdict for edges stored in the
    table; the first verdict row of an id wins (drop_duplicates keep first), the stored dict is the live one (non-empty columns, strings,
    `layer` included)."""
    for c in verdict_chunks:
        rows = [(blob({k: v for k, v in r.items() if v != "" and k != "id"}), r["id"]) for r in c.to_dict("records")]
        con.executemany("UPDATE edges SET verdict = ? WHERE id = ? AND verdict IS NULL", rows)
    con.commit()


# ------------------------------------------------------------------------------------------------ the read side
class _NodeMap(Mapping):
    def __init__(self, ci):
        self.ci = ci

    def __getitem__(self, nid):
        got = self.ci._node(nid)
        if got is None:
            raise KeyError(nid)
        return got[0]

    def __contains__(self, nid):
        return self.ci.con.execute("SELECT 1 FROM nodes WHERE id = ?", (nid,)).fetchone() is not None

    def __iter__(self):
        for (i,) in self.ci.con.execute("SELECT id FROM nodes ORDER BY id"):
            yield i

    def __len__(self):
        return self.ci.con.execute("SELECT count(*) FROM nodes").fetchone()[0]


class _EdgeMap(Mapping):
    def __init__(self, g):
        self.g = g

    def __getitem__(self, eid):
        r = self.g.ci.con.execute("SELECT id, props FROM edges WHERE id = ?", (eid,)).fetchone()
        if r is None:
            raise KeyError(eid)
        return self.g._edge(r)

    def __contains__(self, eid):
        return self.g.ci.con.execute("SELECT 1 FROM edges WHERE id = ?", (eid,)).fetchone() is not None

    def __iter__(self):
        for (i,) in self.g.ci.con.execute("SELECT id FROM edges ORDER BY seq"):
            yield i

    def __len__(self):
        return self.g.ci.con.execute("SELECT count(*) FROM edges").fetchone()[0]


class _Adj(Mapping):
    """by_subject / by_object: node id -> list of edge dicts of the edges table (file order), [] for an unknown node."""

    def __init__(self, g, col):
        self.g, self.col = g, col

    def __getitem__(self, nid):
        rows = self.g.ci.con.execute(f"SELECT id, props FROM edges WHERE {self.col} = ? ORDER BY seq", (nid,)).fetchall()
        if not rows:
            raise KeyError(nid)
        return [self.g._edge(r) for r in rows]

    def get(self, nid, default=None):
        try:
            return self[nid]
        except KeyError:
            return [] if default is None else default

    def __contains__(self, nid):
        return self.g.ci.con.execute(f"SELECT 1 FROM edges WHERE {self.col} = ? LIMIT 1", (nid,)).fetchone() is not None

    def __iter__(self):
        for (i,) in self.g.ci.con.execute(f"SELECT DISTINCT {self.col} FROM edges"):
            yield i

    def __len__(self):
        return self.g.ci.con.execute(f"SELECT count(DISTINCT {self.col}) FROM edges").fetchone()[0]


# ------------------------------------------------------------------------------------------------ CC-38: overlay files
# Two optional files in the data root (TMKG_PHAROS_ROOT on the hosted app) refresh what the card index stores without
# re-uploading it: demo_overlay.json (the tested questions' answers, pharos_index.py --write-overlay) and value_sets.json
# (the relationship value sets the query validator checks literals against). Both are re-read when they change on disk.
OVERLAY_DEMO = "demo_overlay.json"
OVERLAY_VALUES = "value_sets.json"
_OVERLAY_CACHE = {}


def _read_json(path):
    p = Path(path)
    if not p.is_file():
        return None
    key = (str(p), p.stat().st_mtime, p.stat().st_size)
    if key not in _OVERLAY_CACHE:
        try:
            _OVERLAY_CACHE[key] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _OVERLAY_CACHE[key] = None
    return _OVERLAY_CACHE[key]


def load_overlay(folder):
    """Takes the data root (or None). Returns {demo: {question id: {cypher, result}}, value_sets: {prop: [values]} | None,
    made_at, files}: what the two overlay files hold, empty when they are missing or unreadable. demo_overlay.json =
    {made_at, results: {id: {cypher, result}}}; value_sets.json = {made_at, value_sets: {prop: [values]}}."""
    out = dict(demo={}, value_sets=None, made_at={}, files=[])
    if folder is None:
        return out
    d = _read_json(Path(folder) / OVERLAY_DEMO)
    if isinstance(d, dict) and isinstance(d.get("results"), dict):
        out["demo"] = {k: v for k, v in d["results"].items() if isinstance(v, dict) and "cypher" in v and "result" in v}
        out["made_at"]["demo"] = d.get("made_at")
        out["files"].append(OVERLAY_DEMO)
    v = _read_json(Path(folder) / OVERLAY_VALUES)
    if isinstance(v, dict) and isinstance(v.get("value_sets"), dict):
        out["value_sets"] = {k: sorted(map(str, x)) for k, x in v["value_sets"].items() if isinstance(x, list)}
        out["made_at"]["value_sets"] = v.get("made_at")
        out["files"].append(OVERLAY_VALUES)
    return out


def question_phrases(question):
    """Takes a question. Returns its 1-, 2- and 3-word phrases in lower case (3 characters or more, each once, in order of
    first appearance; a trailing 's is dropped: "ibuprofen's" -> ibuprofen): what find_entities looks up as names and symbols."""
    words = [re.sub(r"'s$", "", w) for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", question)]
    out = {}
    for i in range(len(words)):
        for j in (1, 2, 3):
            phrase = " ".join(words[i:i + j]).lower()
            if len(phrase) >= 3:
                out.setdefault(phrase, None)
    return list(out)


class IndexGraph(GraphViews):
    """A read-only, graph-shaped view of the card index: what the page's explain / node / demo / ask / subgraph calls need, answered
    from sqlite with a few indexed lookups, never from the edge files. See the module docstring for what is complete and what is not."""

    def __init__(self, ci, overlay_dir=None):
        self.ci = ci
        self.overlay_dir = overlay_dir                              # CC-38: the data root that may hold the overlay files
        self.nodes = _NodeMap(ci)
        self.edges = _EdgeMap(self)
        self.by_subject, self.by_object = _Adj(self, "subject"), _Adj(self, "object")
        self._predictions = object() if json.loads(ci.meta["predictions_available"]) else None
        self._model = None
        self.version = {"layers": json.loads(ci.meta["layers"]), "source_versions": json.loads(ci.meta["graph_status"]).get("source_versions", [])}

    def _edge(self, r):
        d = {"id": r["id"]}
        d.update(unblob(r["props"]))
        return d

    def label(self, nid):
        r = self.ci.con.execute("SELECT label FROM nodes WHERE id = ?", (nid,)).fetchone()
        return r["label"] if r else nid

    def verdict(self, eid):
        r = self.ci.con.execute("SELECT verdict FROM edges WHERE id = ?", (eid,)).fetchone()
        return unblob(r["verdict"]) if r and r["verdict"] is not None else None

    def same_pair(self, subject, obj, exclude=None):
        rows = self.ci.con.execute("SELECT id, props FROM edges WHERE subject = ? AND object = ? ORDER BY seq", (subject, obj)).fetchall()
        return [self._edge(r) for r in rows if r["id"] != exclude]

    def edges_between(self, a, b):
        """Edges with subject a and object b, file order (one lookup on the edges_pair index)."""
        return [self._edge(r) for r in self.ci.con.execute("SELECT id, props FROM edges WHERE subject = ? AND object = ? ORDER BY seq", (a, b))]

    def adjacent(self, nid, side, predicates):
        """Edges whose `side` ('subject' | 'object') is nid and whose predicate is one of the relationship types `predicates`
        (BioLink local names such as 'associated_with'); other edges of the node are not read or decoded."""
        col = "subject" if side == "subject" else "object"
        cond = " OR ".join("(predicate = ? OR predicate LIKE ?)" for _ in predicates)
        args = [x for t in predicates for x in (t, "%:" + t)]
        rows = self.ci.con.execute(f"SELECT id, props FROM edges WHERE {col} = ? AND ({cond}) ORDER BY seq", [nid] + args).fetchall()
        return [self._edge(r) for r in rows]

    def paths(self, a, b, max_paths=3):
        raise NotImplementedError("predictions are precomputed in the index")

    def predicted_links(self, nid, n=20, task=None):
        return self.ci.predicted_links(nid, n=n, task=task)

    def status(self):
        return self.ci.graph_status()

    def search(self, q, limit=20):
        return self.ci.search(q, limit)

    def card(self, nid, n_pred=10, per_kind=40, picture_edges=40):
        return self.ci.card(nid, n_pred, per_kind, picture_edges)

    def schema_card(self):
        sc = self.ci.schema_card()
        ov = load_overlay(self.overlay_dir)["value_sets"]                     # CC-38: an uploaded value_sets.json wins
        rel_enums = ov if ov is not None else json.loads(self.ci.meta.get("rel_values") or "{}")   # CC-31c; {} before it
        return dict(labels=sc["labels"], patterns=sc["patterns"], rel_props=json.loads(self.ci.meta["rel_props"]), enums={},
                    rel_enums=rel_enums)

    def suggest(self, q, n=3):
        """"Did you mean" (CardIndex.suggest)."""
        return self.ci.suggest(q, n)

    def candidates(self, label, props, ok):
        """The node ids a query pattern (label, props) can match, sorted, narrowed in SQL where a column exists (id, name, symbol,
        class) and then checked with `ok(id)`: replaces the scan of every node that run_local would make on the live graph."""
        where, args = [], []
        if label:
            where.append("category LIKE ?")
            args.append("%:" + label)
        for k in ("id", "name", "symbol"):
            if k in props:
                where.append(f"{k} = ?")
                args.append(str(props[k]))
        sql = "SELECT id FROM nodes" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
        return [r[0] for r in self.ci.con.execute(sql, args) if ok(r[0])]

    def find_entities(self, question, limit=10):
        """As PharosGraph.find_entities: identifiers (MONDO:, CHEMBL, NCBIGene:, GO:, HP:, UniProt) and exact matches of 1-3 word phrases
        against node names and symbols (3 characters or more)."""
        found = set()
        for pat in ID_PATTERNS:
            for m in re.findall(pat, question):
                for cand in (m, f"CHEMBL.COMPOUND:{m}" if m.startswith("CHEMBL") else None,
                             f"UniProtKB:{m}" if re.fullmatch(r"[OPQ][0-9][A-Z0-9]{3}[0-9]", m) else None):
                    if cand and cand in self.nodes:
                        found.add(cand)
        found.update(self.ids_named(question_phrases(question)))
        return sorted(found)[:limit]

    def ids_named(self, phrases, chunk=400):
        """Takes lower-case phrases. Returns the set of node ids whose name or symbol IS one of them, read with ONE query per
        `chunk` phrases (lname IN (...) OR lsymbol IN (...)). Before 11 Nov every phrase was its own query, and each one read
        the whole nodes table: a 12-word question took seconds and held up everyone else's cards (review finding SEC-01)."""
        phrases, found = sorted(set(phrases)), set()
        for i in range(0, len(phrases), chunk):
            part = phrases[i:i + chunk]
            marks = ",".join("?" * len(part))
            found.update(r[0] for r in self.ci.con.execute(
                f"SELECT id FROM nodes WHERE lname IN ({marks}) OR lsymbol IN ({marks})", part + part))
        return found

    def demo(self, ids=None):
        """The tested questions with the answers stored at build time (rows, columns, evidence, passed, note), as PharosGraph.demo."""
        out = []
        stored = {r["id"]: json.loads(r["result"]) for r in self.ci.con.execute("SELECT id, result FROM demo")}
        ov = load_overlay(self.overlay_dir)["demo"]
        for d in DEMO_QUESTIONS:
            if ids is not None and d["id"] not in ids:
                continue
            res = stored.get(d["id"])
            o = ov.get(d["id"])
            if o and o["cypher"] == d["cypher"]:                       # CC-38: only an answer to the query as it is now
                res = o["result"]
            if res is None:
                out.append(dict(d, rows=[], columns=[], evidence=[], passed=False,
                                note="not precomputed in the card index (" + self.ci.meta.get("demo_notes", "run pipelines/pharos_index.py") + ")"))
            else:
                out.append(pharos_app.display_result(dict(d, **res)))      # U11: decimals as the page prints them
        return out

    def compute_demo(self):
        """Runs every tested question on the edges table (live-graph code path, run_local) -> {id: result dict}; used by the build."""
        import querier
        out = {}
        for d in DEMO_QUESTIONS:
            try:
                res = querier.run_local(d["cypher"], self, max_steps=0, max_seconds=0, max_bindings=0, evidence_first=True)
                missing = [c for c in d["expect"]["columns"] if c not in res["columns"]]
                ok = len(res["rows"]) >= d["expect"]["min_rows"] and not missing
                out[d["id"]] = dict(rows=res["rows"], columns=res["columns"], evidence=res["evidence"], passed=ok,
                                    ordering=res.get("ordering"),
                                    note="" if ok else (f"columns missing: {missing}" if missing else "no rows on this graph"))
            except Exception as err:
                out[d["id"]] = dict(rows=[], columns=[], evidence=[], passed=False, note=f"query failed: {err}")
        return out
