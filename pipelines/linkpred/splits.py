"""
Split strategies for link prediction evaluation.

Splits used by run.py are keyed by STRING ids, never by edge position, so that every graph (G0..G4, OptimusKG)
holds out the same Pharos pairs:
  - hash_unit(keys, seed): one float in [0, 1) per key, depending only on (seed, key)
  - k-fold: fold = floor(unit(subject id + object id) * k)   -> every pair tested exactly once
  - cold-start: hide every pair whose disease (or target) has unit(node id) < frac
  - sparsity: hold out the pairs with unit(pair) < frac       -> 10% is a subset of 30%, etc.
  - prospective: new pairs in a newer release (ChEMBL 37 / OT 26.09) become the test set
A pair is held out everywhere at once: run.py removes it from the task edge type AND from every other edge type
that joins the same two node types (e.g. TIGA genetically_associated_with or an Open Targets edge for the same
gene-disease pair), otherwise the enrichment would leak the answer. No validation fold is held out: the models
have no early stopping, so every held-out pair is a test pair.

The index-based helpers at the end (kfold_masks, cold_start_masks, sparsity_masks, leakage_filter) are kept for
single-graph experiments and the tests; they are not used by run.py.
"""
import hashlib

import numpy as np
import pandas as pd

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False


def _require_torch():
    if not _HAS_TORCH:
        raise ImportError("torch is required for link prediction splits")


def hash_unit(keys, seed=0):
    """Takes an iterable of strings and a seed. Returns a float64 array in [0, 1), one value per key, that depends
    only on (seed, key): the same pair or node id gets the same value in every graph and on every machine."""
    out = np.empty(len(keys), dtype=np.float64)
    for i, k in enumerate(keys):
        h = hashlib.sha256(f"{seed}:{k}".encode()).digest()[:8]
        out[i] = int.from_bytes(h, "little") / 2.0 ** 64
    return out


def pair_key(subject_id, object_id):
    """The string that identifies a (subject, object) pair: 'subject id<TAB>object id'."""
    return f"{subject_id}\t{object_id}"


def kfold_of_pairs(pair_keys, k=5, seed=0):
    """Takes pair keys. Returns an int array: the fold (0..k-1) each pair is tested in."""
    return np.floor(hash_unit(pair_keys, seed) * k).astype(int)


def hidden_nodes(node_ids, frac=0.1, seed=0):
    """Takes node ids. Returns the set of ids hidden by a cold-start split (unit(id) < frac): a node is hidden
    in every graph that contains it."""
    u = hash_unit(node_ids, seed)
    hidden = {n for n, v in zip(node_ids, u) if v < frac}
    if not hidden and len(node_ids):             # a tiny graph where no id falls under the threshold: hide one
        hidden = {node_ids[int(np.argmin(u))]}
    return hidden


def sparsity_held_out(pair_keys, frac, seed=0):
    """Takes pair keys and a fraction. Returns a bool array: pairs removed (held out) at that sparsity. The
    pairs held out at 10% are a subset of those held out at 30%."""
    return hash_unit(pair_keys, seed) < frac


def kfold_masks(n_edges, k=5, seed=0):
    """Yield k (train_mask, val_mask, test_mask) boolean arrays over n_edges.

    Each fold: 1/k test, 1/k val (next fold's test), rest train.
    Every edge is tested exactly once across all folds.
    """
    _require_torch()
    rng = np.random.RandomState(seed)
    perm = rng.permutation(n_edges)
    fold_size = n_edges // k
    for i in range(k):
        test_start = i * fold_size
        test_end = test_start + fold_size if i < k - 1 else n_edges
        val_start = ((i + 1) % k) * fold_size
        val_end = val_start + fold_size if (i + 1) % k < k - 1 else n_edges
        test = torch.zeros(n_edges, dtype=torch.bool)
        test[torch.from_numpy(perm[test_start:test_end])] = True
        val = torch.zeros(n_edges, dtype=torch.bool)
        val[torch.from_numpy(perm[val_start:val_end])] = True
        val &= ~test
        yield ~(test | val), val, test


def cold_start_masks(edge_index, node_ids, frac=0.1, seed=0, side="object"):
    """Hide ALL edges touching a random frac of unique nodes on the given side.

    Parameters
    ----------
    edge_index : Tensor [2, E]
    node_ids : array-like, unique node identifiers (used for stratification logging only)
    frac : float, fraction of unique nodes to hide
    seed : int
    side : 'subject' (row 0) or 'object' (row 1)

    Returns (train_mask, val_mask, test_mask) boolean tensors.
    Val is half the hidden edges; test is the other half.
    """
    _require_torch()
    idx = 0 if side == "subject" else 1
    nodes = edge_index[idx].numpy()
    unique = np.unique(nodes)
    rng = np.random.RandomState(seed)
    n_hide = max(1, int(len(unique) * frac))
    hidden = set(rng.choice(unique, size=n_hide, replace=False))
    hidden_idx = np.where(np.isin(nodes, list(hidden)))[0]
    rng.shuffle(hidden_idx)
    mid = len(hidden_idx) // 2
    n = edge_index.shape[1]
    test = torch.zeros(n, dtype=torch.bool)
    test[torch.from_numpy(hidden_idx[:mid])] = True
    val = torch.zeros(n, dtype=torch.bool)
    val[torch.from_numpy(hidden_idx[mid:])] = True
    train = ~(test | val)
    return train, val, test


def prospective_mask(existing_pairs, new_pairs, edge_index, subject_map, object_map):
    """Edges whose (subject, object) pair is in new_pairs but NOT in existing_pairs become test.

    Parameters
    ----------
    existing_pairs : set of (subject_id, object_id) from the older release
    new_pairs : set of (subject_id, object_id) from the newer release
    edge_index : Tensor [2, E]
    subject_map, object_map : list/array mapping integer indices back to string ids

    Returns (train_mask, test_mask) boolean tensors. No val split (use k-fold val from the same graph).
    """
    _require_torch()
    truly_new = new_pairs - existing_pairs
    n = edge_index.shape[1]
    src, dst = edge_index[0].numpy(), edge_index[1].numpy()
    test = torch.zeros(n, dtype=torch.bool)
    for i in range(n):
        pair = (subject_map[src[i]], object_map[dst[i]])
        if pair in truly_new:
            test[i] = True
    train = ~test
    return train, test


def sparsity_masks(n_edges, remove_fracs=(0.1, 0.3, 0.5, 0.7), seed=0):
    """For each fraction, yield (train_mask, test_mask) where test = the removed fraction.

    All use the same random ordering so 10% is a subset of 30%, etc.
    """
    _require_torch()
    rng = np.random.RandomState(seed)
    perm = rng.permutation(n_edges)
    for frac in remove_fracs:
        n_remove = int(n_edges * frac)
        test = torch.zeros(n_edges, dtype=torch.bool)
        test[torch.from_numpy(perm[:n_remove])] = True
        train = ~test
        yield frac, train, test


def leakage_filter(train_mask, edge_index, subject_map, object_map, enrichment_pairs):
    """Zero out training edges whose (subject, object) pair also appears in enrichment_pairs.

    This prevents enrichment layers from leaking test information into training.
    Returns a new train_mask with leaked edges removed.
    """
    _require_torch()
    mask = train_mask.clone()
    src, dst = edge_index[0].numpy(), edge_index[1].numpy()
    for i in range(len(mask)):
        if mask[i]:
            pair = (subject_map[src[i]], object_map[dst[i]])
            if pair in enrichment_pairs:
                mask[i] = False
    return mask
