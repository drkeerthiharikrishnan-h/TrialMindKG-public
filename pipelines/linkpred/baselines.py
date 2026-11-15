"""
Model-free baselines for the link-prediction benchmark (docs/ENRICHMENT_DIAGNOSIS_PLAN.md, wave A), scored on
exactly the same test pairs and negatives as the GNN:

  degree_aurocs(...)     popularity: the training degree of the disease / the gene / their product as the score.
                         If this is close to the GNN's AUROC, the GNN mostly learns popularity.
  path_scores(A, ...)    neighbourhood heuristics on the training graph with every edge type in both directions:
                         cn = common neighbours (2-hop paths gene -> x -> disease), aa = Adamic-Adar (common
                         neighbours weighted 1 / log(1 + degree)), path3 = number of 3-hop paths.
                         If these improve from G0 to G2, the enrichment CONTAINS signal about the held-out links,
                         whatever the GNN makes of it.
  heuristic_aurocs(...)  builds the adjacency from a {edge type: edge_index} dict and returns the three AUROCs.

numpy / scipy only (no torch), so the functions are unit-tested in the cloud; run.py calls them with --baselines.
"""
import numpy as np
import scipy.sparse as sp

from linkpred.evaluate import auroc


def _as_np(x):
    return x.detach().cpu().numpy() if hasattr(x, "detach") else np.asarray(x)


def degree_aurocs(train_src, train_dst, n_src, n_dst, test_ei, neg_ei):
    """Takes the training task edges (subject indices, object indices), the node counts, the test pairs [2, T] and
    the negative pairs [2, T]. Returns {'auroc_degree_dst', 'auroc_degree_src', 'auroc_degree_prod'}: AUROC when the
    score of a pair is the object's training degree, the subject's, or their product."""
    train_src, train_dst, test_ei, neg_ei = map(_as_np, (train_src, train_dst, test_ei, neg_ei))
    deg_s = np.bincount(train_src, minlength=n_src).astype(float)
    deg_d = np.bincount(train_dst, minlength=n_dst).astype(float)
    y = np.r_[np.ones(test_ei.shape[1]), np.zeros(neg_ei.shape[1])]
    s_all = np.r_[test_ei[0], neg_ei[0]]
    d_all = np.r_[test_ei[1], neg_ei[1]]
    return {"auroc_degree_dst": auroc(y, deg_d[d_all]),
            "auroc_degree_src": auroc(y, deg_s[s_all]),
            "auroc_degree_prod": auroc(y, deg_s[s_all] * deg_d[d_all])}


def block_offsets(node_counts):
    """Takes {node type: number of nodes} (insertion order). Returns ({node type: offset}, total): one global index
    space for all node types, the same layout the R-GCN encoder uses."""
    offsets, cur = {}, 0
    for nt, n in node_counts.items():
        offsets[nt] = cur
        cur += int(n)
    return offsets, cur


def build_adjacency(edge_index_dict, node_counts):
    """Takes {edge type: edge_index [2, E]} and {node type: count}. Returns (A, offsets): a symmetric binary CSR
    adjacency over all nodes (every edge in both directions, duplicates and self loops removed)."""
    offsets, n = block_offsets(node_counts)
    rows, cols = [], []
    for et, ei in edge_index_dict.items():
        ei = _as_np(ei)
        if ei.shape[1] == 0:
            continue
        rows.append(ei[0] + offsets[et[0]])
        cols.append(ei[1] + offsets[et[2]])
    if not rows:
        return sp.csr_matrix((n, n)), offsets
    r, c = np.concatenate(rows), np.concatenate(cols)
    keep = r != c
    r, c = r[keep], c[keep]
    A = sp.coo_matrix((np.ones(2 * len(r)), (np.r_[r, c], np.r_[c, r])), shape=(n, n)).tocsr()
    A.data[:] = 1.0
    return A, offsets


def path_scores(A, src, dst, batch=2000):
    """Takes the adjacency, global subject indices and global object indices (same length). Returns
    {'cn', 'aa', 'path3'}: float arrays, one score per (subject, object) pair, computed in batches of rows."""
    src, dst = _as_np(src), _as_np(dst)
    deg = np.asarray(A.sum(1)).ravel()
    w = 1.0 / np.log1p(np.maximum(deg, 1.0))
    cn = np.empty(len(src)); aa = np.empty(len(src)); p3 = np.empty(len(src))
    for start in range(0, len(src), batch):
        s, d = src[start:start + batch], dst[start:start + batch]
        rs, rd = A[s], A[d]                                   # [B, N] sparse rows
        both = rs.multiply(rd)
        cn[start:start + batch] = np.asarray(both.sum(1)).ravel()
        aa[start:start + batch] = both @ w
        p3[start:start + batch] = np.asarray((rs @ A).multiply(rd).sum(1)).ravel()
    return {"cn": cn, "aa": aa, "path3": p3}


def heuristic_aurocs(edge_index_dict, node_counts, src_type, dst_type, test_ei, neg_ei, batch=2000):
    """Takes the training graph ({edge type: edge_index} with held-out pairs removed), {node type: count}, the task's
    node types, the test pairs [2, T] and the negatives [2, T]. Returns {'auroc_cn', 'auroc_aa', 'auroc_path3'}."""
    A, offsets = build_adjacency(edge_index_dict, node_counts)
    test_ei, neg_ei = _as_np(test_ei), _as_np(neg_ei)
    s = np.r_[test_ei[0], neg_ei[0]] + offsets[src_type]
    d = np.r_[test_ei[1], neg_ei[1]] + offsets[dst_type]
    y = np.r_[np.ones(test_ei.shape[1]), np.zeros(neg_ei.shape[1])]
    sc = path_scores(A, s, d, batch)
    return {f"auroc_{k}": auroc(y, v) for k, v in sc.items()}
