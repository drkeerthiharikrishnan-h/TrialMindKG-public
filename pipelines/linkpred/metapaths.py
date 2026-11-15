"""
Meta-path counts for the link-prediction benchmark (docs/ENRICHMENT_DIAGNOSIS_PLAN.md, wave H "multi-hop reasoning"
and A4): for a (gene, disease) pair, how many paths of each named SHAPE join them in the training graph, e.g.
    Gene -interacts_with-> Gene -gene_associated_with_condition-> Disease          (a partner gene has the disease)
    Gene -has_gene_product-> Protein <-has_gene_product- Gene -...-> Disease       (2 and 3 hops)
Every edge type is walked in both directions (a reverse step is written with a '^-1' suffix), so the protein side of
the graph can reach a gene. Counts come from sparse matrix products, cheap for any number of pairs.

  typed_relations(edge_index_dict, node_counts)  -> {relation key: CSR matrix}, forward and reverse steps.
  enumerate_metapaths(relations, src, dst, max_len)  -> the shapes (tuples of relation keys) from src to dst,
                         2 to max_len steps (a 1-step shape is the pair's own edge: never a feature).
  metapath_counts(relations, shapes, src_idx, dst_idx)  -> [pairs, shapes] float array of path counts.
  metapath_aurocs(...)   the "meta-path-only model": logistic regression on log(1 + count) trained on 10% of the
                         training task edges (removed from the graph first, so their own edges cannot score them),
                         then scored on the test pairs and their negatives. Returns auroc_metapath, the share of
                         test pairs with at least one path, and the best single shapes. If G2's number is higher
                         than G0's, the enrichment carries signal about the held-out links, whatever the GNN does.

numpy / scipy only (no torch), unit-tested in the cloud; run.py calls metapath_aurocs with --metapaths.
"""
import hashlib

import numpy as np
import scipy.sparse as sp

from linkpred.evaluate import auroc

REVERSE = "^-1"


def _as_np(x):
    return x.detach().cpu().numpy() if hasattr(x, "detach") else np.asarray(x)


def rel_key(et, reverse=False):
    """Takes an edge type (subject type, predicate, object type). Returns the step's key: the same tuple, or for the
    reverse step (object type, predicate + '^-1', subject type)."""
    return (et[2], et[1] + REVERSE, et[0]) if reverse else tuple(et)


def typed_relations(edge_index_dict, node_counts, min_edges=1):
    """Takes {edge type: edge_index [2, E]} and {node type: count}. Returns {step key: binary CSR [n_from, n_to]}
    for every edge type with at least min_edges edges, in both directions (duplicates count once)."""
    out = {}
    for et, ei in edge_index_dict.items():
        ei = _as_np(ei)
        if ei.shape[1] < max(1, min_edges):
            continue
        n_s, n_o = int(node_counts[et[0]]), int(node_counts[et[2]])
        M = sp.coo_matrix((np.ones(ei.shape[1]), (ei[0], ei[1])), shape=(n_s, n_o)).tocsr()
        M.data[:] = 1.0
        out[rel_key(et)] = M
        out[rel_key(et, reverse=True)] = M.T.tocsr()
    return out


BRIDGE = ("Gene", "has_gene_product", "Protein")
VIA = "@product"


def project_bridge(relations, bridge=BRIDGE):
    """Takes the step matrices and the gene -> protein bridge. Returns new step matrices in which every relation that
    touches a Protein is restated at the gene level through the bridge B (path counts carried over):
      Protein -r-> X     becomes  Gene -r@product-> X      (B @ M)
      X -r-> Protein     becomes  X -r@product-> Gene      (M @ B^T)
      Protein -r-> Protein becomes Gene -r@product-> Gene  (B @ M @ B^T)
    and the protein-level relations and the bridge itself are left out. A gene's interaction partner, its GO terms,
    pathways and the ligands of its protein are then 1 step from the gene, so 'an interaction partner has the
    disease' is a 2-step shape instead of 4 (beyond the 3-step limit). Returns `relations` unchanged when the bridge
    is missing or empty."""
    B = relations.get(bridge)
    if B is None or B.nnz == 0:
        return relations
    gene, _, prot = bridge
    Bt = B.T.tocsr()
    rev = rel_key(bridge, reverse=True)
    out = {}
    for k, M in relations.items():
        if k in (bridge, rev):
            continue
        a, r, b = k
        if a == prot and b == prot:
            out[(gene, r + VIA, gene)] = (B @ M @ Bt).tocsr()
        elif a == prot:
            out[(gene, r + VIA, b)] = (B @ M).tocsr()
        elif b == prot:
            out[(a, r + VIA, gene)] = (M @ Bt).tocsr()
        else:
            out[k] = M
    return {k: M for k, M in out.items() if M.nnz}


def enumerate_metapaths(relations, src_type, dst_type, max_len=3, max_paths=200):
    """Takes the step keys (from typed_relations), the task's node types, the longest shape and a cap. Returns a
    sorted list of shapes (tuples of step keys) that start at src_type and end at dst_type, 2..max_len steps.
    A step back over the same edge type is allowed (Gene -assoc-> Disease <-assoc- Gene -assoc-> Disease = "another
    gene of a disease this gene has also has the target disease"); a walk that returns to the start node needs
    the pair's own edge, which is never in the graph for a scored pair (test pairs and fit pairs are removed).
    Order: shorter shapes first, then the shapes whose sparsest step has the most edges (a shape through a
    20-edge relation is almost always zero); at most max_paths (None = all)."""
    by_start = {}
    for k in sorted(relations):
        by_start.setdefault(k[0], []).append(k)
    found, frontier = [], [((), src_type)]
    for length in range(1, max_len + 1):
        nxt = []
        for path, at in frontier:
            for k in by_start.get(at, []):
                p = path + (k,)
                if length >= 2 and k[2] == dst_type:
                    found.append(p)
                if length < max_len:
                    nxt.append((p, k[2]))
        frontier = nxt
    found.sort(key=lambda p: (len(p), -min(relations[k].nnz for k in p), p))
    return found if max_paths is None else found[:max_paths]


def shape_name(shape):
    """Readable form of a shape: 'Gene -interacts_with-> Gene -gene_associated_with_condition-> Disease'."""
    parts = [shape[0][0]]
    for a, r, b in shape:
        parts.append(f"<-{r[:-len(REVERSE)]}-" if r.endswith(REVERSE) else f"-{r}->")
        parts.append(b)
    return " ".join(parts)


def metapath_counts(relations, shapes, src_idx, dst_idx, batch=2000):
    """Takes the step matrices, the shapes, subject indices and object indices (same length, indices within their
    own node types). Returns a float array [pairs, shapes]: the number of paths of each shape from the subject to
    the object, computed in batches of rows (the last step is taken as a column of the last matrix)."""
    src_idx, dst_idx = _as_np(src_idx).astype(int), _as_np(dst_idx).astype(int)
    out = np.zeros((len(src_idx), len(shapes)))
    for j, shape in enumerate(shapes):
        mats = [relations[k] for k in shape]
        last_t = mats[-1].T.tocsr()
        for start in range(0, len(src_idx), batch):
            s, d = src_idx[start:start + batch], dst_idx[start:start + batch]
            R = mats[0][s]
            for M in mats[1:-1]:
                R = R @ M
            out[start:start + batch, j] = np.asarray(R.multiply(last_t[d]).sum(1)).ravel()
    return out


def fit_logistic(X, y, l2=1.0, iters=50):
    """Takes features [n, f] and 0/1 labels. Returns (w, b, mean, scale): L2-regularised logistic regression fitted
    by Newton steps on standardised features (columns with no spread get scale 1). Small f, so this is exact."""
    X, y = np.asarray(X, float), np.asarray(y, float)
    mu = X.mean(0)
    sd = X.std(0)
    sd[sd == 0] = 1.0
    Z = np.c_[(X - mu) / sd, np.ones(len(X))]
    w = np.zeros(Z.shape[1])
    reg = np.full(Z.shape[1], l2)
    reg[-1] = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w, -30, 30)))
        g = Z.T @ (p - y) + reg * w
        H = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg + 1e-9)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w[:-1], w[-1], mu, sd


def predict_logistic(model, X):
    """Takes the fitted (w, b, mean, scale) and features. Returns the linear scores (higher = more likely a link)."""
    w, b, mu, sd = model
    return ((np.asarray(X, float) - mu) / sd) @ w + b


def _unit(seed, text):
    return int.from_bytes(hashlib.sha256(f"{seed}:{text}".encode()).digest()[:8], "little")


def holdout_edges(task_ei, frac, seed):
    """Takes the training task edges [2, E], a share and a seed. Returns a bool mask of the edges kept aside to fit
    the meta-path model: chosen by a hash of (seed, subject index, object index), at least one edge when E > 1."""
    task_ei = _as_np(task_ei)
    u = np.array([_unit(seed, f"mp:{s}:{o}") % 1_000_000 / 1e6 for s, o in zip(task_ei[0], task_ei[1])])
    mask = u < frac
    if task_ei.shape[1] > 1 and not mask.any():
        mask[int(np.argmin(u))] = True
    return mask


def _drop_pairs(edge_index_dict, src_type, dst_type, pairs):
    """Copy of the edge dict with every edge between src_type and dst_type (either orientation) whose (subject,
    object) pair is in `pairs` removed: the pairs the meta-path model is fitted on must not find their own edge."""
    out = {}
    for et, ei in edge_index_dict.items():
        ei = _as_np(ei)
        if (et[0], et[2]) == (src_type, dst_type):
            keep = np.array([(a, b) not in pairs for a, b in zip(ei[0], ei[1])], dtype=bool)
        elif (et[0], et[2]) == (dst_type, src_type):
            keep = np.array([(b, a) not in pairs for a, b in zip(ei[0], ei[1])], dtype=bool)
        else:
            out[et] = ei
            continue
        out[et] = ei[:, keep] if ei.shape[1] else ei
    return out


def metapath_aurocs(edge_index_dict, node_counts, task_et, test_ei, neg_ei, candidates, seed=0, max_len=3,
                    max_paths=200, fit_frac=0.1, top=10, bridge=BRIDGE):
    """Takes the TRAINING graph ({edge type: edge_index}, held-out pairs already removed), {node type: count}, the
    task edge type, the test pairs [2, T], their negatives [2, T], the candidate object indices for training
    negatives (the test candidates), a seed, the longest shape, a cap on shapes, the share of training task edges
    used to fit, and how many single shapes to report.
    Returns {'auroc_metapath', 'metapath_coverage' (share of test pairs with at least one path),
    'metapath_n_shapes', 'metapath_n_shapes_dropped' (beyond max_paths), 'metapath_fit_pairs', 'metapath_shapes' (list of {shape, auroc, coverage} for the best
    `top` single shapes on the test pairs)}; auroc_metapath is 0.5 when no shape exists. bridge: the gene -> protein
    relation protein-level steps are projected through (project_bridge); None = walk the graph as it is."""
    src_type, _, dst_type = task_et
    task = _as_np(edge_index_dict[task_et])
    test_ei, neg_ei, candidates = _as_np(test_ei), _as_np(neg_ei), np.asarray(sorted(set(_as_np(candidates))))
    fit_mask = holdout_edges(task, fit_frac, seed)
    fit_pos = task[:, fit_mask]
    pos_pairs = {(int(a), int(b)) for a, b in zip(fit_pos[0], fit_pos[1])}
    known = {(int(a), int(b)) for a, b in zip(task[0], task[1])}
    graph = _drop_pairs(edge_index_dict, src_type, dst_type, pos_pairs)
    rel = typed_relations(graph, node_counts)
    if bridge:
        rel = project_bridge(rel, bridge)
    every = enumerate_metapaths(rel, src_type, dst_type, max_len=max_len, max_paths=None)
    shapes = every[:max_paths]
    T = test_ei.shape[1]
    out = {"metapath_n_shapes": len(shapes), "metapath_n_shapes_dropped": len(every) - len(shapes),
           "metapath_fit_pairs": int(fit_pos.shape[1])}
    if not shapes or fit_pos.shape[1] == 0 or len(candidates) == 0:
        out.update({"auroc_metapath": 0.5, "metapath_coverage": 0.0, "metapath_shapes": []})
        return out
    # training negatives: same corruption as the test set (object replaced by a candidate), never a known pair
    fit_neg_dst = []
    for a, b in zip(fit_pos[0], fit_pos[1]):
        for attempt in range(20):
            c = int(candidates[_unit(seed, f"mpneg:{attempt}:{a}:{b}") % len(candidates)])
            if (int(a), c) not in known:
                break
        fit_neg_dst.append(c)
    Xf = metapath_counts(rel, shapes, np.r_[fit_pos[0], fit_pos[0]], np.r_[fit_pos[1], fit_neg_dst])
    yf = np.r_[np.ones(fit_pos.shape[1]), np.zeros(fit_pos.shape[1])]
    Xt = metapath_counts(rel, shapes, np.r_[test_ei[0], neg_ei[0]], np.r_[test_ei[1], neg_ei[1]])
    yt = np.r_[np.ones(T), np.zeros(neg_ei.shape[1])]
    model = fit_logistic(np.log1p(Xf), yf)
    out["auroc_metapath"] = auroc(yt, predict_logistic(model, np.log1p(Xt)))
    out["metapath_coverage"] = float((Xt[:T].sum(1) > 0).mean()) if T else 0.0
    single = [{"shape": shape_name(s), "auroc": round(auroc(yt, Xt[:, j]), 4),
               "coverage": round(float((Xt[:T, j] > 0).mean()), 4) if T else 0.0}
              for j, s in enumerate(shapes)]
    single.sort(key=lambda r: (-r["auroc"], r["shape"]))
    out["metapath_shapes"] = single[:top]
    return out
