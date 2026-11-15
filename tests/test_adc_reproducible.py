"""ADC demo: rebuilds offline from data/snapshots_adc_demo/ and must match the committed outputs/adc/."""
import json
import run_adc_demo as rad
import seed_kg as sk
from snapshot import Snapshot
from kg_schema import validate


def test_adc_offline_rebuild_matches_committed(tmp_path, monkeypatch):
    monkeypatch.setattr(sk, "SNAP", Snapshot(sk.ROOT / "data" / "snapshots_adc_demo", mode="offline"))
    monkeypatch.setattr(rad, "OUT", tmp_path)
    manifest = rad.main(tmp_path)
    committed = json.loads((sk.OUT / "adc" / "run_manifest.json").read_text())
    assert manifest["outputs_sha256"] == committed["outputs_sha256"]
    assert (tmp_path / "demo_query_TUB040.md").read_text() == (sk.OUT / "demo_query_TUB040.md").read_text()


def test_adc_answer_and_graph_stay_labelled_synthetic():
    text = (sk.OUT / "demo_query_TUB040.md").read_text()
    assert "SYNTHETIC" in text
    nodes = (sk.OUT / "adc" / "kg_nodes.csv").read_text()
    assert "INT:SYN-TUB040-PH1B" in nodes and "(SYNTHETIC)" in nodes
    import pandas as pd
    e = pd.read_csv(sk.OUT / "adc" / "kg_edges.csv")
    assert set(e[e.layer == "internal"].evidence_type) == {"synthetic"}   # rule 4: never typed as real data
