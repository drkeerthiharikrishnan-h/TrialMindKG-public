"""
R-GCN and HGT link prediction models for heterogeneous graphs.

Each model:
  - Takes a PyG HeteroData and produces node embeddings per type.
  - A DistMult-style decoder scores (subject_emb, relation, object_emb) triples.
  - Training: BPR or BCE on positive + type-matched negative edges.

Also: DistMult and RotatE as shallow-embedding baselines.
"""
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch_geometric.nn import RGCNConv, HGTConv, Linear
    _HAS_PYG = True
except ImportError:
    _HAS_PYG = False


def _require():
    if not _HAS_PYG:
        raise ImportError("torch + torch_geometric required")


def reverse_edge_types(edge_types):
    """Takes edge types (subject type, predicate, object type). Returns one reverse type per edge type:
    (object type, 'rev_' + predicate, subject type). With reverse edges a node also hears from the edges it
    STARTS (a gene from its diseases and proteins); without them an R-GCN node only hears from edges that end on
    it, and in Pharos no edge ends on a gene."""
    return [(o, "rev_" + p, s) for s, p, o in edge_types]


def with_reverse(edge_index_dict):
    """Takes {edge type: edge_index}. Returns a new dict with, for every type, its reverse type and the flipped
    edge_index (same edge order, so a mask over the forward edges applies to the reverse ones)."""
    out = dict(edge_index_dict)
    for et, ei in edge_index_dict.items():
        out[(et[2], "rev_" + et[1], et[0])] = ei.flip(0)
    return out


class RGCNEncoder(torch.nn.Module if _HAS_PYG else object):
    """2-layer R-GCN encoder that produces per-type node embeddings.

    reverse: add a reverse relation for every edge type (reverse_edge_types), so messages travel both ways.
    learn_nodes: {node type: [num_nodes, hidden] start tensor} -> a trainable embedding per node, started from
      that tensor (the same hashed random code in every graph, so graphs stay comparable) and ADDED to the input
      features; without it the input features are frozen random codes the model can never change.
    num_bases: RGCNConv basis decomposition (fewer parameters per relation; for graphs with many relation types)."""

    def __init__(self, metadata, hidden=128, out=64, dropout=0.2, reverse=False, learn_nodes=None, num_bases=None):
        _require()
        super().__init__()
        self.node_types = metadata[0]
        self.edge_types = list(metadata[1]) + (reverse_edge_types(metadata[1]) if reverse else [])
        self.reverse = reverse
        n_relations = len(self.edge_types)
        self.node_emb = nn.ParameterDict({nt: nn.Parameter(t.clone()) for nt, t in (learn_nodes or {}).items()})
        self.conv1 = RGCNConv(hidden, hidden, n_relations, num_bases=num_bases)
        self.conv2 = RGCNConv(hidden, out, n_relations, num_bases=num_bases)
        self.drop = dropout

    def forward(self, x_dict, edge_index_dict):
        if self.reverse and not any(et[1].startswith("rev_") for et in edge_index_dict):
            edge_index_dict = with_reverse(edge_index_dict)
        if len(self.node_emb):
            x_dict = {nt: (x + self.node_emb[nt] if nt in self.node_emb else x) for nt, x in x_dict.items()}
        edge_index_all, edge_type_all = [], []
        type_map = {et: i for i, et in enumerate(self.edge_types)}
        offset = {}
        cur = 0
        for nt in self.node_types:
            offset[nt] = cur
            cur += x_dict[nt].size(0)
        for et, ei in edge_index_dict.items():
            src_off = offset[et[0]]
            dst_off = offset[et[2]]
            shifted = torch.stack([ei[0] + src_off, ei[1] + dst_off])
            edge_index_all.append(shifted)
            edge_type_all.append(torch.full((ei.size(1),), type_map[et], dtype=torch.long, device=ei.device))
        edge_index = torch.cat(edge_index_all, dim=1) if edge_index_all else torch.zeros(2, 0, dtype=torch.long)
        edge_type = torch.cat(edge_type_all) if edge_type_all else torch.zeros(0, dtype=torch.long)
        x = torch.cat([x_dict[nt] for nt in self.node_types])
        x = F.dropout(F.relu(self.conv1(x, edge_index, edge_type)), p=self.drop, training=self.training)
        x = self.conv2(x, edge_index, edge_type)
        out = {}
        for nt in self.node_types:
            start = offset[nt]
            end = start + x_dict[nt].size(0)
            out[nt] = x[start:end]
        return out


class HGTEncoder(torch.nn.Module if _HAS_PYG else object):
    """2-layer HGT encoder."""

    def __init__(self, metadata, hidden=128, out=64, heads=4, dropout=0.2):
        _require()
        super().__init__()
        self.node_types = metadata[0]
        self.lin = nn.ModuleDict({nt: Linear(-1, hidden) for nt in self.node_types})
        self.conv1 = HGTConv(hidden, hidden, metadata, heads=heads)
        self.conv2 = HGTConv(hidden, out, metadata, heads=heads)
        self.drop = dropout

    def forward(self, x_dict, edge_index_dict):
        x = {nt: self.lin[nt](x_dict[nt]) for nt in self.node_types}
        x = {nt: F.dropout(F.relu(v), p=self.drop, training=self.training) for nt, v in
             self.conv1(x, edge_index_dict).items()}
        return self.conv2(x, edge_index_dict)


class DistMultDecoder(torch.nn.Module if _HAS_PYG else object):
    """DistMult scoring: score(s, r, o) = sum(s * r * o)."""

    def __init__(self, dim, n_relations):
        _require()
        super().__init__()
        self.rel = nn.Embedding(n_relations, dim)

    def forward(self, z_src, z_dst, rel_idx):
        return (z_src * self.rel(rel_idx) * z_dst).sum(dim=-1)


class LinkPredictor(torch.nn.Module if _HAS_PYG else object):
    """Full model: encoder + decoder."""

    def __init__(self, encoder, n_relations, dim):
        _require()
        super().__init__()
        self.encoder = encoder
        self.decoder = DistMultDecoder(dim, n_relations)

    def encode(self, x_dict, edge_index_dict):
        return self.encoder(x_dict, edge_index_dict)

    def decode(self, z_src, z_dst, rel_idx):
        return self.decoder(z_src, z_dst, rel_idx)


def negative_sample(edge_index, n_nodes, k=1, rng=None, pool=None):
    """Type-matched negative sampling: replace the destination with a random node of the same type.
    pool: optional LongTensor of destination indices to draw from (e.g. the diseases that have a training edge);
    without it every node of the type can be drawn, including ontology terms that have no edge of the task kind,
    which makes the training task easier than the test (whose negatives are Pharos diseases)."""
    _require()
    if rng is None:
        rng = torch.Generator()
    if pool is not None and len(pool):
        return pool[torch.randint(0, len(pool), (edge_index.size(1) * k,), generator=rng)]
    neg_dst = torch.randint(0, n_nodes, (edge_index.size(1) * k,), generator=rng)
    return neg_dst


def masked_edges(data, task_edge_type, train_mask, device="cpu", edge_masks=None):
    """Takes the graph, the task edge type, its training mask and optional bool masks for other edge types
    (edge_masks: {edge type: keep mask}, e.g. enrichment edges that restate a held-out pair, see run.py).
    Returns {edge type: edge_index on device} with the masked edges removed: the graph the model may see."""
    out = {}
    for et in data.edge_types:
        ei = data[et].edge_index
        if et == task_edge_type and train_mask is not None:
            ei = ei[:, train_mask]
        elif edge_masks and et in edge_masks:
            ei = ei[:, edge_masks[et]]
        out[et] = ei.to(device)
    return out


def train_epoch(model, data, optimizer, task_edge_type, train_mask, device="cpu", edge_masks=None, neg_pool=None):
    """One training epoch with BPR loss on positive + negative edges. edge_masks: see masked_edges;
    neg_pool: see negative_sample."""
    _require()
    model.train()
    optimizer.zero_grad()
    x_dict = {nt: data[nt].x.to(device) for nt in data.node_types if hasattr(data[nt], "x")}
    train_edge_dict = masked_edges(data, task_edge_type, train_mask, device, edge_masks)
    z = model.encode(x_dict, train_edge_dict)
    src_type, rel, dst_type = task_edge_type
    pos_edge = train_edge_dict[task_edge_type]
    pos_src = z[src_type][pos_edge[0]]
    pos_dst = z[dst_type][pos_edge[1]]
    rel_map = {et: i for i, et in enumerate(data.edge_types)}
    rel_idx = torch.full((pos_edge.size(1),), rel_map[task_edge_type], dtype=torch.long, device=device)
    pos_score = model.decode(pos_src, pos_dst, rel_idx)
    neg_dst_idx = negative_sample(pos_edge, data[dst_type].num_nodes, pool=neg_pool)
    neg_dst = z[dst_type][neg_dst_idx.to(device)]
    neg_score = model.decode(pos_src, neg_dst, rel_idx)
    loss = -F.logsigmoid(pos_score - neg_score).mean()
    loss.backward()
    optimizer.step()
    return float(loss.detach())
