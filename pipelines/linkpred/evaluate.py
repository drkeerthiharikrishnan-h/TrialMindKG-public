"""
Evaluation metrics for link prediction: AUROC, AUPRC, MRR, Hits@K.

All metrics are computed against type-matched negatives (random pairs of the same node types
that do not appear in any source).
"""
import numpy as np

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _require():
    if not _HAS_TORCH:
        raise ImportError("torch required")


def score_edges(model, z_dict, edge_index, src_type, dst_type, rel_idx, device="cpu"):
    """Score a set of edges using the trained model's decoder."""
    _require()
    src = z_dict[src_type][edge_index[0].to(device)]
    dst = z_dict[dst_type][edge_index[1].to(device)]
    with torch.no_grad():
        return model.decode(src, dst, rel_idx.to(device)).cpu().numpy()


def auroc(y, s):
    """Area under the ROC curve (Mann-Whitney U, ties count half). y: 0/1 array, s: scores."""
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s))
    sorted_s = s[order]
    # average rank for tied scores
    _, first, counts = np.unique(sorted_s, return_index=True, return_counts=True)
    avg = first + (counts - 1) / 2.0 + 1
    ranks[order] = np.repeat(avg, counts)
    n_pos, n_neg = int(y.sum()), int(len(y) - y.sum())
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def average_precision(y, s):
    """Area under the precision-recall curve as average precision (same definition as scikit-learn)."""
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    order = np.argsort(-s, kind="mergesort")
    y, s = y[order], s[order]
    last = np.r_[np.where(np.diff(s))[0], len(s) - 1]      # last index of each distinct score
    tp = np.cumsum(y)[last]
    precision = tp / (last + 1)
    recall = tp / y.sum()
    return float(np.sum(np.diff(np.r_[0, recall]) * precision))


def classification_metrics(pos_scores, neg_scores):
    """AUROC and AUPRC from positive and negative edge scores."""
    y = np.concatenate([np.ones(len(pos_scores)), np.zeros(len(neg_scores))])
    s = np.concatenate([pos_scores, neg_scores])
    return {"auroc": auroc(y, s), "auprc": average_precision(y, s)}


def ranking_metrics(model, z_dict, test_edge_index, src_type, dst_type, rel_idx,
                    all_dst_ids, known_pairs, device="cpu", max_eval=1000):
    """Filtered MRR and Hits@10: for each test edge, rank the true destination among ALL destinations,
    filtering out known true pairs (so they don't count as false negatives)."""
    _require()
    n_dst = len(all_dst_ids)
    src_nodes = test_edge_index[0].numpy()
    dst_nodes = test_edge_index[1].numpy()
    n_eval = min(len(src_nodes), max_eval)
    if n_eval < len(src_nodes):
        idx = np.random.choice(len(src_nodes), n_eval, replace=False)
        src_nodes, dst_nodes = src_nodes[idx], dst_nodes[idx]
    ranks = []
    for s, d in zip(src_nodes, dst_nodes):
        src_emb = z_dict[src_type][s].unsqueeze(0).expand(n_dst, -1).to(device)
        dst_emb = z_dict[dst_type].to(device)
        ri = rel_idx.expand(n_dst).to(device)
        with torch.no_grad():
            scores = model.decode(src_emb, dst_emb, ri).cpu().numpy()
        true_score = scores[d]
        filt = scores.copy()
        for known_d in known_pairs.get(int(s), set()):
            if known_d != d and known_d < n_dst:
                filt[known_d] = -1e9
        rank = (filt >= true_score).sum()
        ranks.append(rank)
    ranks = np.array(ranks, dtype=float)
    return {
        "mrr": float((1.0 / ranks).mean()),
        "hits@1": float((ranks <= 1).mean()),
        "hits@10": float((ranks <= 10).mean()),
        "hits@50": float((ranks <= 50).mean()),
        "n_evaluated": len(ranks),
    }


def degree_baseline(train_edge_index, test_edge_index, n_dst):
    """Baseline: rank destinations by training-set degree (popularity). Returns MRR and Hits@10."""
    dst_train = train_edge_index[1].numpy()
    degree = np.bincount(dst_train, minlength=n_dst).astype(float)
    dst_test = test_edge_index[1].numpy()
    ranks = []
    for d in dst_test:
        rank = (degree >= degree[d]).sum()
        ranks.append(rank)
    ranks = np.array(ranks, dtype=float)
    return {"mrr": float((1.0 / ranks).mean()), "hits@10": float((ranks <= 10).mean()), "method": "degree_baseline"}


def summarise_run(metrics_list):
    """Aggregate metrics across seeds: mean and 95% CI of every numeric key, over the seeds that have it (a degree
    stratum can be empty for one seed); non-numeric values (lists of meta-path shapes) are left out."""
    keys = []
    for m in metrics_list:
        keys += [k for k, v in m.items() if k not in keys and isinstance(v, (int, float)) and not isinstance(v, bool)]
    summary = {}
    for k in keys:
        vals = np.array([m[k] for m in metrics_list if k in m])
        mean = vals.mean()
        ci = 1.96 * vals.std(ddof=1) / np.sqrt(len(vals)) if len(vals) > 1 else 0.0
        summary[k] = {"mean": round(float(mean), 4), "ci95": round(float(ci), 4)}
    return summary


def _stratum_order(key):
    """Sort key for 'stratum_<name>_<bin>': by name, then by the bin's lower bound ('1-5' < '6-20' < '>100')."""
    name, _, lab = key[len("stratum_"):].rpartition("_")
    digits = "".join(ch if ch.isdigit() else " " for ch in lab).split()
    return (name, int(digits[0]) + (0.5 if lab.startswith(">") else 0) if digits else 0, lab)


def report_markdown(results):
    """Format a results dict (graph -> task -> split -> summary) as a Markdown report."""
    lines = ["# Link Prediction Results", ""]
    for graph, tasks in sorted(results.items()):
        lines.append(f"## {graph}")
        for task, splits in sorted(tasks.items()):
            lines.append(f"### Task: {task}")
            lines.append("| Split | AUROC | AUPRC | MRR | Hits@10 | degree baseline | path heuristic (AA) | untrained "
                         "| meta-path model | pairs with a path | GNN + paths (auroc_gnnpath) |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for split, m in sorted(splits.items()):
                def fmt(k):
                    if k in m:
                        v = m[k]
                        if isinstance(v, dict):
                            return f"{v['mean']:.4f} +/- {v['ci95']:.4f}"
                        return f"{v:.4f}"
                    return "-"
                lines.append(f"| {split} | {fmt('auroc')} | {fmt('auprc')} | {fmt('mrr')} | {fmt('hits@10')} | "
                             f"{fmt('auroc_degree_dst')} | {fmt('auroc_aa')} | {fmt('auroc_untrained')} | "
                             f"{fmt('auroc_metapath')} | {fmt('metapath_coverage')} | {fmt('auroc_gnnpath')} |")
            strata = sorted({k for m in splits.values() for k in m
                             if k.startswith("stratum_") and not k.endswith("_n")}, key=_stratum_order)
            if strata:
                lines += ["", "AUROC per degree stratum (bins = number of G0 test-eligible pairs of the node; "
                          "n = test pairs per fold):", "",
                          "| Split | " + " | ".join(k[8:] for k in strata) + " |",
                          "|---|" + "---|" * len(strata)]
                for split, m in sorted(splits.items()):
                    cells = [f"{m[k]['mean']:.3f} (n {m[k + '_n']['mean']:.0f})" if k in m else "-" for k in strata]
                    lines.append(f"| {split} | " + " | ".join(cells) + " |")
            lines.append("")
    return "\n".join(lines) + "\n"
