"""--test-levels (curated-only test pairs) and --drop-levels (remove text-mining edges) in pipelines/linkpred/run.py.
Torch-free except the last test, which runs on the Mac (torch in .venv)."""
import sys
sys.path.insert(0, "pipelines")

import numpy as np
import pytest

from linkpred.run import digest, eligible_mask, drop_knowledge_levels
from linkpred.splits import pair_key


class _Store(dict):
    """Minimal stand-in for a HeteroData edge store: attribute access + dict keys (what subset_edges needs)."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)
    def __setattr__(self, k, v):
        self[k] = v


def _graph(levels, layers=None):
    torch = pytest.importorskip("torch")
    n = len(levels)
    store = _Store(edge_index=torch.tensor([list(range(n)), [0] * n]), edge_id=[f"e{i}" for i in range(n)],
                   edge_knowledge_level=list(levels))
    if layers:
        store["edge_layer"] = list(layers)

    class _Data:
        edge_types = [("Gene", "gene_associated_with_condition", "Disease")]
        def __getitem__(self, et):
            return store
        def __delitem__(self, et):
            self.edge_types.remove(et)
    return _Data(), store


def test_eligible_mask_restricts_to_test_levels_when_given():
    data, _ = _graph(["knowledge_assertion", "statistical_association", "not_provided", "text_co_occurrence"])
    et = data.edge_types[0]
    assert eligible_mask(data, et).tolist() == [True, True, True, False]              # default: all but text mining
    assert eligible_mask(data, et, test_levels=("knowledge_assertion",)).tolist() == [True, False, False, False]
    # the layer rule still applies
    data, _ = _graph(["knowledge_assertion"] * 2, layers=["pharos", "opentargets"])
    assert eligible_mask(data, data.edge_types[0], test_levels=("knowledge_assertion",)).tolist() == [True, False]


def test_digest_changes_with_test_levels():
    keys = [pair_key("NCBIGene:1", "MONDO:0000001")]
    assert digest(keys) != digest(keys, test_levels=("knowledge_assertion",))
    assert digest(keys, test_levels=("a", "b")) == digest(keys, test_levels=("b", "a"))   # order-free


def test_drop_knowledge_levels_removes_edges_and_keeps_attributes_aligned():
    data, store = _graph(["knowledge_assertion", "text_co_occurrence", "statistical_association", "text_co_occurrence"])
    gone = drop_knowledge_levels(data, ("text_co_occurrence",))
    assert gone == {"Gene|gene_associated_with_condition|Disease": 2}
    assert store.edge_index[0].tolist() == [0, 2] and store.edge_id == ["e0", "e2"]
    assert store.edge_knowledge_level == ["knowledge_assertion", "statistical_association"]
    assert drop_knowledge_levels(data, ()) == {}                                          # no levels: no change
    # an edge type left empty is deleted
    data, store = _graph(["text_co_occurrence"])
    drop_knowledge_levels(data, ("text_co_occurrence",))
    assert data.edge_types == []
