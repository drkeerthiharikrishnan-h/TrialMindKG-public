"""The core promise: the committed snapshot + code rebuild the committed outputs byte for byte."""
import json
import pytest
import seed_kg as sk
from snapshot import Snapshot
from kg_schema import validate
from export import export_all


def test_offline_rebuild_matches_committed_outputs(tmp_path):
    snap = Snapshot(mode="offline")                     # no network: everything must come from the snapshot
    kg, report, _, versions = sk.build(sk.DATASET, snap)
    assert validate(kg) == []
    manifest = export_all(kg, report, versions, snap, tmp_path)
    committed = json.loads((sk.OUT / "run_manifest.json").read_text())
    assert manifest["outputs_sha256"] == committed["outputs_sha256"]
    assert manifest["snapshot"]["fingerprint"] == committed["snapshot"]["fingerprint"]


def test_graphml_writer_does_not_depend_on_lxml(tmp_path, monkeypatch):
    """2026-10-30: in a container with lxml installed, nx.write_graphml switched to the lxml writer and the GraphML
    hashed differently (same graph, different indentation). export must always use the pure-Python writer."""
    import networkx as nx
    import export

    def boom(*a, **k):
        raise AssertionError("export.write_graphml must not go through nx.write_graphml (lxml-dependent)")

    monkeypatch.setattr(nx, "write_graphml", boom)
    nodes = [{"id": "A", "name": "a"}, {"id": "B", "name": "b"}]
    edges = [{"id": "e1", "subject": "A", "object": "B", "predicate": "tmkg:x"}]
    export.write_graphml(nodes, edges, tmp_path)
    text = (tmp_path / "seed_kg.graphml").read_text()
    assert text.count("\n") > 5 and '<edge source="A" target="B"' in text     # pretty-printed, edge present
