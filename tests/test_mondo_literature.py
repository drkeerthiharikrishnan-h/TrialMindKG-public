"""Tests for pipelines/mondo_ref.py (committed reference tables) and pipelines/literature.py."""
import mondo_ref, literature


def test_mesh_to_mondo_exact():
    ref = mondo_ref.load()
    assert ref["mesh_to_mondo"]["MESH:D000544"] == ["MONDO:0004975"]     # Alzheimer disease
    assert ref["version"].endswith("mondo.owl")


def test_name_index_exact_only():
    ref = mondo_ref.load()
    assert ref["name_index"][mondo_ref.norm_name("systemic lupus erythematosus")] == ["MONDO:0007915"]
    assert mondo_ref.norm_name("Active Systemic Lupus Erythematosus") not in ref["name_index"]


def test_pmids_and_pmcids_from_text():
    t = "[a](https://pubmed.ncbi.nlm.nih.gov/31387606/) [b](https://pmc.ncbi.nlm.nih.gov/articles/PMC9486635/)"
    assert literature.pmids_from_text(t) == ["31387606"]
    assert literature.pmcids_from_text(t) == ["PMC9486635"]
