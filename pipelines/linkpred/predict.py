"""
Top-N link predictions for the app (EXECUTION_PLAN.md Phase 4b): train the R-GCN on ALL edges of a graph (no
hold-out; the held-out evaluation is run.py's job) and write the best-scoring NEW pairs per node.

    python -m pipelines.linkpred.predict --graph G3 --tasks B --top 50 --epochs 300 --hidden 64 \
        --drop-levels text_co_occurrence --test-levels knowledge_assertion --positives assertions \
        --max-edges 2000000 --reverse-edges --learn-embeddings --num-bases 30 --neg-pool training

The graph is prepared exactly as run.py prepares it (same functions, same order): --drop-levels removes edges of
those knowledge levels, --positives moves the task type's other levels into the context relation, --max-edges /
--keep-whole cap every edge type by edge-id hash, --neg-pool picks training negatives. Use the frozen run's flags
(docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md section 5) so the app's predictions come from the measured set-up.
--test-levels is accepted for the same command line and recorded; it changes nothing here (no hold-out).

Reads:   outputs/<graph>/heterodata.pt (make_graphs.py)
Writes:  outputs/linkpred/predictions.tsv.gz  graph, task, subject, object, score, rank, by
         by = "subject" (rank among the subject's candidates) or "object" (rank among the object's candidates);
         pairs already present in the graph (any layer) are not written. The app (pipelines/pharos_app.py) reads
         this file and shows the paths behind each prediction.
         outputs/linkpred/predictions_settings.json  the flags and graph preparation counts behind the file.
         Pairs whose only edge was removed by --drop-levels (e.g. TIN-X co-occurrence) can come back as "new";
         the app's "known in" column still names the layers that hold them.
Scores are DistMult decoder values on the trained embeddings: comparable within one (graph, task), not across.
Not run in the cloud container (no torch); run it on the Mac or the GPU instance after Phase 5.

Meta-path predictions (the explainable predictor; --model metapath, no GNN training, loop/tasks/T-006.yaml):
    python -m pipelines.linkpred.predict --model metapath --graph G3 --tasks B --top-subject 10 --top-object 10 \
        --drop-levels text_co_occurrence --positives assertions --max-edges 2000000 [--objects curated|all] [--workers N]
The graph is prepared as above. A logistic regression on log(1 + path count) of the named meta-path shapes
(linkpred/metapaths.py, the model of metapath_aurocs) is fitted on a tenth of the task edges (taken out of the graph
first), then EVERY (gene, disease) pair without an edge is scored on the full graph; the best `top` per gene and per
disease are written (CC-28: --top-subject / --top-object, default 10 each, --top sets both; --objects curated = only
diseases with a knowledge_assertion task edge are candidates; the paths are found by --workers processes over chunks of
--explain-chunk pairs, with a flushed progress line per chunk; the settings JSON records workers, tops, the objects mode,
the candidate count and the measured ms/pair) to outputs/linkpred/predictions_metapath.tsv.gz:
    graph, task, subject, object, score, rank, by, model (= metapath), best_shape, n_shapes, paths
    paths = JSON list (up to 3, the shapes that contribute most): {shape, count, contribution, via (first node
    between the ends), nodes [ids along the path], edges [{id, subject, predicate, object}]} - the edge ids are rows of
    the graph's edges.tsv.gz, so every step of a prediction can be opened in the app's "why" drawer.
A pair none of the shapes connects is never written. Scores are comparable within one (graph, task) only.
"""
import gzip
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipelines"))

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def parse_args(argv=None):
    """Takes the command line (a list, or None for sys.argv). Returns the argparse namespace. The graph-preparation
    flags have run.py's names, choices and defaults, so the frozen run's command line can be reused."""
    import argparse
    p = argparse.ArgumentParser(description="Top-N link predictions on the whole graph (no hold-out)")
    p.add_argument("--graph", default="G3")
    p.add_argument("--model", default="gnn", choices=["gnn", "metapath"],
                   help="gnn: R-GCN + DistMult (predictions.tsv.gz); metapath: named-path model with the paths behind "
                        "every prediction (predictions_metapath.tsv.gz), no training of a network")
    p.add_argument("--max-len", type=int, default=3, help="metapath: longest shape in steps")
    p.add_argument("--max-shapes", type=int, default=200, help="metapath: at most this many shapes")
    p.add_argument("--batch", type=int, default=512, help="metapath: subjects scored per batch")
    p.add_argument("--tasks", default="A,B")
    p.add_argument("--top", type=int, default=None,
                   help="sets --top-subject and --top-object together (gnn: default 50)")
    p.add_argument("--top-subject", type=int, default=None, help="metapath: best new objects kept per subject (default 10)")
    p.add_argument("--top-object", type=int, default=None, help="metapath: best new subjects kept per object (default 10)")
    p.add_argument("--objects", default="curated", choices=["curated", "all"],
                   help="metapath: candidate objects = those with a knowledge_assertion task edge (curated) or every one")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 1) - 2),
                   help="metapath: processes that explain the pairs (default: CPUs - 2)")
    p.add_argument("--start-method", default="auto", choices=["auto", "fork", "spawn"],
                   help="metapath: how the explain workers start (auto = fork where available; spawn if a fork run hangs)")
    p.add_argument("--explain-chunk", type=int, default=2000, help="metapath: pairs per progress line / worker task")
    p.add_argument("--epochs", type=int, default=200)
    p.add_argument("--hidden", type=int, default=128)
    p.add_argument("--device", default="cpu")
    p.add_argument("--reverse-edges", action="store_true")
    p.add_argument("--learn-embeddings", action="store_true")
    p.add_argument("--num-bases", type=int, default=0)
    p.add_argument("--neg-pool", default="all", choices=["all", "training"],
                   help="training negatives from every object node (all) or objects with a task edge (training); "
                        "run.py's 'reference' needs a test set and has no meaning here")
    p.add_argument("--positives", default="all", choices=["all", "curated", "assertions"])
    p.add_argument("--drop-levels", default="")
    p.add_argument("--test-levels", default="", help="recorded only: there is no test set when predicting")
    p.add_argument("--max-edges", type=int, default=0)
    p.add_argument("--sample-fraction", type=float, default=0)
    p.add_argument("--keep-whole", default=None, help="default: run.py's KEEP_WHOLE (the gene -> protein bridge)")
    return p.parse_args(argv)


def tops(args, default=10):
    """Takes the parsed arguments. Returns (top per subject, top per object): --top-subject / --top-object, else --top
    (which sets both, the old flag), else `default`."""
    ts = args.top_subject if args.top_subject is not None else (args.top if args.top is not None else default)
    to = args.top_object if args.top_object is not None else (args.top if args.top is not None else default)
    return ts, to


def settings(args):
    """Takes the parsed arguments. Returns the dict written to predictions_settings.json (every flag, lists split;
    top_subject / top_object resolved)."""
    lst = lambda v: [x.strip() for x in (v or "").split(",") if x.strip()]
    d = {k: v for k, v in vars(args).items()}
    d["top_subject"], d["top_object"] = tops(args)
    for k in ("tasks", "drop_levels", "test_levels"):
        d[k] = lst(d[k])
    if d["keep_whole"] is not None:
        d["keep_whole"] = lst(d["keep_whole"])
    return d


def prepare_graph(data, task, drop_levels=(), positives="all", max_edges=0, sample_fraction=None, keep_whole=None,
                  log=print):
    """Takes a loaded graph, the task and run.py's preparation flags. Applies them in run.run_experiment's order:
    drop the knowledge levels, move the task type's non-positive levels into the context relation, cap every edge
    type. Returns (data, info) with info = {dropped: {edge type: n}, moved_to_context: n, levels_moved: [...]}."""
    from linkpred.run import (drop_knowledge_levels, positive_exclusions, split_text_mining, cap_edges,
                              find_task_edge_type, KEEP_WHOLE)
    info = {"dropped": {}, "moved_to_context": 0, "levels_moved": []}
    if drop_levels:
        info["dropped"] = drop_knowledge_levels(data, tuple(drop_levels))
        log(f"  edges of knowledge levels {list(drop_levels)} removed: {info['dropped']}")
    if positives != "all":
        et = find_task_edge_type(data, task)
        levels = positive_exclusions(data, et, positives)
        info["levels_moved"] = list(levels)
        info["moved_to_context"] = split_text_mining(data, et, levels)
        log(f"  {info['moved_to_context']:,} task edges of levels {list(levels)} moved to the context relation")
    data = cap_edges(data, max_edges or None, fraction=sample_fraction or None,
                     keep_whole=KEEP_WHOLE if keep_whole is None else tuple(keep_whole))
    return data, info


def train_full(data, task_et, hidden, out, epochs, lr, device, seed=0, reverse=False, learn=False, num_bases=None,
               neg_pool="all"):
    """Train on every edge of the graph. Returns (model, z) with z = {node type: embeddings} (no grad).
    reverse / learn / num_bases / neg_pool: the wiring options of run.py (--reverse-edges, --learn-embeddings,
    --num-bases, --neg-pool all|training); use the same ones the benchmark was run with, so the app's predictions
    come from the measured set-up."""
    from linkpred.models import LinkPredictor, RGCNEncoder, train_epoch, masked_edges
    from linkpred.run import training_negative_pool
    torch.manual_seed(seed)
    learn_nodes = {nt: data[nt].x for nt in data.node_types if hasattr(data[nt], "x")} if learn else None
    encoder = RGCNEncoder(data.metadata(), hidden=hidden, out=out, reverse=reverse, learn_nodes=learn_nodes,
                          num_bases=num_bases)
    model = LinkPredictor(encoder, len(data.edge_types), out).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    n = data[task_et].edge_index.size(1)
    full = torch.ones(n, dtype=torch.bool)
    pool = training_negative_pool(data, task_et, full, None, neg_pool)
    for _ in range(epochs):
        train_epoch(model, data, opt, task_et, full, device, neg_pool=pool)
    model.eval()
    x = {nt: data[nt].x.to(device) for nt in data.node_types if hasattr(data[nt], "x")}
    with torch.no_grad():
        z = model.encode(x, masked_edges(data, task_et, full, device))
    return model, z


def top_new_pairs(model, z, data, task_et, rel_idx, top, device, batch=512):
    """Scores every (subject, object) of the task's node types in batches and keeps, per subject and per object,
    the `top` best pairs that are not edges of the graph already. Returns a list of rows
    (subject index, object index, score, rank, by)."""
    src_type, _, dst_type = task_et
    zs, zd = z[src_type], z[dst_type]
    n_src, n_dst = zs.size(0), zd.size(0)
    known = {}                                                                 # subject index -> object indices
    for et in data.edge_types:
        if (et[0], et[2]) == (src_type, dst_type):
            pairs = data[et].edge_index.t().tolist()
        elif (et[0], et[2]) == (dst_type, src_type):
            pairs = [(o, s) for s, o in data[et].edge_index.t().tolist()]
        else:
            continue
        for a, o in pairs:
            known.setdefault(a, []).append(o)
    rel = model.decoder.rel(torch.tensor([rel_idx], device=device))            # [1, dim]
    col_best = torch.full((n_dst, top), -1e9, device=device)                   # running top-k per object
    col_idx = torch.full((n_dst, top), -1, dtype=torch.long, device=device)
    rows = []
    for start in range(0, n_src, batch):
        s = zs[start:start + batch]                                            # [B, dim]
        scores = (s * rel) @ zd.t()                                            # [B, n_dst]
        for i in range(s.size(0)):                                             # known pairs never count as new
            objs = known.get(start + i)
            if objs:
                scores[i, torch.tensor(objs, device=device)] = -1e9
        best, idx = scores.topk(min(top, n_dst), dim=1)
        for i in range(s.size(0)):
            for r, (sc, o) in enumerate(zip(best[i].tolist(), idx[i].tolist()), 1):
                if sc > -1e8:
                    rows.append((start + i, o, sc, r, "subject"))
        # per-object running top-k: merge this batch's column scores with the kept ones
        merged = torch.cat([col_best, scores.t()], dim=1)                      # [n_dst, top + B]
        merged_idx = torch.cat([col_idx, torch.arange(start, start + s.size(0), device=device).expand(n_dst, -1)], dim=1)
        col_best, pick = merged.topk(top, dim=1)
        col_idx = merged_idx.gather(1, pick)
    for o in range(n_dst):
        for r, (sc, a) in enumerate(zip(col_best[o].tolist(), col_idx[o].tolist()), 1):
            if sc > -1e8 and a >= 0:
                rows.append((a, o, sc, r, "object"))
    return rows


# ----------------------------------------------------------------------------------------- meta-path predictions
METAPATH_COLUMNS = ["graph", "task", "subject", "object", "score", "rank", "by", "model", "best_shape", "n_shapes",
                    "paths"]


def fit_metapath_model(edge_index_dict, node_counts, task_et, candidates, seed=0, max_len=3, max_paths=200,
                       fit_frac=0.1, bridge=None):
    """Takes the graph ({edge type: edge_index}), node counts, the task edge type, the candidate object indices for
    negatives, a seed and the shape limits (as metapaths.metapath_aurocs). Fits the logistic meta-path model the way
    metapath_aurocs does: a share `fit_frac` of the task edges is taken out of the graph, its pairs and an equal
    number of corrupted pairs are the training set. Returns (model, shapes, relations of the FULL graph) or None when
    there is no shape or no fit pair. The relations are those of the full graph (fit edges back in), the ones the
    candidates are scored on."""
    from linkpred import metapaths as mp
    bridge = mp.BRIDGE if bridge is None else bridge
    src_type, _, dst_type = task_et
    task = mp._as_np(edge_index_dict[task_et])
    candidates = np.asarray(sorted(set(mp._as_np(candidates))))
    fit_pos = task[:, mp.holdout_edges(task, fit_frac, seed)]
    pos_pairs = {(int(a), int(b)) for a, b in zip(fit_pos[0], fit_pos[1])}
    known = {(int(a), int(b)) for a, b in zip(task[0], task[1])}
    rel_fit = mp.typed_relations(mp._drop_pairs(edge_index_dict, src_type, dst_type, pos_pairs), node_counts)
    rel_full = mp.typed_relations(edge_index_dict, node_counts)
    if bridge:
        rel_fit, rel_full = mp.project_bridge(rel_fit, bridge), mp.project_bridge(rel_full, bridge)
    shapes = mp.enumerate_metapaths(rel_fit, src_type, dst_type, max_len=max_len, max_paths=max_paths)
    if not shapes or fit_pos.shape[1] == 0 or len(candidates) == 0:
        return None
    neg_dst = []
    for a, b in zip(fit_pos[0], fit_pos[1]):
        for attempt in range(20):
            c = int(candidates[mp._unit(seed, f"mpneg:{attempt}:{a}:{b}") % len(candidates)])
            if (int(a), c) not in known:
                break
        neg_dst.append(c)
    X = mp.metapath_counts(rel_fit, shapes, np.r_[fit_pos[0], fit_pos[0]], np.r_[fit_pos[1], neg_dst])
    y = np.r_[np.ones(fit_pos.shape[1]), np.zeros(fit_pos.shape[1])]
    return mp.fit_logistic(np.log1p(X), y), shapes, rel_full


def _score_batch(rel, shapes, coef, const, rows, n_dst):
    """Dense scores [len(rows), n_dst] of every pair of the subject rows, and a bool matrix of the pairs that at
    least one shape connects. score = const + sum_j coef_j * log1p(count_j): the model's linear score with the
    standardisation folded into coef / const, so a pair with count 0 on every shape gets exactly `const`."""
    score = np.full((len(rows), n_dst), const, dtype=np.float64)
    hit = np.zeros((len(rows), n_dst), dtype=bool)
    for shape, cj in zip(shapes, coef):
        R = rel[shape[0]][rows]
        for k in shape[1:]:
            R = R @ rel[k]
        R = R.tocoo()
        if R.nnz:
            np.add.at(score, (R.row, R.col), cj * np.log1p(R.data))
            hit[R.row, R.col] = True
    return score, hit


def score_metapath_pairs(rel, shapes, model, known, n_src, n_dst, top, batch=512, top_object=None, objects=None):
    """Takes the full-graph relations, the shapes, the fitted model, {subject index: set of object indices already
    linked}, the node counts of the two task types, `top` (best objects kept per subject), a batch size, `top_object`
    (best subjects kept per object; default = top) and `objects` (candidate object indices; None = every object).
    Scores every (subject, candidate object) pair that some shape connects and has no edge, and keeps the best per
    subject and per object. Returns a list of (subject index, object index, score, rank, by), by = 'subject' | 'object'."""
    top_object = top if top_object is None else top_object
    blocked = None
    if objects is not None:
        blocked = np.ones(n_dst, dtype=bool)
        blocked[np.asarray(list(objects), dtype=np.int64)] = False
    w, b, mu, sd = model
    coef = w / sd
    const = float(b - (w * mu / sd).sum())
    col_best = np.full((n_dst, top_object), -np.inf)
    col_idx = np.full((n_dst, top_object), -1, dtype=np.int64)
    rows_out = []
    for start in range(0, n_src, batch):
        rows = np.arange(start, min(start + batch, n_src))
        score, hit = _score_batch(rel, shapes, coef, const, rows, n_dst)
        score[~hit] = -np.inf
        if blocked is not None:
            score[:, blocked] = -np.inf
        for i, r in enumerate(rows):
            objs = known.get(int(r))
            if objs:
                score[i, list(objs)] = -np.inf
        k = min(top, n_dst)
        part = np.argpartition(-score, k - 1, axis=1)[:, :k] if n_dst > k else np.tile(np.arange(n_dst), (len(rows), 1))
        for i, r in enumerate(rows):
            cand = part[i][np.isfinite(score[i, part[i]])]
            order = cand[np.lexsort((cand, -score[i, cand]))]            # best first, ties by object index
            for rank, o in enumerate(order, 1):
                rows_out.append((int(r), int(o), float(score[i, o]), rank, "subject"))
        merged = np.concatenate([col_best, score.T], axis=1)
        merged_idx = np.concatenate([col_idx, np.broadcast_to(rows, (n_dst, len(rows)))], axis=1)
        pick = np.argsort(-merged, axis=1, kind="stable")[:, :top_object]
        col_best = np.take_along_axis(merged, pick, axis=1)
        col_idx = np.take_along_axis(merged_idx, pick, axis=1)
    for o in range(n_dst):
        for rank, (sc, a) in enumerate(zip(col_best[o], col_idx[o]), 1):
            if np.isfinite(sc) and a >= 0:
                rows_out.append((int(a), o, float(sc), rank, "object"))
    return rows_out


def _edge_lookup(edge_index_dict, edge_ids):
    """{edge type: {(subject index, object index): edge id}} from the edge indices and their edge-id lists."""
    from linkpred.metapaths import _as_np
    out = {}
    for et, ei in edge_index_dict.items():
        ids = edge_ids.get(et)
        if ids is None:
            continue
        ei = _as_np(ei)
        out[et] = {(int(a), int(b)): ids[i] for i, (a, b) in enumerate(zip(ei[0], ei[1]))}
    return out


def _base(key, i, j):
    """Takes a step key and the node indices it was walked between. Returns (the stored edge type, subject index,
    object index) of the edge underneath: a reverse step '^-1' is the stored edge read backwards."""
    from linkpred.metapaths import REVERSE
    if key[1].endswith(REVERSE):
        return (key[2], key[1][:-len(REVERSE)], key[0]), j, i
    return tuple(key), i, j


def _record(lookup, node_ids, key, i, j):
    """One edge record {id, subject, predicate, object} for the step `key` from node i to node j, or None when the
    graph has no edge-id list for that edge type."""
    et, s, o = _base(key, i, j)
    eid = lookup.get(et, {}).get((s, o))
    if eid is None:
        return None
    return {"id": eid, "subject": node_ids[et[0]][s], "predicate": et[1], "object": node_ids[et[2]][o]}


def find_path(rel, rel0, shape, src, dst, lookup, node_ids, bridge=None):
    """Takes the (projected) relations, the unprojected ones, one shape, the two ends and the edge-id lookup + node ids.
    Returns {'nodes': [node ids along the path], 'edges': [edge records]} for the first path of that shape from src to
    dst that visits no node twice (smallest node indices first, so the same input gives the same path), or None
    when there is none (the shape's count may come from walks that revisit a node). A step the
    gene -> protein projection merged (a relation named r@product) is expanded into its real edges, bridge edges
    included, so each record is a row of the graph."""
    from linkpred.metapaths import BRIDGE, VIA
    bridge = BRIDGE if bridge is None else bridge
    mats = [rel[k] for k in shape]
    reach = [None] * (len(shape) + 1)                       # reach[t]: nodes at position t that can still reach dst
    ind = np.zeros(mats[-1].shape[1])
    ind[dst] = 1
    reach[len(shape)] = ind > 0
    for t in range(len(shape) - 1, -1, -1):
        reach[t] = (mats[t] @ reach[t + 1].astype(float)) > 0
    if not reach[0][src]:
        return None
    types = [k[0] for k in shape] + [shape[-1][2]]

    def walk(t, at, seen):
        """First path (smallest indices first) from position t at node `at` to dst that visits no node twice."""
        if t == len(mats):
            return [at] if at == dst else None
        M = mats[t]
        for c in sorted(int(x) for x in M.indices[M.indptr[at]:M.indptr[at + 1]]):
            if reach[t + 1][c] and (types[t + 1], c) not in seen:
                rest = walk(t + 1, c, seen | {(types[t + 1], c)})
                if rest:
                    return [at] + rest
        return None

    nodes_idx = walk(0, src, {(types[0], src)})
    if not nodes_idx:
        return None
    nodes, edges = [], []
    g_type, _, p_type = bridge
    B = rel0.get(bridge)
    for t, key in enumerate(shape):
        i, j = nodes_idx[t], nodes_idx[t + 1]
        a, r, b = key
        if r.endswith(VIA):
            r0 = r[:-len(VIA)]
            both = a == g_type and b == g_type
            orig = (p_type, r0, p_type) if both else ((p_type, r0, b) if a == g_type else (a, r0, p_type))
            M0 = rel0[orig]
            if both:
                P1, P2 = B[i].indices, B[j].indices
                pick = next(((p1, p2) for p1 in P1 for p2 in P2 if M0[p1, p2]), None)
                if pick is None:
                    return None
                recs = [_record(lookup, node_ids, bridge, i, pick[0]), _record(lookup, node_ids, orig, pick[0], pick[1]),
                        _record(lookup, node_ids, (p_type, bridge[1] + "^-1", g_type), pick[1], j)]
                mid = [node_ids[p_type][pick[0]], node_ids[p_type][pick[1]]]
            elif a == g_type:
                p = next((p for p in B[i].indices if M0[p, j]), None)
                if p is None:
                    return None
                recs = [_record(lookup, node_ids, bridge, i, p), _record(lookup, node_ids, orig, p, j)]
                mid = [node_ids[p_type][p]]
            else:
                p = next((p for p in B[j].indices if M0[i, p]), None)
                if p is None:
                    return None
                recs = [_record(lookup, node_ids, orig, i, p), _record(lookup, node_ids, (p_type, bridge[1] + "^-1", g_type), p, j)]
                mid = [node_ids[p_type][p]]
            nodes += [node_ids[a][i]] + mid
            edges += recs
        else:
            nodes.append(node_ids[a][i])
            edges.append(_record(lookup, node_ids, key, i, j))
    nodes.append(node_ids[shape[-1][2]][nodes_idx[-1]])
    if any(e is None for e in edges):
        return None
    return {"nodes": nodes, "edges": edges}


def explain_pairs(rel, rel0, shapes, model, pairs, lookup, node_ids, top_shapes=3, bridge=None, max_tries=25):
    """Takes the relations, the shapes, the fitted model, a list of (subject index, object index) and the edge
    lookup / node ids. Returns a list (same order) of dicts {best_shape, n_shapes, paths}: the shapes ranked by their
    contribution to the pair's score (coefficient x log1p(count), only shapes with a path), and for the best
    `top_shapes` of them that have a path visiting no node twice (at most `max_tries` shapes are tried) one real path
    with its edge records."""
    from linkpred.metapaths import metapath_counts, shape_name
    w, b, mu, sd = model
    coef = w / sd
    if not pairs:
        return []
    X = metapath_counts(rel, shapes, [p[0] for p in pairs], [p[1] for p in pairs])
    out = []
    for (a, o), x in zip(pairs, X):
        contrib = coef * np.log1p(x)
        order = [j for j in np.lexsort((np.arange(len(x)), -contrib)) if x[j] > 0]
        paths = []
        for j in order[:max_tries]:                       # a shape's count can come from walks that revisit a node: skip it
            if len(paths) == top_shapes:
                break
            path = find_path(rel, rel0, shapes[j], a, o, lookup, node_ids, bridge)
            if path:
                paths.append({"shape": shape_name(shapes[j]), "count": int(x[j]), "contribution": round(float(contrib[j]), 4),
                              "via": path["nodes"][1], **path})
        out.append({"best_shape": shape_name(shapes[order[0]]) if order else "", "n_shapes": len(order), "paths": paths})
    return out


_EXPLAIN = {}                 # the explain_pairs arguments a worker process uses (set before the pool forks / by its initializer)


def _explain_init(state):
    _EXPLAIN.clear()
    _EXPLAIN.update(state)


def _explain_chunk(pairs):
    return explain_pairs(pairs=pairs, **_EXPLAIN)


def progress_line(done, total, elapsed):
    """Takes pairs explained so far, all pairs and the seconds spent. Returns e.g.
    'explained 120,000 / 1,300,000 pairs, 41 ms/pair, ETA 13 min' (ms/pair = wall time per pair with all workers)."""
    ms = 1000.0 * elapsed / done if done else 0.0
    left = (total - done) * ms / 1000.0
    eta = f"{left / 60:.0f} min" if left < 7200 else f"{left / 3600:.1f} h"
    return f"explained {done:,} / {total:,} pairs, {ms:.0f} ms/pair, ETA {eta}"


def explain_pairs_parallel(rel, rel0, shapes, model, pairs, lookup, node_ids, top_shapes=3, bridge=None, workers=1,
                           chunk=2000, log=print, start_method="auto"):
    """explain_pairs over chunks of `pairs` in a pool of `workers` processes (1 = in this process). Output rows are
    the same, in the same order, as one explain_pairs call. After every chunk a progress_line is logged (flushed).
    Returns (list of explain dicts, stats {workers, chunk, pairs, seconds, ms_per_pair, start_method})."""
    state = dict(rel=rel, rel0=rel0, shapes=shapes, model=model, lookup=lookup, node_ids=node_ids,
                 top_shapes=top_shapes, bridge=bridge)
    chunks = [pairs[i:i + chunk] for i in range(0, len(pairs), max(1, chunk))]
    out, done, t0 = [], 0, time.time()
    workers = max(1, min(int(workers), len(chunks) or 1))
    method = "in-process"

    def step(res, n):
        nonlocal done
        out.extend(res)
        done += n
        log(progress_line(done, len(pairs), time.time() - t0))
        sys.stdout.flush()

    if workers == 1:
        for c in chunks:
            step(explain_pairs(rel, rel0, shapes, model, c, lookup, node_ids, top_shapes, bridge), len(c))
    else:
        # fork shares the graph with the workers without copying it; spawn (Windows) sends it once per worker
        method = start_method
        if method == "auto" or method not in multiprocessing.get_all_start_methods():
            method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
        ctx = multiprocessing.get_context(method)
        if method == "fork":
            _explain_init(state)
            pool = ctx.Pool(workers)
        else:
            pool = ctx.Pool(workers, initializer=_explain_init, initargs=(state,))
        try:
            for c, res in zip(chunks, pool.imap(_explain_chunk, chunks)):      # imap keeps the chunk order
                step(res, len(c))
        finally:
            pool.close()
            pool.join()
            _EXPLAIN.clear()
    secs = time.time() - t0
    return out, dict(workers=workers, chunk=chunk, pairs=len(pairs), seconds=round(secs, 1), start_method=method,
                     ms_per_pair=round(1000.0 * secs / len(pairs), 2) if pairs else None)


def curated_objects(edge_index, levels, level="knowledge_assertion"):
    """Takes the task edge index [2, n] and its per-edge knowledge levels (None when the graph has none). Returns the
    sorted object indices with at least one task edge of `level` (no levels: every object of a task edge)."""
    from linkpred.metapaths import _as_np
    ei = _as_np(edge_index)
    if levels is None:
        return np.unique(ei[1])
    keep = np.fromiter((lv == level for lv in levels), dtype=bool, count=ei.shape[1])
    return np.unique(ei[1][keep])


def metapath_predictions(edge_index_dict, node_counts, task_et, node_ids, edge_ids, top=50, graph="", task="",
                         seed=0, max_len=3, max_paths=200, fit_frac=0.1, batch=512, top_shapes=3, top_object=None,
                         objects=None, workers=1, chunk=2000, log=print, stats=None, start_method="auto"):
    """Top-`top` new links per subject and top-`top_object` (default = top) per object with the paths behind them,
    from a meta-path model fitted on the graph (see the module docstring). Takes {edge type: edge_index}, {node type:
    count}, the task edge type, {node type: list of node ids}, {edge type: list of edge ids (same order as the edge
    index)}; `objects` = candidate object indices (None = all); `workers` / `chunk` = the explain step's pool. Returns a
    list of row dicts with METAPATH_COLUMNS (score as a 4-decimal string, rank an int, paths a JSON string); [] when the
    graph has no shape from the subject to the object type. Deterministic (the same rows for any workers / chunk).
    A dict passed as `stats` receives the explain step's stats (explain_pairs_parallel) and the candidate count."""
    from linkpred import metapaths as mp
    src_type, _, dst_type = task_et
    n_src, n_dst = int(node_counts[src_type]), int(node_counts[dst_type])
    task_ei = mp._as_np(edge_index_dict[task_et])
    fitted = fit_metapath_model(edge_index_dict, node_counts, task_et, np.unique(task_ei[1]), seed, max_len, max_paths,
                                fit_frac)
    if fitted is None:
        return []
    model, shapes, rel = fitted
    known = {}
    for et, ei in edge_index_dict.items():
        ei = mp._as_np(ei)
        if (et[0], et[2]) == (src_type, dst_type):
            pairs = zip(ei[0].tolist(), ei[1].tolist())
        elif (et[0], et[2]) == (dst_type, src_type):
            pairs = zip(ei[1].tolist(), ei[0].tolist())
        else:
            continue
        for a, o in pairs:
            known.setdefault(a, set()).add(o)
    found = score_metapath_pairs(rel, shapes, model, known, n_src, n_dst, top, batch, top_object, objects)
    found.sort(key=lambda r: (r[0], r[1], r[4]))
    uniq = sorted({(r[0], r[1]) for r in found})
    log(f"  {len(found):,} prediction rows, {len(uniq):,} distinct pairs to explain with {workers} worker(s)")
    rel0 = mp.typed_relations(edge_index_dict, node_counts)
    expl, st = explain_pairs_parallel(rel, rel0, shapes, model, uniq, _edge_lookup(edge_index_dict, edge_ids), node_ids,
                                      top_shapes, workers=workers, chunk=chunk, log=log, start_method=start_method)
    if stats is not None:
        stats.update(st, candidate_objects=n_dst if objects is None else len(objects))
    why = dict(zip(uniq, expl))
    rows = []
    for a, o, sc, rank, by in sorted(found, key=lambda r: (node_ids[src_type][r[0]], node_ids[dst_type][r[1]], r[4])):
        w_ = why[(a, o)]
        rows.append({"graph": graph, "task": task, "subject": node_ids[src_type][a], "object": node_ids[dst_type][o],
                     "score": f"{sc:.4f}", "rank": rank, "by": by, "model": "metapath", "best_shape": w_["best_shape"],
                     "n_shapes": w_["n_shapes"], "paths": json.dumps(w_["paths"], separators=(",", ":"))})
    return rows


def write_metapath_predictions(rows, path, settings_dict=None):
    """Takes the row dicts of metapath_predictions and a path. Writes the gzipped TSV (METAPATH_COLUMNS, deterministic
    bytes) and, next to it, <name>_settings.json when settings are given. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        gz.write(("\t".join(METAPATH_COLUMNS) + "\n").encode())
        for r in rows:
            gz.write(("\t".join(str(r[c]) for c in METAPATH_COLUMNS) + "\n").encode())
    if settings_dict is not None:
        path.with_name(path.name.replace(".tsv.gz", "_settings.json")).write_text(json.dumps(settings_dict, indent=2))
    return path


def main_metapath(args):
    """The --model metapath path of main(): loads the graph with run.py's loader, prepares it, exports the rows."""
    from linkpred.run import load_graph, find_task_edge_type
    st = settings(args)
    out_rows, prep, explain = [], {}, {}
    for task in st["tasks"]:
        print(f"{args.graph} task {task}: preparing the graph", flush=True)
        data, prep[task] = prepare_graph(load_graph(args.graph), task, st["drop_levels"], args.positives,
                                         args.max_edges, args.sample_fraction, st["keep_whole"])
        et = find_task_edge_type(data, task)
        objs = None
        if args.objects == "curated":
            objs = curated_objects(data[et].edge_index.numpy(), getattr(data[et], "edge_knowledge_level", None))
            print(f"  candidate objects (curated: a knowledge_assertion task edge): {len(objs):,} of "
                  f"{int(data[et[2]].num_nodes):,}", flush=True)
        edge_index = {k: data[k].edge_index.numpy() for k in data.edge_types}
        counts = {nt: int(data[nt].num_nodes) for nt in data.node_types}
        ids = {nt: list(data[nt].node_id) for nt in data.node_types}
        eids = {k: list(data[k].edge_id) for k in data.edge_types if hasattr(data[k], "edge_id")}
        if et not in eids:
            sys.exit(f"{args.graph}: edge type {et} has no edge_id list; rebuild the graph with make_graphs.py")
        explain[task] = {"objects": args.objects}
        out_rows += metapath_predictions(edge_index, counts, et, ids, eids, top=st["top_subject"], graph=args.graph,
                                         task=task, max_len=args.max_len, max_paths=args.max_shapes, batch=args.batch,
                                         top_object=st["top_object"], objects=objs, workers=args.workers,
                                         chunk=args.explain_chunk, log=lambda m: print(m, flush=True), stats=explain[task],
                                         start_method=args.start_method)
        print(f"{args.graph} task {task}: {len(out_rows):,} prediction rows so far", flush=True)
    out = ROOT / "outputs" / "linkpred" / "predictions_metapath.tsv.gz"
    write_metapath_predictions(out_rows, out, {"settings": st, "explain": explain, "preparation": {
        t: {**i, "dropped": {"|".join(k) if isinstance(k, tuple) else str(k): v for k, v in i["dropped"].items()}}
        for t, i in prep.items()}, "rows": len(out_rows)})
    print(f"wrote {out} ({len(out_rows):,} rows)")


def main(argv=None):
    args = parse_args(argv)
    if not _HAS_TORCH:
        sys.exit("torch is required: pip install torch torch_geometric")
    if args.model == "metapath":
        return main_metapath(args)
    from linkpred.run import load_graph, init_features, find_task_edge_type
    st = settings(args)
    out_rows, prep = [], {}
    for task in st["tasks"]:
        print(f"{args.graph} task {task}: preparing the graph")
        data, prep[task] = prepare_graph(load_graph(args.graph), task, st["drop_levels"], args.positives,
                                         args.max_edges, args.sample_fraction, st["keep_whole"])
        data = init_features(data, dim=args.hidden)
        et = find_task_edge_type(data, task)
        rel_idx = list(data.edge_types).index(et)
        model, z = train_full(data, et, args.hidden, 64, args.epochs, 0.01, args.device, reverse=args.reverse_edges,
                              learn=args.learn_embeddings, num_bases=args.num_bases or None, neg_pool=args.neg_pool)
        src_ids, dst_ids = data[et[0]].node_id, data[et[2]].node_id
        for a, o, sc, r, by in top_new_pairs(model, z, data, et, rel_idx, args.top or 50, args.device):
            out_rows.append((args.graph, task, src_ids[a], dst_ids[o], f"{sc:.4f}", r, by))
        print(f"{args.graph} task {task}: {len(out_rows):,} prediction rows so far")
    out = ROOT / "outputs" / "linkpred" / "predictions.tsv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt") as f:
        f.write("graph\ttask\tsubject\tobject\tscore\trank\tby\n")
        for row in sorted(out_rows):
            f.write("\t".join(map(str, row)) + "\n")
    (out.parent / "predictions_settings.json").write_text(json.dumps(
        {"settings": st, "preparation": {t: {**i, "dropped": {"|".join(k) if isinstance(k, tuple) else str(k): v
                                                             for k, v in i["dropped"].items()}}
                                         for t, i in prep.items()}, "rows": len(out_rows)}, indent=2))
    print(f"wrote {out} ({len(out_rows):,} rows) and predictions_settings.json")


if __name__ == "__main__":
    main()
