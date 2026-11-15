"""Tests for pipelines/timeline.py on small fixtures; no network."""
import json
import sys
sys.path.insert(0, "pipelines")

import pandas as pd
import pytest
from timeline import event, summarise, build_timeline, pharos_dates, ot_score_curve


def test_event_validation():
    e = event("2024-02", "Pharos", "first_seen", "supports")
    assert e["date"] == "2024-02" and e["direction"] == "supports"
    with pytest.raises(AssertionError):
        event("2024", "x", "made_up", "supports")
    with pytest.raises(AssertionError):
        event("2024", "x", "first_seen", "bad_direction")


def test_summarise_empty():
    s = summarise([])
    assert s["first_seen"] is None and s["trend"] == "unknown"


def test_summarise_supporting_only():
    evts = [event("2018", "A", "first_seen", "supports"),
            event("2020", "B", "confirmed", "supports"),
            event("2024", "C", "new_support", "supports")]
    s = summarise(evts)
    assert s["first_seen"] == "2018"
    assert s["last_confirmed"] == "2024"
    assert s["n_supporting"] == 3 and s["n_contradicting"] == 0
    assert s["trend"] == "strengthening"


def test_summarise_contested():
    evts = [event("2018", "A", "first_seen", "supports"),
            event("2020", "B", "confirmed", "supports"),
            event("2023", "C", "trial_failed", "contradicts")]
    s = summarise(evts)
    assert s["trend"] == "contested"
    assert s["n_contradicting"] == 1


def test_summarise_weakening():
    evts = [event("2018", "A", "first_seen", "supports"),
            event("2020", "B", "contradicted", "contradicts"),
            event("2022", "C", "removed", "contradicts"),
            event("2024", "D", "trial_failed", "contradicts")]
    s = summarise(evts)
    assert s["trend"] == "weakening"


def test_pharos_dates():
    edges = pd.DataFrame({
        "id": ["e1", "e2"],
        "source": ["CTD", "DisGeNET"],
        "pmids": ["12345|67890", None],
        "predicate": ["biolink:gene_associated_with_condition"] * 2,
        "subject": ["NCBIGene:1", "NCBIGene:2"],
        "object": ["MONDO:0000001", "MONDO:0000002"],
    })
    tl = pharos_dates(edges)
    assert "e1" in tl
    assert len(tl["e1"]) == 2   # pmids event + first_seen
    assert tl["e1"][0]["reference"].startswith("PMIDs:")


def test_ot_score_curve():
    edges = pd.DataFrame({
        "id": ["e1"],
        "predicate": ["biolink:gene_associated_with_condition"],
        "subject": ["ENSG00000001"],
        "object": ["MONDO:0000001"],
    })
    releases = {
        "21.04": pd.DataFrame({"target_id": ["ENSG00000001"], "disease_id": ["MONDO:0000001"], "score": [0.3]}),
        "26.09": pd.DataFrame({"target_id": ["ENSG00000001"], "disease_id": ["MONDO:0000001"], "score": [0.6]}),
    }
    tl = ot_score_curve(edges, releases)
    assert "e1" in tl
    assert len(tl["e1"]) == 2
    assert tl["e1"][0]["date"] == "2021-04"
    assert tl["e1"][1]["value"] == 0.6


def test_build_timeline():
    edges = pd.DataFrame({
        "id": ["e1", "e2"],
        "source": ["CTD", "DisGeNET"],
        "pmids": [None, None],
        "predicate": ["biolink:gene_associated_with_condition"] * 2,
        "subject": ["NCBIGene:1", "NCBIGene:2"],
        "object": ["MONDO:0000001", "MONDO:0000002"],
    })
    result = build_timeline(edges)
    assert "evidence_timeline" in result.columns
    assert "trend" in result.columns
    assert "first_seen" in result.columns
    # All edges should have at least a first_seen from pharos_dates
    assert result["first_seen"].notna().all()
    # Parse the JSON to check it's valid
    for tl in result["evidence_timeline"]:
        if tl:
            parsed = json.loads(tl)
            assert isinstance(parsed, list)
