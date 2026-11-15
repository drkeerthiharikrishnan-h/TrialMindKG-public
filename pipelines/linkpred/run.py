"""
Main runner for link prediction experiments.

    python -m pipelines.linkpred.run [--graphs G0,G3] [--tasks A,B] [--splits kfold,cold_disease] [--seeds 5]
                                     [--device cpu] [--epochs 200] [--hidden 128] [--max-edges 0]
                                     [--sample-fraction 0.01] [--reverse-edges] [--learn-embeddings] [--num-bases 30]
                                     [--neg-pool training] [--positives curated] [--baselines]
                                     [--metapaths] [--neg-match degree] [--strata]
    (the last nine: docs/ENRICHMENT_DIAGNOSIS_PLAN.md waves A-C and H; the result JSON records them under
    settings.wiring)

--sample-fraction F keeps the share F of EVERY edge type (proportions preserved); --max-edges N keeps at most N
edges of each edge type (a fixed cap: the 13.4M-edge disease-association type is cut to N while small enrichment
layers stay whole, so enriched graphs look far more "enriched" than they are: the 2026-10-31 smoke run showed
G2/G3 ~0.82 vs G0/G1 ~0.87 under a 100,000 cap, which says nothing about the full graph). Both: a smoke test that fits a laptop (the full Pharos
graph has 16M edges; full runs belong on the GPU instance). Results with a cap are NOT comparable to full runs,
but capped graphs ARE comparable with each other: the kept edges are the N with the smallest hash of
(seed, edge id), so an edge kept in G0 is kept in every graph that still holds it (a pruned graph keeps the
same edges minus the pruned ones, plus the next ones in hash order), and the test pairs still come from G0.

Reads PyG HeteroData files from outputs/<graph>/heterodata.pt (built by make_graphs.py / make_g0.py).
Writes results to outputs/linkpred/<graph>_<task>_<split>.json and a summary report.

What is held out (the same for every graph, so G0 vs G3 is a fair comparison):
  - Only Pharos (L0) pairs of the task edge type are scored, and not text-mining pairs (knowledge_level
    text_co_occurrence: TIN-X, JensenLab text mining; they follow literature volume and would inflate the score;
    they stay in the graph as context). The reference set is G0's eligible pairs
    (--reference G0, outputs/g0/heterodata.pt); each graph is tested on exactly those pairs, mapped by node id,
    whether or not pruning removed the edge from that graph, with or without --max-edges (the cap removes
    training edges, never test pairs). Without a reference file the graph's own L0 pairs are used and the
    result JSON says so ("test_from": "self"); then graphs are NOT scored on the same pairs.
  - Folds / hidden nodes are assigned by a hash of the string ids (linkpred.splits), never by edge position.
  - A held-out pair is removed from training everywhere: from the task edge type (Pharos duplicates from other
    sources and Open Targets L1 edges included) and from every other edge type joining the same two node types
    (e.g. TIGA genetically_associated_with for the same gene-disease pair).
  - Test negatives are the SAME in every graph: for each test pair, the negative object is chosen by a hash of
    (seed, pair) among the objects of the reference pairs, redrawn while it is a known pair in this graph or the
    reference. Each result JSON records the test set's digest: equal digest = identical test pairs.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipelines"))

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

TASK_EDGES = {
    "A": ("biolink:SmallMolecule", "biolink:directly_physically_interacts_with", "biolink:Protein"),
    "B": ("biolink:Gene", "biolink:gene_associated_with_condition", "biolink:Disease"),
}

GRAPHS = ["G0", "G1", "G2", "G3", "G4", "OptimusKG"]
L0_LAYER = "pharos"          # edge_layer value of the Pharos layer (make_graphs: the source card stem)
REFERENCE_GRAPH = "G0"       # whose L0 pairs are the test pairs for every graph
EXCLUDED_LEVELS = ("text_co_occurrence",)   # knowledge levels never used as test items (kept as graph context)

SPLIT_NAMES = ["kfold", "cold_disease", "cold_target", "prospective", "sparsity"]


def load_graph(name):
    from kgbuilder.pyg_export import load_pyg
    path = ROOT / "outputs" / name.lower() / "heterodata.pt"
    if not path.exists():
        raise FileNotFoundError(f"Graph {name} not found at {path}")
    return load_pyg(path)


def find_task_edge_type(data, task):
    """Find the HeteroData edge type matching the task's (subject, predicate, object). pyg_export stores short
    names ('Gene', 'gene_associated_with_condition'), so the biolink: prefix is ignored when comparing."""
    want = tuple(x.split(":")[-1] for x in TASK_EDGES[task])
    for et in data.edge_types:
        if tuple(et) == want:
            return et
    raise KeyError(f"No edge type {want} in graph")


def node_features(node_ids, dim=64, seed=0):
    """Takes a list of node ids. Returns a float32 tensor [len(node_ids), dim] of standard-normal values, where
    each row depends only on (seed, node id): the same node gets the same vector in every graph (G0..G4),
    whatever the node order or the other node types. Needed for a fair baseline-vs-enriched comparison."""
    import hashlib
    import numpy as np
    rows = np.empty((len(node_ids), dim), dtype=np.float32)
    for i, nid in enumerate(node_ids):
        h = int.from_bytes(hashlib.sha256(f"{seed}:{nid}".encode()).digest()[:8], "little")
        rows[i] = np.random.default_rng(h).standard_normal(dim)
    return torch.from_numpy(rows)


def init_features(data, dim=64, seed=0):
    """Initialise node features as random embeddings (per node id, see node_features) if not already present.
    Node types without node_id fall back to one generator seeded by (seed, node type name)."""
    if not _HAS_TORCH:
        raise ImportError("torch required")
    import zlib
    for nt in data.node_types:
        if not hasattr(data[nt], "x") or data[nt].x is None:
            ids = getattr(data[nt], "node_id", None)
            if ids is not None:
                data[nt].x = node_features(ids, dim, seed)
            else:
                rng = torch.Generator().manual_seed(seed * 1_000_003 + zlib.crc32(nt.encode()))
                data[nt].x = torch.randn(data[nt].num_nodes, dim, generator=rng)
    return data


def cap_keep_indices(edge_ids, n, max_edges, seed=0, name=""):
    """Takes the edge ids of one edge type (a list, or None), its edge count n, the cap and a seed. Returns the
    sorted positions of the edges to keep: the max_edges edges with the smallest hash of (seed, edge id), so the
    choice depends on the edges' ids, not on their position or on the other edge types (a graph holding a
    subset of the same edges keeps the same ones). Without ids: a random choice seeded by (seed, edge type name).
    All positions when n <= max_edges."""
    import zlib
    from linkpred.splits import hash_unit
    if n <= max_edges:
        return np.arange(n)
    if edge_ids is not None and len(edge_ids) == n:
        order = np.argsort(hash_unit(edge_ids, seed), kind="stable")
        return np.sort(order[:max_edges])
    rng = np.random.RandomState((seed + zlib.crc32(name.encode())) % 2 ** 32)
    return np.sort(rng.choice(n, max_edges, replace=False))


def cap_size(n, max_edges=None, fraction=None):
    """How many edges of an edge type with n edges a smoke run keeps: at most max_edges (a fixed cap per type)
    and at most fraction * n rounded up (the same share of every type, so the types keep their relative sizes:
    a 100,000 cap cuts 13.4M disease-association edges to 0.7% but leaves a 50,000-edge layer whole, which
    makes the enrichment ~130x heavier than in the full graph; a 1% sample keeps the proportions). Both given:
    the smaller. Neither: n."""
    keep = n
    if max_edges:
        keep = min(keep, int(max_edges))
    if fraction:
        keep = min(keep, max(1, int(math.ceil(fraction * n))))
    return keep


KEEP_WHOLE = ("has_gene_product",)   # identity bridges, not evidence: never sampled (see cap_edges)


def cap_edges(data, max_edges, seed=0, fraction=None, keep_whole=()):
    """Keep at most cap_size(n, max_edges, fraction) edges of every edge type (see cap_keep_indices: by hash of
    the edge id, so the same edges survive in every graph), so a smoke test fits in a laptop's memory. Edge
    attributes stored per edge (edge_id, edge_layer, n_records) are cut the same way. Returns the data object
    (changed in place); max_edges None or 0 and fraction None or 0 = no cap."""
    if not max_edges and not fraction:
        return data
    for et in data.edge_types:
        if et[1] in keep_whole:
            continue
        n = data[et].edge_index.size(1)
        size = cap_size(n, max_edges, fraction)
        if n <= size:
            continue
        keep = cap_keep_indices(getattr(data[et], "edge_id", None), n, size, seed, "|".join(et))
        subset_edges(data[et], keep, n)
    return data


def subset_edges(store, keep, n):
    """Takes one edge type's store, the sorted positions to keep (numpy int array) and its edge count n. Keeps
    those edges and cuts every per-edge attribute (tensor or list of length n) the same way, in place."""
    keep = np.asarray(keep, dtype=np.int64)
    keep_t = torch.from_numpy(keep)
    for key in list(store.keys()):
        val = store[key]
        if key == "edge_index":
            store[key] = val[:, keep_t]
        elif torch.is_tensor(val) and val.dim() >= 1 and val.size(0) == n:
            store[key] = val[keep_t]
        elif isinstance(val, list) and len(val) == n:
            store[key] = [val[j] for j in keep]


def apply_ablation(data, keep_layers=(), drop_layers=(), shuffle_layers=(), random_drop=0.0, seed=0,
                   keep_types=()):
    """Wave D (linkpred.ablate) on the loaded graph, in place: keep only edges of keep_layers (empty = all), drop
    edges of drop_layers, permute the objects of shuffle_layers' edges within each edge type (degree-preserving
    random control), and remove the share random_drop of the Pharos-layer edges at random (control for pruning).
    Edge types left empty are deleted, except keep_types (the task type). Returns {'removed', 'shuffled',
    'types_deleted'}. Raises ValueError when the graph carries no edge_layer (a plain G0 export)."""
    from linkpred.ablate import keep_mask, layer_mask, shuffle_objects, random_drop_mask
    info = {"removed": 0, "shuffled": 0, "types_deleted": []}
    if not (keep_layers or drop_layers or shuffle_layers or random_drop):
        return info
    for et in list(data.edge_types):
        layer = getattr(data[et], "edge_layer", None)
        n = data[et].edge_index.size(1)
        if layer is None:
            raise ValueError(f"edge type {et} has no edge_layer: layer ablation needs a graph from make_graphs.py")
        name = "|".join(et)
        stay = keep_mask(layer, keep_layers, drop_layers)
        if random_drop:
            stay &= random_drop_mask(layer, random_drop, seed, name)
        if shuffle_layers:
            mask = layer_mask(layer, shuffle_layers)
            if mask.any():
                ei = shuffle_objects(data[et].edge_index.numpy(), mask, seed, name)
                data[et].edge_index = torch.from_numpy(ei)
                info["shuffled"] += int(mask[stay].sum())
        if not stay.all():
            info["removed"] += int((~stay).sum())
            subset_edges(data[et], np.flatnonzero(stay), n)
        if data[et].edge_index.size(1) == 0 and et not in keep_types:
            del data[et]
            info["types_deleted"].append(et)
    return info


CONTEXT_SUFFIX = "_context"      # was "_text_mined" until 2026-11-02: since --positives assertions the relation also
TEXT_MINED_SUFFIX = CONTEXT_SUFFIX  # holds statistical and not_provided edges (BUILD_ROLES Code item 3; name only)


def split_text_mining(data, et, excluded_levels=EXCLUDED_LEVELS):
    """Moves the task edge type's text-mining edges (knowledge level in excluded_levels) into their own edge type
    (subject type, predicate + '_context', object type), keeping every per-edge attribute aligned. The model
    then trains its positives on curated / statistical pairs, the kind the test pairs are, while the text-mining
    pairs stay in the graph as context (a separate relation). Without this, 98% of the task B training positives
    are text co-occurrence (13.1M of 13.4M Pharos edges) and the model learns to predict literature volume.
    Returns the number of edges moved (0 when the type carries no knowledge levels)."""
    level = getattr(data[et], "edge_knowledge_level", None)
    if level is None:
        return 0
    move = np.fromiter((lv in excluded_levels for lv in level), dtype=bool, count=len(level))
    if not move.any():
        return 0
    keep = ~move
    new_et = (et[0], et[1] + CONTEXT_SUFFIX, et[2])
    store, dest = data[et], data[new_et]
    for key in list(store.keys()):
        val = store[key]
        if key == "edge_index":
            dest.edge_index, store.edge_index = val[:, torch.from_numpy(move)], val[:, torch.from_numpy(keep)]
        elif isinstance(val, torch.Tensor) and val.dim() >= 1 and val.size(0) == len(move):
            setattr(dest, key, val[torch.from_numpy(move)]); setattr(store, key, val[torch.from_numpy(keep)])
        elif isinstance(val, list) and len(val) == len(move):
            setattr(dest, key, [v for v, m in zip(val, move) if m]); setattr(store, key, [v for v, m in zip(val, keep) if m])
    return int(move.sum())


def positive_exclusions(data, et, mode):
    """Knowledge levels moved out of the task type by --positives: 'all' -> none, 'curated' -> the text-mining
    levels (EXCLUDED_LEVELS), 'assertions' -> every level except knowledge_assertion (statistical associations
    such as Open Targets overall scores and TIGA become context too)."""
    if mode == "all":
        return ()
    if mode == "curated":
        return EXCLUDED_LEVELS
    if mode == "assertions":
        level = getattr(data[et], "edge_knowledge_level", None) or []
        return tuple(sorted(set(level) - {"knowledge_assertion"}))
    raise ValueError(f"Unknown positives mode: {mode}")


def drop_small_edge_types(data, min_edges, keep=()):
    """Removes every edge type with fewer than min_edges edges (after the cap), except those in keep (the task
    type). Returns the list of removed types. Each relation type costs the R-GCN its own weight matrices; the
    2026-11-01 G2 smoke run had 42 types, 20 of them under 100 edges, pure noise for the model."""
    gone = []
    for et in list(data.edge_types):
        if et in keep or data[et].edge_index.size(1) >= min_edges:
            continue
        del data[et]
        gone.append(et)
    return gone


def training_negative_pool(data, et, train_mask, candidates_idx, mode="all"):
    """Takes the graph, the task edge type, its training mask, the test candidates' object indices and a mode.
    Returns the LongTensor of object indices training negatives are drawn from, or None for 'all' (every node of
    the object type, the old behaviour). 'training': objects with at least one training edge of the task type;
    'reference': the test candidates (objects of G0's pairs present in this graph). Both make the training task
    match the test task, whose negatives are always Pharos diseases; in an enriched graph 'all' includes every
    MONDO / EFO term with no gene edge, an easier job that does not transfer."""
    if mode == "all":
        return None
    if mode == "training":
        return torch.unique(data[et].edge_index[1][train_mask])
    if mode == "reference":
        return torch.tensor(sorted(candidates_idx), dtype=torch.long)
    raise ValueError(f"Unknown negative pool: {mode}")


def edge_pair_keys(data, et, reverse=False):
    """Takes the graph and an edge type. Returns one key per edge: 'subject id<TAB>object id' (KGX ids).
    reverse=True builds the key as object<TAB>subject (for an edge type that runs the other way round)."""
    from linkpred.splits import pair_key
    s_ids, o_ids = data[et[0]].node_id, data[et[2]].node_id
    ei = data[et].edge_index.numpy()
    if reverse:
        return [pair_key(o_ids[o], s_ids[s]) for s, o in zip(ei[0], ei[1])]
    return [pair_key(s_ids[s], o_ids[o]) for s, o in zip(ei[0], ei[1])]


def eligible_mask(data, et, excluded_levels=EXCLUDED_LEVELS, test_levels=()):
    """Bool array: which edges of the edge type may be test items: Pharos layer (all edges when the graph carries
    no edge_layer attribute, e.g. a plain G0 export) and not an excluded knowledge level (text mining; all edges
    count when the graph carries no edge_knowledge_level attribute). test_levels (--test-levels): when given,
    ONLY edges of these knowledge levels are test items, e.g. ('knowledge_assertion',) = the curated Pharos
    sources (CTD, Monarch, UniProt Disease, MedlinePlus, UniProtKB-KW, AmyCo, DrugCentral indication; 39,513
    unique pairs on 2026-11-02) instead of every non-text-mining pair (250,314 pairs, 63% of them Expression Atlas
    differential-expression hits on 107 diseases, 84% on diseases with more than 100 genes)."""
    n = data[et].edge_index.size(1)
    layer = getattr(data[et], "edge_layer", None)
    ok = np.ones(n, dtype=bool) if layer is None else np.fromiter((l == L0_LAYER for l in layer), dtype=bool, count=n)
    level = getattr(data[et], "edge_knowledge_level", None)
    if level is not None:
        ok &= np.fromiter((lv not in excluded_levels for lv in level), dtype=bool, count=n)
        if test_levels:
            ok &= np.fromiter((lv in test_levels for lv in level), dtype=bool, count=n)
    return ok


def drop_knowledge_levels(data, levels):
    """Removes, from every edge type that carries edge_knowledge_level, the edges whose level is in `levels`
    (--drop-levels text_co_occurrence: the 13.1M TIN-X / JensenLab text-mining gene-disease edges leave the
    graph entirely, so the FULL remaining Pharos graph fits a laptop; they are not test items anyway). Edge types
    left empty are deleted. Returns {edge type name: edges removed}."""
    removed = {}
    if not levels:
        return removed
    for et in list(data.edge_types):
        level = getattr(data[et], "edge_knowledge_level", None)
        if level is None:
            continue
        n = data[et].edge_index.size(1)
        gone = np.fromiter((lv in levels for lv in level), dtype=bool, count=n)
        if not gone.any():
            continue
        removed["|".join(et)] = int(gone.sum())
        subset_edges(data[et], np.flatnonzero(~gone), n)
        if data[et].edge_index.size(1) == 0:
            del data[et]
    return removed


l0_mask = eligible_mask   # old name


def digest(ref_keys, excluded_levels=EXCLUDED_LEVELS, test_levels=()):
    """Short sha256 of the sorted test pairs and the exclusion / inclusion rules: equal digests = identical test set."""
    h = hashlib.sha256()
    h.update(repr(tuple(excluded_levels)).encode())
    if test_levels:
        h.update(repr(tuple(sorted(test_levels))).encode())
    for k in sorted(ref_keys):
        h.update(k.encode() + b"\n")
    return h.hexdigest()[:16]


def reference_pairs(data, task, test_levels=()):
    """Takes a graph and a task. Returns the sorted unique eligible pair keys of its task edge type (Pharos layer,
    not text mining; only test_levels when given): the pairs every graph is tested on when this graph is the
    reference (G0)."""
    et = find_task_edge_type(data, task)
    keys = edge_pair_keys(data, et)
    return sorted(set(k for k, keep in zip(keys, eligible_mask(data, et, test_levels=test_levels)) if keep))


def load_reference(task, graph_name, max_edges=None, reference=REFERENCE_GRAPH, root=ROOT, data=None, test_levels=()):
    """Returns (pair keys, name) for the test pairs: the reference graph's (G0's) eligible L0 pairs when its file
    exists; else (None, 'self'). An edge cap (--max-edges, smoke test) does not change this: the cap cuts
    training edges, and every capped graph is still scored on the same G0 pairs (before 2026-10-31 a capped run
    used each graph's own pairs, so G0..G3 were not comparable). For the reference graph itself pass its loaded,
    UNCAPPED graph as `data`: its pairs are taken from that, so a capped G0 is scored on the same pairs as the
    other graphs (a second bug of the same kind, found by the 2026-10-31 smoke re-run: G0 had 430 test pairs
    per fold, G1-G3 49,000)."""
    if not reference:
        return None, "self"
    if graph_name == reference:
        return (None, "self") if data is None else (reference_pairs(data, task, test_levels), reference)
    path = Path(root) / "outputs" / reference.lower() / "heterodata.pt"
    if not path.exists():
        return None, "self"
    return reference_pairs(load_graph(reference), task, test_levels), reference


def held_out_sets(ref_keys, split, seed, src_ids, dst_ids, k=5):
    """Yields, per fold of the split, (fold label, set of held-out pair keys, hidden): hidden is None, or for a
    cold-start split (side, set of hidden node ids) with side 0 = subject (cold_target) / 1 = object (cold_disease).
    kfold: k folds by pair hash; cold start: every pair whose node on that side is hidden; sparsity_<frac>: the
    pairs with unit < frac. ref_keys: the pair keys that can be tested."""
    from linkpred.splits import kfold_of_pairs, hidden_nodes, sparsity_held_out
    if split == "kfold":
        folds = kfold_of_pairs(ref_keys, k=k, seed=seed)
        for i in range(k):
            yield f"fold {i + 1}/{k}", {p for p, f in zip(ref_keys, folds) if f == i}, None
    elif split in ("cold_disease", "cold_target"):
        side = 1 if split == "cold_disease" else 0
        hidden = hidden_nodes(dst_ids if side == 1 else src_ids, frac=0.1, seed=seed)
        yield split, {p for p in ref_keys if p.split("\t")[side] in hidden}, (side, hidden)
    elif split.startswith("sparsity"):
        frac = float(split.split("_")[1]) if "_" in split else 0.5
        held = sparsity_held_out(ref_keys, frac, seed=seed)
        yield split, {p for p, h in zip(ref_keys, held) if h}, None
    else:
        raise ValueError(f"Unknown split: {split}")


def _held_mask(keys, test_keys, side_ids=None, hidden_ids=None):
    """Bool array over edges: the edge restates a held-out pair, or (cold start) touches a hidden node."""
    out = np.fromiter((k in test_keys for k in keys), dtype=bool, count=len(keys))
    if hidden_ids is not None:
        out |= np.fromiter((n in hidden_ids for n in side_ids), dtype=bool, count=len(side_ids))
    return out


def split_graph(data, et, test_keys, hidden, src_index, dst_index, task_keys, other_keys=None):
    """Takes the graph, the task edge type, the held-out pair keys, hidden = None or (side, hidden node ids) for a
    cold-start split, node id -> index maps and the task edges' pair keys. Returns (test_ei, train_mask,
    edge_masks, info): test_ei = the held-out pairs as a [2, T] index tensor (pairs whose node is missing in this
    graph are dropped and counted), train_mask = task edges that may be trained on, edge_masks = {other edge type
    joining the same two node types: keep mask} with the edges that restate a held-out pair (or touch a hidden
    node) removed, info = the counts. other_keys: {edge type: pair keys} cache filled by this function."""
    src_type, _, dst_type = et
    other_keys = {} if other_keys is None else other_keys
    pairs = [(src_index.get(a), dst_index.get(b)) for a, b in (p.split("\t") for p in sorted(test_keys))]
    kept = [(a, b) for a, b in pairs if a is not None and b is not None]
    test_ei = torch.tensor(kept, dtype=torch.long).t() if kept else torch.zeros(2, 0, dtype=torch.long)

    hidden_type, hidden_ids = (None, None) if hidden is None else ((src_type, dst_type)[hidden[0]], hidden[1])

    def side_ids(other):
        """Node ids of the hidden node type on each edge of `other` (None when no cold start)."""
        if hidden_type is None:
            return None
        pos = 0 if other[0] == hidden_type else 1
        ids = data[other[pos * 2]].node_id
        return [ids[i] for i in data[other].edge_index.numpy()[pos]]

    train_mask = torch.from_numpy(~_held_mask(task_keys, test_keys, side_ids(et), hidden_ids))
    edge_masks, removed = {}, 0
    for other in data.edge_types:
        if other == et:
            continue
        if (other[0], other[2]) == (src_type, dst_type):
            keys = other_keys.setdefault(other, edge_pair_keys(data, other))
        elif (other[0], other[2]) == (dst_type, src_type):
            keys = other_keys.setdefault(other, edge_pair_keys(data, other, reverse=True))
        else:
            continue
        keep = ~_held_mask(keys, test_keys, side_ids(other), hidden_ids)
        removed += int((~keep).sum())
        edge_masks[other] = torch.from_numpy(keep)
    info = {"n_test": test_ei.size(1), "n_test_missing_node": len(pairs) - len(kept),
            "n_train_task": int(train_mask.sum()), "n_leak_edges_removed": removed}
    return test_ei, train_mask, edge_masks, info


def choose_negative(pair_key_str, candidates, known_keys, seed, rounds=20):
    """Takes one test pair key, the sorted candidate object ids, the known pair keys and a seed. Returns the
    object id of the negative: candidate number sha256(seed:attempt:pair) mod len(candidates), redrawn up to
    `rounds` times while (subject, candidate) is a known pair. Depends only on its inputs, so the same pair gets
    the same negative in every graph that holds the candidate."""
    from linkpred.splits import pair_key
    subject = pair_key_str.split("\t")[0]
    for attempt in range(rounds):
        h = hashlib.sha256(f"{seed}:{attempt}:{pair_key_str}".encode()).digest()[:8]
        cand = candidates[int.from_bytes(h, "little") % len(candidates)]
        if pair_key(subject, cand) not in known_keys:
            return cand
    return cand


def negative_ids(test_keys, candidates, known_keys, seed, rounds=20, pick=None):
    """Takes the held-out pair keys, the sorted candidate object ids, the known pair keys, a seed and optionally
    pick = a function true object id -> the candidates to draw from (strata.matched_candidates, --neg-match
    degree). Returns the negative object id for each test pair, in order (see choose_negative)."""
    return [choose_negative(k, pick(k.split("\t")[1]) if pick else candidates, known_keys, seed, rounds)
            for k in test_keys]


def sample_negatives(test_keys, candidates, known_keys, dst_index, seed, rounds=20, pick=None):
    """Takes the held-out pair keys (sorted, nodes present in this graph), the sorted candidate object ids (the
    objects of the reference pairs that exist in this graph), the known pair keys (this graph + reference), the
    object id -> index map, a seed and an optional pick (see negative_ids). Returns a LongTensor [T] of negative
    object indices, one per test pair, in the same order as the test pairs (identical across graphs)."""
    neg = [dst_index[c] for c in negative_ids(test_keys, candidates, known_keys, seed, rounds, pick)]
    return torch.tensor(neg, dtype=torch.long) if neg else torch.zeros(0, dtype=torch.long)


def result_name(graph, task, split, tag=""):
    """File name of one result: '<graph>_<task>_<split>.json', or '<graph>_<task>_<split>@<tag>.json' for a tagged
    variant (an ablation, a wiring change), so variants sit beside the plain run instead of overwriting it."""
    if tag and any(ch in tag for ch in "/\\ "):
        raise ValueError(f"tag must not contain '/', '\\' or spaces: {tag!r}")
    return f"{graph}_{task}_{split}{'@' + tag if tag else ''}.json"


def collect_results(out_dir):
    """Takes the results folder (outputs/linkpred). Returns {graph: {task: {split: summary}}} read from every
    <graph>_<task>_<split>.json in it, so the report covers all runs on disk, not only the graphs of the last
    command (a re-run of one graph, e.g. `--graphs G0`, keeps the other graphs' rows)."""
    results = {}
    for path in sorted(Path(out_dir).glob("*_*_*.json")):
        graph, task, split = path.stem.split("_", 2)
        try:
            summary = json.loads(path.read_text())["summary"]
        except (ValueError, KeyError, TypeError):
            continue
        results.setdefault(graph, {}).setdefault(task, {})[split] = summary
    return results


WIRING_DEFAULTS = {"reverse_edges": False, "learn_embeddings": False, "num_bases": None, "neg_pool": "all",
                   "positives": "all", "baselines": False, "min_type_edges": 0,
                   "metapaths": False, "metapath_len": 3, "neg_match": "none", "strata": False,
                   "keep_layers": (), "drop_layers": (), "shuffle_layers": (), "random_drop": 0.0,
                   "keep_whole": KEEP_WHOLE, "test_levels": (), "drop_levels": ()}


def average_folds(fold_results):
    """Takes the per-fold metric dicts. Returns the mean of every numeric key over the folds that HAVE it (a degree
    stratum can be empty in one fold), plus non-numeric values (e.g. the best meta-path shapes) from the first
    fold that has them."""
    keys = []
    for f in fold_results:
        keys += [k for k in f if k not in keys]
    avg = {}
    for k in keys:
        vals = [f[k] for f in fold_results if k in f]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            avg[k] = sum(vals) / len(vals)
        else:
            avg[k] = vals[0]
    return avg


def run_experiment(graph_name, task, split, n_seeds=5, device="cpu", hidden=128, out=64, epochs=200, lr=0.01,
                   max_edges=None, data=None, reference=None, log=print, sample_fraction=None, **wiring):
    """Run one (graph, task, split) experiment across seeds. Returns a list of per-seed metric dicts
    (k-fold: the mean over folds), each with the test-set bookkeeping (n_test, n_leak_edges_removed, test_set_digest).
    max_edges: cap per edge type, sample_fraction: share of every edge type kept (smoke tests on a laptop; see
    cap_size); data: an already loaded HeteroData (tests);
    reference: a list of pair keys or a HeteroData whose L0 task pairs are the test pairs (default: G0's file).
    wiring (docs/ENRICHMENT_DIAGNOSIS_PLAN.md, waves A-B; defaults in WIRING_DEFAULTS): reverse_edges,
    learn_embeddings, num_bases (models.RGCNEncoder), neg_pool (training_negative_pool), positives 'all' |
    'curated' (split_text_mining), baselines (degree, untrained and path-heuristic AUROCs per fold), metapaths
    (linkpred.metapaths: the meta-path-only model, coverage and best shapes; metapath_len = longest shape),
    neg_match 'none' | 'degree' (test negatives from the true disease's degree bin, linkpred.strata), strata
    (AUROC per disease and per gene degree bin: keys stratum_disease_<bin> / stratum_gene_<bin>, with _n counts),
    keep_layers / drop_layers / shuffle_layers / random_drop (apply_ablation, wave D).
    Each per-seed dict also holds 'folds': the numeric metrics of every fold, for paired comparisons
    (linkpred.compare)."""
    unknown = set(wiring) - set(WIRING_DEFAULTS)
    if unknown:
        raise TypeError(f"unknown options: {sorted(unknown)}")
    w = {**WIRING_DEFAULTS, **wiring}
    given = data is not None                     # a graph handed in (tests) is never compared with the G0 file
    data = load_graph(graph_name) if data is None else data
    if reference is None:                        # BEFORE the cap: the test pairs are never cut by --max-edges
        ref_keys, test_from = (None, "self") if given else load_reference(task, graph_name, max_edges, data=data,
                                                                         test_levels=w["test_levels"])
    if w["drop_levels"]:                           # AFTER the reference pairs are taken (test pairs untouched) and
        gone = drop_knowledge_levels(data, w["drop_levels"])   # BEFORE the cap (the cap then bounds PPI, not the task type)
        log(f"    {graph_name}: edges of knowledge levels {list(w['drop_levels'])} removed: "
            + (", ".join(f"{k} {v:,}" for k, v in gone.items()) or "none"))
    if w["positives"] != "all":                    # BEFORE the cap: the positives type (e.g. the 52k curated edges) stays
        levels = positive_exclusions(data, find_task_edge_type(data, task), w["positives"])   # whole, the cap bounds
        moved = split_text_mining(data, find_task_edge_type(data, task), levels)              # the context relation
        log(f"    {graph_name}: {moved:,} task edges of levels {list(levels)} moved to a context relation")
    data = cap_edges(data, max_edges, fraction=sample_fraction, keep_whole=w["keep_whole"])
    if w["keep_layers"] or w["drop_layers"] or w["shuffle_layers"] or w["random_drop"]:
        from linkpred.ablate import describe
        ab = apply_ablation(data, w["keep_layers"], w["drop_layers"], w["shuffle_layers"], w["random_drop"],
                            keep_types=(find_task_edge_type(data, task),))
        log(f"    {graph_name}: ablation ({describe(w['keep_layers'], w['drop_layers'], w['shuffle_layers'], w['random_drop'])}): "
            f"{ab['removed']:,} edges removed, {ab['shuffled']:,} shuffled, {len(ab['types_deleted'])} edge types emptied")
    if w["min_type_edges"]:
        gone = drop_small_edge_types(data, w["min_type_edges"], keep=(find_task_edge_type(data, task),))
        log(f"    {graph_name}: {len(gone)} edge types with fewer than {w['min_type_edges']:,} edges dropped")
    data = init_features(data, dim=hidden)
    sizes = {"|".join(et): int(data[et].edge_index.size(1)) for et in data.edge_types}
    log(f"    {graph_name}: {sum(int(data[nt].num_nodes) for nt in data.node_types):,} nodes; edges after the cap: "
        + ", ".join(f"{k} {v:,}" for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])))
    et = find_task_edge_type(data, task)
    src_type, _, dst_type = et

    if reference is None:
        pass
    elif isinstance(reference, list):
        ref_keys, test_from = reference, "given"
    else:
        ref_keys, test_from = reference_pairs(reference, task, w["test_levels"]), "given"
    task_keys = edge_pair_keys(data, et)
    if ref_keys is None:
        ref_keys = sorted(set(k for k, keep in zip(task_keys, eligible_mask(data, et, test_levels=w["test_levels"])) if keep))
    src_ids, dst_ids = data[src_type].node_id, data[dst_type].node_id
    src_index = {n: i for i, n in enumerate(src_ids)}
    dst_index = {n: i for i, n in enumerate(dst_ids)}
    known_keys = set(task_keys) | set(ref_keys)
    candidates = sorted({k.split("\t")[1] for k in ref_keys} & set(dst_index))
    test_digest = digest(ref_keys, test_levels=w["test_levels"])
    if w["neg_match"] not in ("none", "degree"):
        raise ValueError(f"Unknown neg_match: {w['neg_match']}")
    from linkpred.strata import degree_bins, matched_candidates, match_bins
    dst_bin, src_bin = degree_bins(ref_keys, side=1), degree_bins(ref_keys, side=0)
    pick = matched_candidates(candidates, match_bins(ref_keys, side=1)) if w["neg_match"] == "degree" else None
    other_keys = {}
    all_metrics = []

    for seed in range(n_seeds):
        fold_results = []
        for label, test_keys, hidden_ids in held_out_sets(ref_keys, split, seed, src_ids, dst_ids):
            test_ei, train_mask, edge_masks, info = split_graph(data, et, test_keys, hidden_ids, src_index,
                                                                dst_index, task_keys, other_keys)
            if test_ei.size(1) == 0:
                raise ValueError(f"{graph_name} {task} {split} {label}: no test pairs found in this graph")
            present = [k for k in sorted(test_keys)
                       if k.split("\t")[0] in src_index and k.split("\t")[1] in dst_index]
            neg_ei = torch.stack([test_ei[0], sample_negatives(present, candidates, known_keys, dst_index, seed,
                                                               pick=pick)])
            cand_idx = [dst_index[c] for c in candidates]
            pool = training_negative_pool(data, et, train_mask, cand_idx, w["neg_pool"])
            strata = None
            if w["strata"]:
                strata = {"disease": [dst_bin[k.split("\t")[1]] for k in present],
                          "gene": [src_bin[k.split("\t")[0]] for k in present]}
            m = _train_and_eval(data, et, train_mask, edge_masks, test_ei, neg_ei, hidden, out, epochs, lr,
                                device, seed, reverse=w["reverse_edges"], learn=w["learn_embeddings"],
                                num_bases=w["num_bases"], neg_pool=pool, baselines=w["baselines"],
                                metapaths=w["metapath_len"] if w["metapaths"] else 0, candidates_idx=cand_idx,
                                strata=strata)
            m.update(info)
            extra = "".join(f", {k[6:]} {v:.4f}" for k, v in m.items() if k.startswith("auroc_"))
            log(f"    seed {seed} {label}: AUROC {m['auroc']:.4f} (test pairs {info['n_test']}, "
                f"leak edges removed {info['n_leak_edges_removed']}){extra}")
            if "metapath_coverage" in m:
                log(f"      meta-paths: {m['metapath_n_shapes']} shapes ({m['metapath_n_shapes_dropped']} beyond the "
                    f"cap), test pairs with a path {m['metapath_coverage']:.1%}; best: "
                    + "; ".join(f"{r['shape']} {r['auroc']:.3f}" for r in m["metapath_shapes"][:3]))
            if strata:
                log("      strata: " + ", ".join(f"{k[8:]} {v:.3f} (n {m[k + '_n']})" for k, v in m.items()
                                                  if k.startswith("stratum_") and not k.endswith("_n")))
            fold_results.append(m)
        avg = average_folds(fold_results)
        avg["folds"] = [{k: v for k, v in f.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                        for f in fold_results]
        avg["test_from"] = test_from
        avg["test_set_digest"] = test_digest
        all_metrics.append(avg)

    return all_metrics


def _train_and_eval(data, task_et, train_mask, edge_masks, test_ei, neg_ei, hidden, out, epochs, lr, device, seed,
                    reverse=False, learn=False, num_bases=None, neg_pool=None, baselines=False, metapaths=0,
                    candidates_idx=None, strata=None):
    """Train the R-GCN on the graph minus the held-out pairs, then score the test pairs against the negatives.
    Returns {'auroc', 'auprc'} plus, with baselines=True, 'auroc_untrained' (the model before any training),
    'auroc_degree_*' and 'auroc_cn' / 'auroc_aa' / 'auroc_path3' (linkpred.baselines) on the same pairs;
    metapaths = N > 0: the meta-path-only model with shapes up to N steps (linkpred.metapaths.metapath_aurocs,
    training negatives from candidates_idx); strata = {name: one label per test pair}: AUROC per stratum
    (linkpred.strata.stratified_aurocs, keys stratum_<name>_<label>)."""
    from linkpred.models import LinkPredictor, RGCNEncoder, train_epoch, masked_edges
    from linkpred.evaluate import score_edges, classification_metrics

    torch.manual_seed(seed)
    learn_nodes = {nt: data[nt].x for nt in data.node_types if hasattr(data[nt], "x")} if learn else None
    encoder = RGCNEncoder(data.metadata(), hidden=hidden, out=out, reverse=reverse, learn_nodes=learn_nodes,
                          num_bases=num_bases)
    rel_map = {et: i for i, et in enumerate(data.edge_types)}
    model = LinkPredictor(encoder, len(data.edge_types), out).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    src_type, _, dst_type = task_et
    x_dict = {nt: data[nt].x.to(device) for nt in data.node_types if hasattr(data[nt], "x")}
    edge_dict = masked_edges(data, task_et, train_mask, device, edge_masks)
    ri = torch.full((test_ei.size(1),), rel_map[task_et], dtype=torch.long)

    def evaluate(keep_scores=False):
        model.eval()
        with torch.no_grad():
            z = model.encode(x_dict, edge_dict)
        pos = score_edges(model, z, test_ei, src_type, dst_type, ri, device)
        neg = score_edges(model, z, neg_ei, src_type, dst_type, ri, device)
        m = classification_metrics(pos, neg)
        return (m, pos, neg) if keep_scores else m

    extra = {}
    if baselines:
        from linkpred.baselines import degree_aurocs, heuristic_aurocs
        extra["auroc_untrained"] = evaluate()["auroc"]
        tr = edge_dict[task_et].cpu()
        extra.update(degree_aurocs(tr[0], tr[1], data[src_type].num_nodes, data[dst_type].num_nodes, test_ei, neg_ei))
        counts = {nt: int(data[nt].num_nodes) for nt in data.node_types}
        extra.update(heuristic_aurocs({et: ei.cpu() for et, ei in edge_dict.items()}, counts, src_type, dst_type,
                                      test_ei, neg_ei))
    if metapaths:
        from linkpred.metapaths import metapath_aurocs
        counts = {nt: int(data[nt].num_nodes) for nt in data.node_types}
        extra.update(metapath_aurocs({et: ei.cpu() for et, ei in edge_dict.items()}, counts, task_et, test_ei.cpu(),
                                     neg_ei.cpu(), candidates_idx if candidates_idx is not None
                                     else neg_ei[1].tolist(), seed=seed, max_len=metapaths))

    for epoch in range(epochs):
        train_epoch(model, data, optimizer, task_et, train_mask, device, edge_masks, neg_pool=neg_pool)
    m, pos, neg = evaluate(keep_scores=True)
    if strata:
        from linkpred.strata import stratified_aurocs
        for name, labels in strata.items():
            m.update(stratified_aurocs(pos, neg, labels, prefix=f"stratum_{name}"))
    m.update(extra)
    return m


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--graphs", default="G0,G3")
    p.add_argument("--tasks", default="A,B")
    p.add_argument("--splits", default="kfold,cold_disease,cold_target")
    p.add_argument("--seeds", type=int, default=5)
    p.add_argument("--device", default="cpu")
    p.add_argument("--epochs", type=int, default=200)
    p.add_argument("--hidden", type=int, default=128)
    p.add_argument("--sample-fraction", type=float, default=0,
                   help="smoke test: keep this share of EVERY edge type (e.g. 0.01), so the types keep their relative "
                        "sizes (a fixed --max-edges makes small enrichment layers far heavier than in the full graph); "
                        "combine with --max-edges to also bound the largest types")
    p.add_argument("--max-edges", type=int, default=0,
                   help="smoke test: keep at most this many edges per edge type, chosen by hash of the edge id "
                        "(0 = whole graph); test pairs still come from G0, so capped graphs compare with each other")
    p.add_argument("--reverse-edges", action="store_true",
                   help="add a reverse relation for every edge type so messages travel both ways (today a gene "
                        "receives no message at all in G0: no Pharos edge ends on a gene)")
    p.add_argument("--learn-embeddings", action="store_true",
                   help="a trainable embedding per node added to the frozen random code")
    p.add_argument("--num-bases", type=int, default=0, help="RGCNConv basis decomposition (0 = off)")
    p.add_argument("--neg-pool", default="all", choices=["all", "training", "reference"],
                   help="where training negatives come from: every node of the object type (all), objects with a "
                        "training edge (training), or the test candidates (reference)")
    p.add_argument("--positives", default="all", choices=["all", "curated", "assertions"],
                   help="curated: text-mining edges of the task type become a separate context relation, so the "
                        "model trains on the kind of pair it is tested on; assertions: only knowledge_assertion "
                        "edges stay positives (statistical associations become context too)")
    p.add_argument("--min-type-edges", type=int, default=0,
                   help="drop edge types with fewer edges than this after the cap (0 = keep all); the task type "
                        "is always kept")
    p.add_argument("--baselines", action="store_true",
                   help="also report, per fold, the untrained model, the degree (popularity) baselines and the "
                        "common-neighbour / Adamic-Adar / 3-hop path heuristics on the same pairs")
    p.add_argument("--metapaths", action="store_true",
                   help="also fit the meta-path-only model (path counts per named shape, logistic regression) and "
                        "report its AUROC, the share of test pairs with a path, and the best shapes")
    p.add_argument("--metapath-len", type=int, default=3, help="longest meta-path shape in steps (default 3)")
    p.add_argument("--neg-match", default="none", choices=["none", "degree"],
                   help="degree: each test negative comes from the true disease's degree bin (1-5, 6-20, 21-100, "
                        ">100 reference pairs), so popularity cannot score; every AUROC drops, what is left is biology")
    p.add_argument("--strata", action="store_true",
                   help="also report AUROC per disease degree bin and per gene degree bin")
    p.add_argument("--keep-layers", default="",
                   help="wave D1: comma list of layers whose edges stay (e.g. pharos,chembl37); the rest are removed")
    p.add_argument("--drop-layers", default="", help="wave D2: comma list of layers whose edges are removed")
    p.add_argument("--shuffle-layers", default="",
                   help="wave D3: comma list of layers whose edges get random objects (degree-preserving control)")
    p.add_argument("--random-drop", type=float, default=0.0,
                   help="wave D4: remove this share of the Pharos-layer edges at random (control for pruning)")
    p.add_argument("--keep-whole", default=",".join(KEEP_WHOLE),
                   help="comma list of predicates never cut by --sample-fraction / --max-edges (default "
                        "has_gene_product: the gene -> protein bridge; at 1%% only 202 of ~20,000 survived on "
                        "2026-11-02, which cut every protein-side layer off from the genes). '' = sample everything")
    p.add_argument("--test-levels", default="",
                   help="comma list of knowledge levels whose Pharos pairs are the test items (default: every "
                        "non-text-mining level); 'knowledge_assertion' = the curated sources only (CTD, Monarch, "
                        "UniProt Disease, MedlinePlus, UniProtKB-KW, AmyCo, DrugCentral), ~39,500 pairs instead of "
                        "~250,000 dominated by Expression Atlas")
    p.add_argument("--drop-levels", default="",
                   help="comma list of knowledge levels whose edges are removed from the graph entirely before "
                        "training (e.g. text_co_occurrence: the 13.1M TIN-X edges), so a full-graph run fits a laptop")
    p.add_argument("--tag", default="",
                   help="name of this variant: results go to <graph>_<task>_<split>@<tag>.json (and a report row "
                        "'<split>@<tag>'), so ablation runs do not overwrite the plain run")
    args = p.parse_args()
    lst = lambda v: tuple(x.strip() for x in v.split(",") if x.strip())
    wiring = {"reverse_edges": args.reverse_edges, "learn_embeddings": args.learn_embeddings,
              "num_bases": args.num_bases or None, "neg_pool": args.neg_pool, "positives": args.positives,
              "baselines": args.baselines, "min_type_edges": args.min_type_edges, "metapaths": args.metapaths,
              "metapath_len": args.metapath_len, "neg_match": args.neg_match, "strata": args.strata,
              "keep_layers": lst(args.keep_layers), "drop_layers": lst(args.drop_layers),
              "shuffle_layers": lst(args.shuffle_layers), "random_drop": args.random_drop,
              "keep_whole": lst(args.keep_whole), "test_levels": lst(args.test_levels),
              "drop_levels": lst(args.drop_levels)}

    from linkpred.evaluate import summarise_run, report_markdown

    out_dir = ROOT / "outputs" / "linkpred"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    for graph in args.graphs.split(","):
        results[graph] = {}
        for task in args.tasks.split(","):
            results[graph][task] = {}
            for split in args.splits.split(","):
                print(f"Running {graph} / task {task} / {split} ({args.seeds} seeds)...")
                metrics = run_experiment(graph, task, split, n_seeds=args.seeds, device=args.device,
                                         epochs=args.epochs, hidden=args.hidden, max_edges=args.max_edges,
                                         sample_fraction=args.sample_fraction or None, **wiring)
                summary = summarise_run(metrics)
                results[graph][task][split] = summary
                detail_path = out_dir / result_name(graph, task, split, args.tag)
                settings = {"epochs": args.epochs, "hidden": args.hidden, "seeds": args.seeds,
                            "max_edges_per_type": args.max_edges or None, "sample_fraction": args.sample_fraction or None,
                            "wiring": wiring, "tag": args.tag or None,
                            "test_set": {"from": metrics[0].get("test_from"), "digest": metrics[0].get("test_set_digest"),
                                         "layer": L0_LAYER, "excluded_knowledge_levels": list(EXCLUDED_LEVELS),
                                         "included_knowledge_levels": list(wiring["test_levels"]) or "all other"}}
                detail_path.write_text(json.dumps({"settings": settings, "per_seed": metrics, "summary": summary},
                                                  indent=2))
                print(f"  -> {detail_path.name}: AUROC {summary.get('auroc', {}).get('mean', '?')}")

    report = report_markdown(collect_results(out_dir))    # every result file on disk, not only this command's graphs
    report_path = out_dir / "report.md"
    report_path.write_text(report)
    print(f"\nReport written to {report_path} ({len(collect_results(out_dir))} graphs)")


if __name__ == "__main__":
    main()
