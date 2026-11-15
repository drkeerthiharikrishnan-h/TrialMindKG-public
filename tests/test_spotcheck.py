"""Tests for pipelines/make_spotcheck.py on small fixtures."""
import sys
sys.path.insert(0, "pipelines")

import pandas as pd
from make_spotcheck import DEFAULT_VERDICTS, load_verdicts, sample_for_review, score_review


def test_sample_stratified():
    edges = pd.DataFrame({
        "id": [f"e{i}" for i in range(50)],
        "subject": ["A"] * 50,
        "predicate": ["biolink:gene_associated_with_condition"] * 30 + ["biolink:physically_interacts_with"] * 20,
        "object": ["B"] * 50,
        "source": ["CTD"] * 50,
        "confidence": [0.1 * (i % 10) for i in range(50)],
        "verdict": ["prune"] * 20 + ["keep"] * 30,
        "reasons": ["low_score"] * 20 + ["high_score"] * 30,
    })
    sample = sample_for_review(edges, n_prune=10, n_keep=10, seed=0)
    assert len(sample) == 20
    assert (sample["verdict"] == "prune").sum() == 10
    assert (sample["verdict"] == "keep").sum() == 10
    assert "owner_agrees" in sample.columns
    # Both predicates should be represented
    assert sample["predicate"].nunique() == 2


def test_sample_fewer_than_requested():
    edges = pd.DataFrame({
        "id": ["e1", "e2", "e3"],
        "subject": ["A"] * 3, "predicate": ["p"] * 3, "object": ["B"] * 3,
        "source": ["S"] * 3, "confidence": [0.1, 0.5, 0.9],
        "verdict": ["prune", "prune", "keep"], "reasons": ["x"] * 3,
    })
    sample = sample_for_review(edges, n_prune=100, n_keep=100, seed=0)
    assert (sample["verdict"] == "prune").sum() == 2
    assert (sample["verdict"] == "keep").sum() == 1


def test_score_review(tmp_path):
    df = pd.DataFrame({
        "id": ["e1", "e2", "e3", "e4"],
        "subject": ["A"] * 4, "predicate": ["p"] * 4, "object": ["B"] * 4,
        "source": ["S"] * 4, "confidence": [0.1, 0.2, 0.8, 0.9],
        "verdict": ["prune", "prune", "keep", "keep"],
        "reasons": ["x"] * 4,
        "owner_agrees": ["Y", "N", "Y", ""],
        "owner_notes": ["", "this is actually correct", "", ""],
    })
    path = tmp_path / "test.xlsx"
    df.to_excel(path, index=False, sheet_name="spot_check")
    result = score_review(path)
    assert result["n_reviewed"] == 3
    assert result["prune_precision"] == 0.5    # 1/2 prune reviewed
    assert result["keep_precision"] == 1.0     # 1/1 keep reviewed
    assert len(result["disagreements"]) == 1


def test_load_verdicts_reads_prune_kg_file(tmp_path):
    import gzip
    path = tmp_path / "edge_verdicts.tsv.gz"
    with gzip.open(path, "wt") as f:
        f.write("id\tsubject\tpredicate\tobject\tsource\tconfidence\tverdict\treasons\n")
        f.write("e1\tA\tp\tB\tS\t0.25\tkeep\t\n")
        f.write("e2\tA\tp\tC\tS\t\tprune\tlow_score\n")
    edges = load_verdicts(path)
    assert list(edges["id"]) == ["e1", "e2"]
    assert edges["confidence"].iloc[0] == 0.25
    assert pd.isna(edges["confidence"].iloc[1])
    assert edges["reasons"].iloc[0] == ""          # empty cells stay empty text, not NaN


def test_load_verdicts_default_is_the_pruner_output():
    # prune_kg.py writes data/kg/<layer>/edge_verdicts.tsv.gz; the default must point there (not outputs/).
    assert DEFAULT_VERDICTS.parts[-4:] == ("data", "kg", "pharos", "edge_verdicts.tsv.gz")


def test_load_verdicts_missing_file_names_the_fix(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError, match="prune_kg.py"):
        load_verdicts(tmp_path / "missing.tsv.gz")
