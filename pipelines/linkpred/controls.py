"""
Independence controls for the meta-path gain of the enriched graphs over G0 (loop/tasks/T-002.yaml, EXPLORATORY: the
frozen primary protocol in docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md section 3 is not touched; run.py, splits.py,
metapaths.py and evaluate.py are used as they are).

Why: the meta-path model scores G2 0.77 vs G0 0.69 on the same test pairs, but G2 reaches 99% of the test pairs by
at least one path and G0 only 88%. A pair with no path gets the model's constant score, so part of the gain may be
coverage, not signal. The control: score both graphs on the pairs that have a path in BOTH, fold by fold, and
report the paired difference of those AUROCs with a 95% interval (same t interval as compare.py).

  metapath_pair_scores(...)        -> per test pair and negative: key, label, meta-path score, has_path (one graph, one fold).
                                      Same fit as metapaths.metapath_aurocs (its auroc_metapath is reproduced exactly).
  coverage_matched(a, b)           -> AUROC of both graphs on the pairs both cover, and how many pairs that is.
  summarise_matched(folds)         -> mean paired difference over the folds with its 95% interval and wins.
  matched_sentence(summary, ...)   -> one sentence computed from those numbers (CLAUDE.md rule 7).
  matched_markdown(...)            -> the report table + sentence.

A pair is keyed by the node ids of its two ends ("NCBIGene:7157|MONDO:0000005"), not by tensor index, because the
index of the same gene differs between G0 and G2. Fixtures and unit tests only here; the real runs are Claude
Science's (one fold of a G0 / G2 pair at --epochs 0 takes minutes: no GNN is involved).
"""
import math

import numpy as np

from linkpred.compare import t_quantile
from linkpred.evaluate import auroc
from linkpred.metapaths import (_as_np, _drop_pairs, _unit, enumerate_metapaths, fit_logistic, holdout_edges,
                                metapath_counts, predict_logistic, project_bridge, typed_relations, BRIDGE)


def pair_key(src_id, dst_id):
    return f"{src_id}|{dst_id}"


def metapath_pair_scores(edge_index_dict, node_counts, task_et, test_ei, neg_ei, candidates, src_ids, dst_ids,
                         seed=0, max_len=3, max_paths=200, fit_frac=0.1, bridge=BRIDGE):
    """Takes the same inputs as metapaths.metapath_aurocs (TRAINING graph with the held-out pairs removed, node
    counts, task edge type, test pairs [2, T], negatives [2, T], candidate object indices, seed, longest shape, shape
    cap, fit share, bridge) plus the node id of every index of the two task node types (src_ids, dst_ids: lists).
    Returns {'key': [T + T pair keys], 'label': 1/0 array, 'score': meta-path model score, 'has_path': bool array
    (at least one path of any shape), 'auroc': the AUROC of 'score' (equal to metapath_aurocs' auroc_metapath),
    'coverage': share of positive test pairs with a path}. With no shape or no fit pair the scores are 0 and no
    pair has a path, as metapath_aurocs reports 0.5."""
    src_type, _, dst_type = task_et
    task = _as_np(edge_index_dict[task_et])
    test_ei, neg_ei = _as_np(test_ei), _as_np(neg_ei)
    candidates = np.asarray(sorted(set(_as_np(candidates))))
    ends = (np.r_[test_ei[0], neg_ei[0]], np.r_[test_ei[1], neg_ei[1]])
    key = [pair_key(src_ids[a], dst_ids[b]) for a, b in zip(*ends)]
    y = np.r_[np.ones(test_ei.shape[1]), np.zeros(neg_ei.shape[1])]
    empty = {"key": key, "label": y, "score": np.zeros(len(y)), "has_path": np.zeros(len(y), bool), "auroc": 0.5,
             "coverage": 0.0}
    fit_mask = holdout_edges(task, fit_frac, seed)
    fit_pos = task[:, fit_mask]
    pos_pairs = {(int(a), int(b)) for a, b in zip(fit_pos[0], fit_pos[1])}
    known = {(int(a), int(b)) for a, b in zip(task[0], task[1])}
    rel = typed_relations(_drop_pairs(edge_index_dict, src_type, dst_type, pos_pairs), node_counts)
    if bridge:
        rel = project_bridge(rel, bridge)
    shapes = enumerate_metapaths(rel, src_type, dst_type, max_len=max_len, max_paths=None)[:max_paths]
    if not shapes or fit_pos.shape[1] == 0 or len(candidates) == 0:
        return empty
    fit_neg_dst = []
    for a, b in zip(fit_pos[0], fit_pos[1]):
        for attempt in range(20):
            c = int(candidates[_unit(seed, f"mpneg:{attempt}:{a}:{b}") % len(candidates)])
            if (int(a), c) not in known:
                break
        fit_neg_dst.append(c)
    Xf = metapath_counts(rel, shapes, np.r_[fit_pos[0], fit_pos[0]], np.r_[fit_pos[1], fit_neg_dst])
    yf = np.r_[np.ones(fit_pos.shape[1]), np.zeros(fit_pos.shape[1])]
    Xt = metapath_counts(rel, shapes, *ends)
    model = fit_logistic(np.log1p(Xf), yf)
    score = predict_logistic(model, np.log1p(Xt))
    has = Xt.sum(1) > 0
    T = test_ei.shape[1]
    return {"key": key, "label": y, "score": score, "has_path": has, "auroc": auroc(y, score),
            "coverage": float(has[:T].mean()) if T else 0.0}


def coverage_matched(a, b):
    """Takes the pair-score dicts of two graphs for the SAME fold (metapath_pair_scores output; the same test
    pairs and negatives, ordered or not). Returns {'n_common': pairs both graphs scored, 'n_matched': of those, the
    pairs with a path in both, 'share_matched', 'n_pos', 'n_neg' among the matched, 'auroc_a', 'auroc_b', 'delta'
    (a - b)}; the AUROCs and delta are None when the matched set lacks a positive or a negative pair. A pair whose
    label differs between the graphs is a different task and raises ValueError."""
    ia = {k: i for i, k in enumerate(a["key"])}
    ib = {k: i for i, k in enumerate(b["key"])}
    common = sorted(set(ia) & set(ib))
    ja = np.array([ia[k] for k in common], int)
    jb = np.array([ib[k] for k in common], int)
    if len(common) and not np.array_equal(np.asarray(a["label"])[ja], np.asarray(b["label"])[jb]):
        raise ValueError("the two graphs label the same pair differently: not the same test set")
    both = (np.asarray(a["has_path"])[ja] & np.asarray(b["has_path"])[jb]) if len(common) else np.zeros(0, bool)
    ja, jb = ja[both], jb[both]
    y = np.asarray(a["label"])[ja]
    out = {"n_common": len(common), "n_matched": int(both.sum()),
           "share_matched": float(both.mean()) if len(common) else 0.0,
           "n_pos": int((y == 1).sum()), "n_neg": int((y == 0).sum()), "auroc_a": None, "auroc_b": None, "delta": None}
    if out["n_pos"] and out["n_neg"]:
        out["auroc_a"] = float(auroc(y, np.asarray(a["score"])[ja]))
        out["auroc_b"] = float(auroc(y, np.asarray(b["score"])[jb]))
        out["delta"] = out["auroc_a"] - out["auroc_b"]
    return out


def summarise_matched(folds):
    """Takes a list of coverage_matched results (one per fold / seed). Returns {'n': folds with a delta, 'mean',
    'ci_low', 'ci_high' (95% t interval over the folds; None when n < 2), 'wins' (folds where a beat b),
    'mean_share_matched', 'mean_auroc_a', 'mean_auroc_b'}."""
    f = [x for x in folds if x["delta"] is not None]
    out = {"n": len(f), "mean": None, "ci_low": None, "ci_high": None, "wins": sum(x["delta"] > 0 for x in f),
           "mean_share_matched": float(np.mean([x["share_matched"] for x in folds])) if folds else None,
           "mean_auroc_a": None, "mean_auroc_b": None}
    if not f:
        return out
    d = [x["delta"] for x in f]
    out["mean"] = sum(d) / len(d)
    out["mean_auroc_a"] = float(np.mean([x["auroc_a"] for x in f]))
    out["mean_auroc_b"] = float(np.mean([x["auroc_b"] for x in f]))
    if len(d) >= 2:
        sd = math.sqrt(sum((x - out["mean"]) ** 2 for x in d) / (len(d) - 1))
        half = t_quantile(len(d) - 1) * sd / math.sqrt(len(d))
        out["ci_low"], out["ci_high"] = out["mean"] - half, out["mean"] + half
    return out


def matched_sentence(s, a_label="G2", b_label="G0", unmatched_delta=None):
    """Takes a summarise_matched result, the two labels and optionally the UNmatched paired difference (the frozen
    run's number). Returns one sentence whose wording follows the intervals: the gain survives when the matched
    interval is above 0, is gone when it includes 0, reverses when below; and, if the unmatched difference is
    given, how much of it remains."""
    if s["n"] < 2 or s["ci_low"] is None:
        return f"Only {s['n']} fold(s) with matched pairs: too few for an interval."
    diff = (f"{s['mean']:+.4f} AUROC on the {s['mean_share_matched']:.1%} of pairs with a meta-path in both graphs "
            f"(95% CI {s['ci_low']:+.4f} to {s['ci_high']:+.4f}; {s['wins']} of {s['n']} folds higher)")
    keep = ""
    if unmatched_delta:
        keep = f" That is {s['mean'] / unmatched_delta:.0%} of the unmatched difference ({unmatched_delta:+.4f})."
    if s["ci_low"] > 0:
        return f"The meta-path model on {a_label} still scores higher than on {b_label} when coverage is matched: {diff}.{keep}"
    if s["ci_high"] < 0:
        return f"With coverage matched the meta-path model on {a_label} scores LOWER than on {b_label}: {diff}.{keep}"
    return (f"With coverage matched there is no clear difference between {a_label} and {b_label}: {diff}; the interval "
            f"includes 0, so the gain is not shown to be more than coverage.{keep}")


def matched_markdown(folds, a_label="G2", b_label="G0", unmatched_delta=None):
    """Takes the per-fold coverage_matched results and the labels. Returns the Markdown report of the control."""
    s = summarise_matched(folds)
    fmt = lambda v: "-" if v is None else f"{v:.4f}"
    lines = [f"# Coverage-matched meta-path AUROC: {a_label} vs {b_label} (exploratory control)", "",
             "Both graphs scored on the same test pairs and negatives, restricted to the pairs with at least one "
             "meta-path in both graphs. The frozen primary protocol is unchanged.", "",
             f"| fold | pairs in both | with a path in both | share | positives / negatives | AUROC {a_label} | "
             f"AUROC {b_label} | difference |", "|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(folds):
        lines.append(f"| {i} | {x['n_common']} | {x['n_matched']} | {x['share_matched']:.1%} | {x['n_pos']} / "
                     f"{x['n_neg']} | {fmt(x['auroc_a'])} | {fmt(x['auroc_b'])} | "
                     f"{'-' if x['delta'] is None else format(x['delta'], '+.4f')} |")
    lines += ["", matched_sentence(s, a_label, b_label, unmatched_delta)]
    return "\n".join(lines) + "\n"
