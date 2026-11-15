"""
GNN + PATH FEATURES on the frozen link-prediction benchmark. EXPLORATORY (ledger E008, pre-registered by Claude
Science before the first real run; the frozen R-GCN result stays the primary one and is never replaced by this).

Why: the R-GCN shows no gain from the enriched graph, the meta-path model does (mostly through the MONDO hierarchy).
A message-passing GNN gives every node one vector and cannot count the paths BETWEEN a gene and a disease. Here the
R-GCN stays the encoder and a small logistic head is fitted on top of (a) the pair's GNN vector, (b) the pair's
meta-path counts, (c) both together.

How it stays the same test: run.py is not edited. This module replaces only run._train_and_eval (the "train one
model on one fold, score the test pairs" step) for the duration of a call, the way kge.py does, so the test pairs,
folds, leak-edge removal and negatives are run.py's own. Inside the step:
  1. fit pairs = a hash-chosen share (default 10%) of the TRAINING gene-disease edges (metapaths.holdout_edges);
  2. those pairs are removed from the graph the GNN sees, in EVERY gene-disease edge type, both orientations;
  3. the R-GCN is trained on that smaller graph with run.py's own loop (models.train_epoch);
  4. pair features come from the same smaller graph: z_src * relation * z_dst (+ the DistMult score), and the
     meta-path counts (project_bridge -> enumerate_metapaths -> metapath_counts, log1p);
  5. three logistic heads are fitted on the fit pairs and their hash-chosen negatives ONLY, then score the test pairs.

    python -m pipelines.linkpred.gnnpath [--gnnpath-fit-frac 0.1] -- <run.py flags, tag diag_gnnpath_*>

  split_fit_pairs(data, task_et, train_mask, edge_masks, frac, seed) -> (train_mask2, edge_masks2, fit_pairs)
  gnnpath_train_eval(...)  same signature as run._train_and_eval -> run.py's metrics + auroc_gnnpath, auroc_gnnhead,
                           auroc_pathhead, gnnpath_fit_pairs, gnnpath_n_path_features (+ stratum_gnnpath_<name>_*)
  patched()                context manager that swaps run._train_and_eval for gnnpath_train_eval
"""
import argparse
import contextlib
import json
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipelines"))

GNNPATH_DEFAULTS = {"fit_frac": 0.1, "head": "logistic", "l2": 1.0}
_SETTINGS = dict(GNNPATH_DEFAULTS)


def _keep_mask(ei, src_type, dst_type, et, pairs):
    """bool numpy mask over the edges of type `et`: False where the edge restates a (gene, disease) pair in `pairs`
    (an edge dst_type -> src_type is read flipped)."""
    a, b = (ei[0], ei[1]) if (et[0], et[2]) == (src_type, dst_type) else (ei[1], ei[0])
    return np.array([(int(x), int(y)) not in pairs for x, y in zip(a, b)], dtype=bool)


def split_fit_pairs(data, task_et, train_mask, edge_masks, frac, seed):
    """Takes the graph, the task edge type, run.py's training mask and edge_masks and the fit share. Returns
    (train_mask2, edge_masks2, fit_pairs): the fit pairs (a set of (subject, object) indices drawn from the
    training task edges) are removed from the task type and from every other edge type between the two task node
    types, either orientation; existing edge_masks are kept (AND, never replaced)."""
    from linkpred.metapaths import holdout_edges
    src_type, _, dst_type = task_et
    train_mask = torch.as_tensor(train_mask, dtype=torch.bool)
    full = data[task_et].edge_index
    idx = train_mask.nonzero().flatten()
    train_ei = full[:, train_mask].cpu().numpy()
    fit_local = holdout_edges(train_ei, frac, seed)
    fit_pairs = {(int(a), int(b)) for a, b in zip(train_ei[0][fit_local], train_ei[1][fit_local])}
    keep_task = torch.from_numpy(_keep_mask(full.cpu().numpy(), src_type, dst_type, task_et, fit_pairs))
    train_mask2 = train_mask & keep_task
    masks2 = {}
    for et in data.edge_types:
        if et == task_et or {et[0], et[2]} != {src_type, dst_type}:
            continue
        keep = torch.from_numpy(_keep_mask(data[et].edge_index.cpu().numpy(), src_type, dst_type, et, fit_pairs))
        old = (edge_masks or {}).get(et)
        masks2[et] = keep if old is None else (torch.as_tensor(old, dtype=torch.bool) & keep)
    for et, m in (edge_masks or {}).items():
        masks2.setdefault(et, torch.as_tensor(m, dtype=torch.bool))
    return train_mask2, masks2, fit_pairs


def _fit_negatives(fit_pos, known, candidates, seed):
    """Same corruption as metapaths.metapath_aurocs: the object is replaced by a hash-chosen candidate that is not a
    known training pair. Returns the list of negative object indices (one per fit positive)."""
    from linkpred.metapaths import _unit
    out = []
    for a, b in zip(fit_pos[0], fit_pos[1]):
        for attempt in range(20):
            c = int(candidates[_unit(seed, f"mpneg:{attempt}:{a}:{b}") % len(candidates)])
            if (int(a), c) not in known:
                break
        out.append(c)
    return out


def fit_heads(feats_fit, y_fit, feats_test, l2=1.0):
    """Takes {head name: fit features [n, f]}, the 0/1 fit labels, {head name: test features [m, f]} and the L2
    weight. Returns {head name: test scores}. Each head is a logistic regression (metapaths.fit_logistic, columns
    standardised with the FIT mean and spread); the test rows never influence a weight or a standardisation."""
    from linkpred.metapaths import fit_logistic, predict_logistic
    return {k: predict_logistic(fit_logistic(feats_fit[k], y_fit, l2=l2), feats_test[k]) for k in feats_fit}


def gnnpath_train_eval(data, task_et, train_mask, edge_masks, test_ei, neg_ei, hidden, out, epochs, lr, device, seed,
                       reverse=False, learn=False, num_bases=None, neg_pool=None, baselines=False, metapaths=0,
                       candidates_idx=None, strata=None):
    """Same signature as run._train_and_eval; steps 1-5 of the module docstring. Returns run.py's metric dict
    ('auroc' = the plain R-GCN score of this smaller-graph model on the test pairs; baselines/metapaths as run.py
    would give them on the graph it is handed) plus auroc_gnnpath, auroc_gnnhead, auroc_pathhead,
    gnnpath_fit_pairs, gnnpath_n_path_features and, with strata, stratum_gnnpath_<name>_<label>."""
    from linkpred import metapaths as mp
    from linkpred.evaluate import auroc, classification_metrics, score_edges
    from linkpred.models import LinkPredictor, RGCNEncoder, masked_edges, train_epoch

    s = _SETTINGS
    torch.manual_seed(seed)
    src_type, _, dst_type = task_et
    train_mask2, masks2, fit_pairs = split_fit_pairs(data, task_et, train_mask, edge_masks, s["fit_frac"], seed)

    learn_nodes = {nt: data[nt].x for nt in data.node_types if hasattr(data[nt], "x")} if learn else None
    encoder = RGCNEncoder(data.metadata(), hidden=hidden, out=out, reverse=reverse, learn_nodes=learn_nodes,
                          num_bases=num_bases)
    rel_map = {et: i for i, et in enumerate(data.edge_types)}
    model = LinkPredictor(encoder, len(data.edge_types), out).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    x_dict = {nt: data[nt].x.to(device) for nt in data.node_types if hasattr(data[nt], "x")}
    edge_dict = masked_edges(data, task_et, train_mask2, device, masks2)
    for _ in range(epochs):
        train_epoch(model, data, optimizer, task_et, train_mask2, device, masks2, neg_pool=neg_pool)
    model.eval()
    with torch.no_grad():
        z = model.encode(x_dict, edge_dict)

    ri = torch.full((test_ei.size(1),), rel_map[task_et], dtype=torch.long)
    pos = score_edges(model, z, test_ei, src_type, dst_type, ri, device)
    neg = score_edges(model, z, neg_ei, src_type, dst_type, ri, device)
    m = classification_metrics(pos, neg)

    test_np, neg_np = test_ei.cpu().numpy(), neg_ei.cpu().numpy()
    raw = candidates_idx if candidates_idx is not None else neg_np[1]
    cand = np.asarray(sorted({int(x) for x in np.asarray(raw).ravel()}), dtype=int)
    fit_arr = np.array(sorted(fit_pairs), dtype=int).reshape(-1, 2).T
    full_train = data[task_et].edge_index[:, torch.as_tensor(train_mask, dtype=torch.bool)].cpu().numpy()
    known = {(int(a), int(b)) for a, b in zip(full_train[0], full_train[1])}
    m.update({"gnnpath_fit_pairs": int(fit_arr.shape[1])})
    if fit_arr.shape[1] == 0 or len(cand) == 0:
        m.update({"auroc_gnnpath": 0.5, "auroc_gnnhead": 0.5, "auroc_pathhead": 0.5, "gnnpath_n_path_features": 0})
        return m
    fit_neg = np.array(_fit_negatives(fit_arr, known, cand, seed), dtype=int)

    # the path graph = what the GNN saw (fit pairs gone from every gene-disease type); _drop_pairs is a belt-and-braces repeat
    graph = mp._drop_pairs({et: ei.cpu() for et, ei in edge_dict.items()}, src_type, dst_type, fit_pairs)
    counts = {nt: int(data[nt].num_nodes) for nt in data.node_types}
    rel = mp.project_bridge(mp.typed_relations(graph, counts), mp.BRIDGE)
    shapes = mp.enumerate_metapaths(rel, src_type, dst_type, max_len=metapaths or 3, max_paths=200)

    fs, fd = np.r_[fit_arr[0], fit_arr[0]], np.r_[fit_arr[1], fit_neg]
    ts, td = np.r_[test_np[0], neg_np[0]], np.r_[test_np[1], neg_np[1]]
    y_fit = np.r_[np.ones(fit_arr.shape[1]), np.zeros(fit_arr.shape[1])]
    y_test = np.r_[np.ones(test_np.shape[1]), np.zeros(neg_np.shape[1])]
    r_vec = model.decoder.rel.weight[rel_map[task_et]].detach().cpu()

    def gnn_feats(a, b):
        zs, zd = z[src_type].cpu()[torch.as_tensor(a)], z[dst_type].cpu()[torch.as_tensor(b)]
        prod = zs * r_vec * zd
        return np.c_[prod.numpy(), prod.sum(1, keepdim=True).numpy()]

    def path_feats(a, b):
        return np.log1p(mp.metapath_counts(rel, shapes, a, b)) if shapes else np.zeros((len(a), 0))

    G_fit, G_test = gnn_feats(fs, fd), gnn_feats(ts, td)
    P_fit, P_test = path_feats(fs, fd), path_feats(ts, td)
    feats_fit = {"gnnhead": G_fit}
    feats_test = {"gnnhead": G_test}
    if P_fit.shape[1]:
        feats_fit.update({"pathhead": P_fit, "gnnpath": np.c_[G_fit, P_fit]})
        feats_test.update({"pathhead": P_test, "gnnpath": np.c_[G_test, P_test]})
    scores = fit_heads(feats_fit, y_fit, feats_test, l2=s["l2"])
    T = test_np.shape[1]
    for name in ("gnnpath", "gnnhead", "pathhead"):
        m[f"auroc_{name}"] = auroc(y_test, scores[name]) if name in scores else 0.5
    m["gnnpath_n_path_features"] = int(P_fit.shape[1])
    if strata and "gnnpath" in scores:
        from linkpred.strata import stratified_aurocs
        for name, labels in strata.items():
            m.update(stratified_aurocs(scores["gnnpath"][:T], scores["gnnpath"][T:], labels,
                                       prefix=f"stratum_gnnpath_{name}"))
    return m


@contextlib.contextmanager
def patched(**settings):
    """Swap run._train_and_eval for gnnpath_train_eval (settings: any GNNPATH_DEFAULTS key) inside the with-block."""
    from linkpred import run
    unknown = set(settings) - set(GNNPATH_DEFAULTS)
    if unknown:
        raise TypeError(f"unknown gnnpath settings: {sorted(unknown)}")
    old_fn, old_settings = run._train_and_eval, dict(_SETTINGS)
    _SETTINGS.update(settings)
    run._train_and_eval = gnnpath_train_eval
    try:
        yield
    finally:
        run._train_and_eval = old_fn
        _SETTINGS.clear()
        _SETTINGS.update(old_settings)


def main(argv=None):
    """Parse the gnnpath options, then hand the rest to run.main() with the model step swapped. The tag must start
    with 'diag_gnnpath' so the result never looks like a frozen run."""
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--gnnpath-fit-frac", type=float, default=GNNPATH_DEFAULTS["fit_frac"])
    p.add_argument("--gnnpath-head", default=GNNPATH_DEFAULTS["head"], choices=["logistic"])
    p.add_argument("--gnnpath-l2", type=float, default=GNNPATH_DEFAULTS["l2"])
    own, rest = p.parse_known_args(argv)
    rest = [a for a in rest if a != "--"]
    if "--tag" not in rest or not rest[rest.index("--tag") + 1].startswith("diag_gnnpath"):
        raise SystemExit("gnnpath.py: pass --tag diag_gnnpath_<name> so the result is labelled exploratory")
    from linkpred import run
    settings = dict(fit_frac=own.gnnpath_fit_frac, head=own.gnnpath_head, l2=own.gnnpath_l2)
    tag = rest[rest.index("--tag") + 1]
    with patched(**settings):
        sys.argv = ["linkpred.run"] + rest
        run.main()
    for path in (ROOT / "outputs" / "linkpred").glob(f"*@{tag}.json"):
        d = json.loads(path.read_text())
        d["settings"]["gnnpath"] = {**settings, "encoder": "R-GCN (run.py's own)",
                                    "note": "EXPLORATORY (ledger E008); auroc = plain R-GCN on the smaller graph, "
                                            "auroc_gnnpath = logistic head on GNN pair vector + meta-path counts"}
        path.write_text(json.dumps(d, indent=2))


if __name__ == "__main__":
    main()
