"""
Knowledge-graph EMBEDDING models (TransE / DistMult / ComplEx / RotatE from torch_geometric.nn.kge) on the frozen
link-prediction benchmark. EXPLORATORY addition (ledger E006, pre-registered before the first real run): the brief's
evaluation names "GNNs, GCNs, graph embeddings"; our R-GCN gained nothing from enrichment, so we add one shallow
embedding model under the SAME protocol.

How it stays the same test: run.py is not edited. This module replaces only run._train_and_eval (the "train one model
on one fold, score the test pairs" step) for the duration of a call, so every other step is run.py's own: the test
pairs (digest 24f5864cc07ad723), the drop of text-mined edges, the context relation, the per-type cap, the removal of
edges that restate a held-out pair (leak edges), the degree-matched negatives, the folds, the strata, the result file.

    python -m pipelines.linkpred.kge --kge rotate --kge-epochs 30 --kge-minutes 60 -- <run.py flags, tag diag_kge_*>

  build_triples(edge_dict, data)                 -> (head, rel, tail) global-index tensors + node offsets + rel ids
  kge_train_eval(data, task_et, train_mask, edge_masks, test_ei, neg_ei, hidden, ..., model_name)
                                                 -> {'auroc', 'auprc', 'kge_epochs_done', 'kge_train_minutes', ...}
  patched()                                      -> context manager that swaps run._train_and_eval for kge_train_eval
"""
import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipelines"))

KGE_DEFAULTS = {"model_name": "rotate", "kge_epochs": 30, "kge_minutes": 60.0, "batch_size": 16384, "lr": 0.01,
                "margin": 9.0}
_SETTINGS = dict(KGE_DEFAULTS)


def _kge_class(name):
    from torch_geometric.nn import kge
    classes = {"transe": kge.TransE, "distmult": kge.DistMult, "complex": kge.ComplEx, "rotate": kge.RotatE}
    if name not in classes:
        raise ValueError(f"unknown KGE model {name!r}; choose one of {sorted(classes)}")
    return classes[name]


def build_triples(edge_dict, data):
    """Takes {edge type: [2, E] edge_index} (the TRAINING graph, leak edges already removed) and the HeteroData.
    Returns (head, rel, tail, offsets, rel_ids): one global index space for all node types (offsets[node type] is
    added to the local index), one relation id per edge type (rel_ids[edge type]); empty edge types are skipped but
    still get a relation id so the task type always has one."""
    offsets, n = {}, 0
    for nt in data.node_types:
        offsets[nt] = n
        n += int(data[nt].num_nodes)
    rel_ids = {et: i for i, et in enumerate(data.edge_types)}
    hs, rs, ts = [], [], []
    for et, ei in edge_dict.items():
        if ei.numel() == 0:
            continue
        s, _, d = et
        hs.append(ei[0].cpu() + offsets[s])
        ts.append(ei[1].cpu() + offsets[d])
        rs.append(torch.full((ei.size(1),), rel_ids[et], dtype=torch.long))
    head = torch.cat(hs) if hs else torch.zeros(0, dtype=torch.long)
    tail = torch.cat(ts) if ts else torch.zeros(0, dtype=torch.long)
    rel = torch.cat(rs) if rs else torch.zeros(0, dtype=torch.long)
    return head, rel, tail, offsets, rel_ids, n


def kge_train_eval(data, task_et, train_mask, edge_masks, test_ei, neg_ei, hidden, out, epochs, lr, device, seed,
                   reverse=False, learn=False, num_bases=None, neg_pool=None, baselines=False, metapaths=0,
                   candidates_idx=None, strata=None):
    """Same signature as run._train_and_eval. Trains the KGE model chosen in _SETTINGS (dimension = run.py's
    --hidden) on every training edge of every type, with PyG's own negative sampling (head or tail replaced by a random
    node), for at most _SETTINGS['kge_epochs'] epochs or _SETTINGS['kge_minutes'] minutes, whichever comes first; then
    scores the held-out gene-disease pairs and their degree-matched negatives with the task relation. Returns
    {'auroc', 'auprc'} (+ strata keys when strata is given) plus bookkeeping: kge_model, kge_dim, kge_epochs_done,
    kge_train_minutes, kge_final_loss, kge_n_triples. R-GCN-only arguments (reverse, learn, num_bases, neg_pool) are
    ignored; baselines/metapaths are not recomputed here (they are in the frozen result files already)."""
    from linkpred.evaluate import classification_metrics
    from linkpred.models import masked_edges

    torch.manual_seed(seed)
    s = _SETTINGS
    edge_dict = masked_edges(data, task_et, train_mask, "cpu", edge_masks)
    head, rel, tail, offsets, rel_ids, n_nodes = build_triples(edge_dict, data)
    cls = _kge_class(s["model_name"])
    kwargs = {"margin": s["margin"]} if s["model_name"] in ("rotate", "transe") else {}
    model = cls(n_nodes, len(rel_ids), hidden, sparse=True, **kwargs).to(device)
    opt = torch.optim.SparseAdam(list(model.parameters()), lr=s["lr"])
    loader = model.loader(head_index=head, rel_type=rel, tail_index=tail, batch_size=s["batch_size"], shuffle=True)
    t0, done, last = time.time(), 0, float("nan")
    for _ in range(s["kge_epochs"]):
        model.train()
        total, nb = 0.0, 0
        for h, r, t in loader:
            opt.zero_grad()
            loss = model.loss(h.to(device), r.to(device), t.to(device))
            loss.backward()
            opt.step()
            total, nb = total + float(loss.detach()), nb + 1
        done, last = done + 1, total / max(nb, 1)
        if (time.time() - t0) / 60 > s["kge_minutes"]:
            break
    src_type, _, dst_type = task_et
    r_task = rel_ids[task_et]

    def score(ei):
        model.eval()
        with torch.no_grad():
            h = (ei[0].cpu() + offsets[src_type]).to(device)
            t = (ei[1].cpu() + offsets[dst_type]).to(device)
            r = torch.full_like(h, r_task)
            return model(h, r, t).cpu()

    pos, neg = score(test_ei), score(neg_ei)
    m = classification_metrics(pos, neg)
    if strata:
        from linkpred.strata import stratified_aurocs
        for name, labels in strata.items():
            m.update(stratified_aurocs(pos, neg, labels, prefix=f"stratum_{name}"))
    m.update({"kge_dim": int(hidden), "kge_epochs_done": done, "kge_train_minutes": round((time.time() - t0) / 60, 2),
              "kge_final_loss": last, "kge_n_triples": int(head.numel())})
    return m


@contextlib.contextmanager
def patched(**settings):
    """Swap run._train_and_eval for kge_train_eval (settings: any KGE_DEFAULTS key) inside the with-block."""
    from linkpred import run
    unknown = set(settings) - set(KGE_DEFAULTS)
    if unknown:
        raise TypeError(f"unknown KGE settings: {sorted(unknown)}")
    old_fn, old_settings = run._train_and_eval, dict(_SETTINGS)
    _SETTINGS.update(settings)
    run._train_and_eval = kge_train_eval
    try:
        yield
    finally:
        run._train_and_eval = old_fn
        _SETTINGS.clear()
        _SETTINGS.update(old_settings)


def main(argv=None):
    """Parse the KGE options, then hand the remaining arguments to run.main() with the model step swapped. The tag
    must start with 'diag_kge' so the result never looks like a frozen run."""
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--kge", default=KGE_DEFAULTS["model_name"], choices=["transe", "distmult", "complex", "rotate"])
    p.add_argument("--kge-epochs", type=int, default=KGE_DEFAULTS["kge_epochs"])
    p.add_argument("--kge-minutes", type=float, default=KGE_DEFAULTS["kge_minutes"])
    p.add_argument("--kge-batch", type=int, default=KGE_DEFAULTS["batch_size"])
    p.add_argument("--kge-lr", type=float, default=KGE_DEFAULTS["lr"])
    p.add_argument("--kge-margin", type=float, default=KGE_DEFAULTS["margin"])
    own, rest = p.parse_known_args(argv)
    rest = [a for a in rest if a != "--"]
    if "--tag" not in rest or not rest[rest.index("--tag") + 1].startswith("diag_kge"):
        raise SystemExit("kge.py: pass --tag diag_kge_<name> so the result is labelled exploratory")
    from linkpred import run
    settings = dict(model_name=own.kge, kge_epochs=own.kge_epochs, kge_minutes=own.kge_minutes,
                    batch_size=own.kge_batch, lr=own.kge_lr, margin=own.kge_margin)
    tag = rest[rest.index("--tag") + 1]
    with patched(**settings):
        sys.argv = ["linkpred.run"] + rest
        run.main()
    for path in (ROOT / "outputs" / "linkpred").glob(f"*@{tag}.json"):     # record which embedding model made it
        d = json.loads(path.read_text())
        d["settings"]["kge"] = {**settings, "library": "torch_geometric.nn.kge", "optimizer": "SparseAdam",
                                "negatives": "PyG KGEModel.random_sample (head or tail corrupted)",
                                "note": "EXPLORATORY (ledger E006); auroc = the KGE model's AUROC"}
        path.write_text(json.dumps(d, indent=2))


if __name__ == "__main__":
    main()
