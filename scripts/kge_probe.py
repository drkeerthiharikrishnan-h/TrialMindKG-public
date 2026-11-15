"""Timing probe for the E006 graph-embedding runs (NOT a result; writes nothing under outputs/).

    python scripts/kge_probe.py [epochs] [dim] [graph] [device]     e.g.  python scripts/kge_probe.py 2 512 G2 cuda

Runs the first k-fold fold only, with the frozen wiring and the 1M cap, RotatE via pipelines/linkpred/kge.py, and prints
the preparation time, minutes per epoch, the number of training triples, GPU memory (when on CUDA) and the fold AUROC
(sanity only: a 2-epoch model is not a result).
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import torch  # noqa: E402
from linkpred import run, kge  # noqa: E402

EPOCHS = int(sys.argv[1]) if len(sys.argv) > 1 else 1
DIM = int(sys.argv[2]) if len(sys.argv) > 2 else 256
GRAPH = sys.argv[3] if len(sys.argv) > 3 else "G2"
DEVICE = sys.argv[4] if len(sys.argv) > 4 else "cpu"

orig_sets = run.held_out_sets


def first_fold(*a, **k):
    for item in orig_sets(*a, **k):
        yield item
        return


run.held_out_sets = first_fold
orig_eval = kge.kge_train_eval
stamp = {}


def timed(*a, **k):
    stamp["prep_done"] = time.time()
    return orig_eval(*a, **k)


t0 = time.time()
with kge.patched(model_name="rotate", kge_epochs=EPOCHS, kge_minutes=600, batch_size=16384, lr=0.01, margin=9.0):
    run._train_and_eval = timed
    out = run.run_experiment(GRAPH, "B", "kfold", n_seeds=1, device=DEVICE, epochs=EPOCHS, hidden=DIM,
                             max_edges=1000000, reverse_edges=True, learn_embeddings=True, num_bases=30,
                             neg_pool="training", positives="assertions", baselines=False, metapaths=False,
                             neg_match="degree", strata=True, test_levels=("knowledge_assertion",),
                             drop_levels=("text_co_occurrence",), log=print)
f = out[0]["folds"][0]
mem = f"; GPU peak {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB on {torch.cuda.get_device_name(0)}" \
    if DEVICE.startswith("cuda") else ""
print(f"PROBE {GRAPH} dim {DIM} on {DEVICE}: prep {stamp['prep_done'] - t0:.0f} s; fit {f['kge_train_minutes']} min for "
      f"{f['kge_epochs_done']} epoch(s) = {f['kge_train_minutes'] / max(f['kge_epochs_done'], 1):.2f} min/epoch; "
      f"triples {f['kge_n_triples']:,}{mem}; fold-1 AUROC {f['auroc']:.4f} (sanity only); test pairs {f['n_test']}")
