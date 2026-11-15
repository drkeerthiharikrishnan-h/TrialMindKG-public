"""
Layer ablation and controls for the link-prediction benchmark (docs/ENRICHMENT_DIAGNOSIS_PLAN.md, wave D). Every
edge of a graph built by make_graphs.py carries the layer it came from (edge_layer: 'pharos', 'opentargets_layer',
'chembl37', 'hpo_hierarchy', 'go_hierarchy', 'mondo_hierarchy', 'trialmindkg'), so one graph file (G2) gives all
the variants in memory, no new .pt files:

  D1 additive     keep_layers  = pharos + one layer           ("G0 + Open Targets", ...)
  D2 subtractive  drop_layers  = one layer                    ("G2 minus ChEMBL 37", ...)
  D3 shuffled     shuffle_layers = one layer: its edges' objects are permuted within each edge type, so every
                  node keeps its number of edges of that type (degree-preserving) but the content is random.
                  Real layer better than its shuffled twin = the content helps; equal = any harm is model capacity.
  D4 random drop  random_drop = share of Pharos-layer edges removed at random: the control for pruning (G1 / G3
                  must beat removing the same share at random, or the pruning is not pruning).

Functions take and return numpy arrays (no torch), so they are unit-tested in the cloud; run.py applies them to
the loaded graph with --keep-layers / --drop-layers / --shuffle-layers / --random-drop.
"""
import hashlib

import numpy as np

L0 = "pharos"


def _seed_int(seed, name):
    return int.from_bytes(hashlib.sha256(f"{seed}:{name}".encode()).digest()[:4], "little")


def layer_mask(edge_layer, layers):
    """Takes the per-edge layer names (list) and a collection of layer names. Returns a bool array: edge in one of
    those layers."""
    layers = set(layers)
    return np.fromiter((lv in layers for lv in edge_layer), dtype=bool, count=len(edge_layer))


def keep_mask(edge_layer, keep_layers=(), drop_layers=()):
    """Takes the per-edge layer names, the layers to keep (empty = all) and the layers to drop. Returns the bool
    array of edges that stay."""
    ok = np.ones(len(edge_layer), dtype=bool)
    if keep_layers:
        ok &= layer_mask(edge_layer, keep_layers)
    if drop_layers:
        ok &= ~layer_mask(edge_layer, drop_layers)
    return ok


def shuffle_objects(edge_index, mask, seed=0, name=""):
    """Takes an edge_index [2, E] (numpy), a bool mask of the edges to shuffle, a seed and the edge type's name.
    Returns a new edge_index where the objects of the masked edges are permuted among themselves (a seeded
    permutation, so the same in every run): each subject keeps its out-degree and each object its in-degree within
    the masked set, the pairs are random. Unmasked edges are unchanged."""
    out = np.array(edge_index, copy=True)
    idx = np.flatnonzero(mask)
    if len(idx) > 1:
        rng = np.random.default_rng(_seed_int(seed, f"shuffle:{name}"))
        out[1, idx] = out[1, idx[rng.permutation(len(idx))]]
    return out


def random_drop_mask(edge_layer, share, seed=0, name="", layer=L0):
    """Takes the per-edge layer names, the share of `layer` edges to remove, a seed and the edge type's name.
    Returns the bool array of edges that stay: round(share * n_layer_edges) of the layer's edges are removed,
    chosen by a seeded permutation; edges of other layers always stay."""
    stay = np.ones(len(edge_layer), dtype=bool)
    idx = np.flatnonzero(layer_mask(edge_layer, [layer]))
    k = int(round(share * len(idx)))
    if k:
        rng = np.random.default_rng(_seed_int(seed, f"drop:{name}"))
        stay[idx[rng.permutation(len(idx))[:k]]] = False
    return stay


def describe(keep_layers=(), drop_layers=(), shuffle_layers=(), random_drop=0.0):
    """Short label of an ablation for logs and result tags, e.g. 'keep pharos+chembl37, shuffle chembl37'.
    Empty string when nothing is changed."""
    parts = []
    if keep_layers:
        parts.append("keep " + "+".join(keep_layers))
    if drop_layers:
        parts.append("drop " + "+".join(drop_layers))
    if shuffle_layers:
        parts.append("shuffle " + "+".join(shuffle_layers))
    if random_drop:
        parts.append(f"random drop {random_drop:g} of {L0}")
    return ", ".join(parts)
