"""
Export a KG builder Build to PyTorch Geometric HeteroData.

    from kgbuilder.pyg_export import to_heterodata, save_pyg, load_pyg

    hd = to_heterodata(build)       # Build -> HeteroData (CPU tensors, no features yet)
    save_pyg(hd, "outputs/pharos/graph.pt")
    hd2 = load_pyg("outputs/pharos/graph.pt")

Nodes are integer-indexed per category. Edges become (subject_category, predicate, object_category) typed
edge_index tensors. The mapping from KGX string id to integer index is stored as node_id on each node type,
and the KGX edge id is stored as edge_id on each edge type.

Node and edge *features* are not added here: link-prediction code adds them (Morgan fingerprints for
SmallMolecule, ESM-2 embeddings for Protein, one-hot TDL for Gene, etc.) because feature sets vary between
experiments.

Functions
---------
to_heterodata(build)           Build -> HeteroData
save_pyg(hd, path)             save to .pt
load_pyg(path)                 load from .pt
split_edges(hd, predicate_group, fractions, seed)
                               -> list of HeteroData with train_mask / val_mask / test_mask on edge types
summary(hd)                    -> str   counts per node/edge type
"""
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import torch
    from torch_geometric.data import HeteroData
    _HAS_PYG = True
except ImportError:
    _HAS_PYG = False


def _require_pyg():
    if not _HAS_PYG:
        raise ImportError("PyTorch Geometric is required: pip install torch torch_geometric")


def to_heterodata(build):
    """Convert a kgbuilder.engine.Build to a PyTorch Geometric HeteroData object.

    Parameters
    ----------
    build : kgbuilder.engine.Build
        The built graph (nodes and edges DataFrames with KGX columns).

    Returns
    -------
    torch_geometric.data.HeteroData
        Node types keyed by BioLink category (e.g. "Gene", "Protein").
        Edge types keyed by (subject_category, predicate, object_category) with short names
        (e.g. ("Gene", "gene_associated_with_condition", "Disease")).
        Each node type stores:
          - num_nodes: int
          - node_id: list of KGX string ids (same order as indices)
        Each edge type stores:
          - edge_index: LongTensor [2, E]
          - edge_id: list of KGX edge ids
          - n_records: LongTensor [E]  (merge count from the builder)
          - edge_layer: list of layer names (when the edges carry a 'layer' column, e.g. from make_graphs)
          - edge_knowledge_level: list of BioLink knowledge levels (when the edges carry 'knowledge_level');
            linkpred/eval_set.py uses both to pick the test pairs
    """
    _require_pyg()
    nodes, edges = build.nodes, build.edges
    if "subject_category" not in edges or "object_category" not in edges:
        # engine.build() drops these columns after use; take them from the node table
        cat = dict(zip(nodes["id"], nodes["category"]))
        edges = edges.assign(subject_category=edges["subject"].map(cat), object_category=edges["object"].map(cat))

    # Build per-category node index: category -> {kgx_id: int_index}
    cat_ids = {}   # category -> list of kgx ids (order = index)
    cat_map = {}   # category -> {kgx_id: int}
    for cat, grp in nodes.groupby("category"):
        ids = grp["id"].tolist()
        cat_ids[cat] = ids
        cat_map[cat] = {v: i for i, v in enumerate(ids)}

    hd = HeteroData()

    # Register node types
    for cat, ids in sorted(cat_ids.items()):
        short = _short_class(cat)
        hd[short].num_nodes = len(ids)
        hd[short].node_id = ids

    # Register edge types
    for (scat, pred, ocat), grp in edges.groupby(["subject_category", "predicate", "object_category"]):
        s_short = _short_class(scat)
        o_short = _short_class(ocat)
        p_short = _short_pred(pred)
        s_map = cat_map.get(scat, {})
        o_map = cat_map.get(ocat, {})
        # Map subject/object KGX ids to integer indices; skip edges whose nodes weren't in the node table
        s_idx = grp["subject"].map(s_map)
        o_idx = grp["object"].map(o_map)
        valid = s_idx.notna() & o_idx.notna()
        if not valid.any():
            continue
        s_arr = torch.from_numpy(s_idx[valid].astype(np.int64).values)
        o_arr = torch.from_numpy(o_idx[valid].astype(np.int64).values)
        edge_index = torch.stack([s_arr, o_arr], dim=0)
        key = (s_short, p_short, o_short)
        hd[key].edge_index = edge_index
        hd[key].edge_id = grp.loc[valid.values, "id"].tolist()
        if "layer" in grp.columns:
            hd[key].edge_layer = _shared_strings(grp.loc[valid.values, "layer"])
        if "knowledge_level" in grp.columns:
            hd[key].edge_knowledge_level = _shared_strings(grp.loc[valid.values, "knowledge_level"])
        if "n_records" in grp.columns:
            hd[key].n_records = torch.from_numpy(grp.loc[valid.values, "n_records"].astype(np.int64).values)

    return hd


def save_pyg(hd, path):
    """Save HeteroData to a .pt file."""
    _require_pyg()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(hd, path)


def load_pyg(path):
    """Load HeteroData from a .pt file."""
    _require_pyg()
    return torch.load(Path(path), weights_only=False)


def split_edges(hd, predicate_group, fractions=(0.8, 0.1, 0.1), seed=42):
    """Add train/val/test boolean masks to edge types whose predicate is in predicate_group.

    Parameters
    ----------
    hd : HeteroData
    predicate_group : list of str
        Short predicate names (e.g. ["gene_associated_with_condition"]).
    fractions : tuple of float
        (train, val, test) fractions; must sum to 1.
    seed : int

    Returns
    -------
    HeteroData (modified in place and returned)
    """
    _require_pyg()
    assert abs(sum(fractions) - 1.0) < 1e-6, f"fractions must sum to 1, got {sum(fractions)}"
    rng = np.random.RandomState(seed)
    for key in hd.edge_types:
        _, pred, _ = key
        if pred not in predicate_group:
            continue
        n = hd[key].edge_index.size(1)
        perm = rng.permutation(n)
        n_train = int(fractions[0] * n)
        n_val = int(fractions[1] * n)
        train_mask = torch.zeros(n, dtype=torch.bool)
        val_mask = torch.zeros(n, dtype=torch.bool)
        test_mask = torch.zeros(n, dtype=torch.bool)
        train_mask[perm[:n_train]] = True
        val_mask[perm[n_train:n_train + n_val]] = True
        test_mask[perm[n_train + n_val:]] = True
        hd[key].train_mask = train_mask
        hd[key].val_mask = val_mask
        hd[key].test_mask = test_mask
    return hd


def summary(hd):
    """One-line-per-type summary of a HeteroData object."""
    _require_pyg()
    lines = ["## PyG HeteroData summary", ""]
    lines.append("### Node types")
    for nt in sorted(hd.node_types):
        lines.append(f"- {nt}: {hd[nt].num_nodes:,} nodes")
    lines.append("")
    lines.append("### Edge types")
    for et in sorted(hd.edge_types):
        n = hd[et].edge_index.size(1)
        lines.append(f"- {et}: {n:,} edges")
    lines.append("")
    total_n = sum(hd[nt].num_nodes for nt in hd.node_types)
    total_e = sum(hd[et].edge_index.size(1) for et in hd.edge_types)
    lines.append(f"**Total: {total_n:,} nodes, {total_e:,} edges, {len(hd.node_types)} node types, {len(hd.edge_types)} edge types.**")
    return "\n".join(lines) + "\n"


# --- helpers ---

def _shared_strings(col):
    """Column -> list of str in which equal values are the same object (millions of edges pickle small)."""
    cats = pd.Categorical(col.fillna("").astype(str))
    names = [str(c) for c in cats.categories]
    return [names[c] for c in cats.codes]


def _short_class(biolink_class):
    """'biolink:SmallMolecule' -> 'SmallMolecule'."""
    return biolink_class.split(":")[-1] if ":" in biolink_class else biolink_class


def _short_pred(biolink_pred):
    """'biolink:gene_associated_with_condition' -> 'gene_associated_with_condition'."""
    return biolink_pred.split(":")[-1] if ":" in biolink_pred else biolink_pred
