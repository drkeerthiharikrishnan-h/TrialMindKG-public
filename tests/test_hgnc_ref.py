"""Tests for pipelines/hgnc_ref.py (HGNC gene id reference table), engine.ref_lookup and the trial-layer id
renaming in make_graphs.py; tiny fixtures, no network."""
import pandas as pd
import pytest

import hgnc_ref
import make_graphs as mg
from kgbuilder import engine


def hgnc_fixture():
    return pd.DataFrame({
        "hgnc_id": ["HGNC:5", "HGNC:7", "HGNC:9", "HGNC:11"],
        "symbol": ["A1BG", "TWO", "GONE", "NOENTREZ"],
        "status": ["Approved", "Approved", "Entry Withdrawn", "Approved"],
        "locus_group": ["protein-coding gene"] * 4,
        "entrez_id": ["1", "2", "9", None],
        "ensembl_gene_id": ["ENSG1", "ENSG2", "ENSG9", "ENSG11"],
        "uniprot_ids": ["P1", "P2|P1", None, "P11"]})


def test_gene_id_table_rows_and_prefixes(tmp_path):
    t = hgnc_ref.gene_id_table(hgnc_fixture())
    assert list(t.columns) == hgnc_ref.COLS
    assert "HGNC:9" not in set(t["hgnc_id"])                          # withdrawn entry dropped
    rows = t.set_index(["hgnc_id", "uniprot_id"])
    assert rows.loc[("HGNC:5", "P1"), "ncbigene_id"] == "NCBIGene:1"
    assert {"P1", "P2"} == set(t.loc[t["hgnc_id"] == "HGNC:7", "uniprot_id"])   # one row per accession
    assert rows.loc[("HGNC:11", "P11"), "ncbigene_id"] == ""           # no Entrez id -> empty, not 'NCBIGene:nan'
    path = tmp_path / "hgnc_gene_ids.tsv"
    t.to_csv(path, sep="\t", index=False)
    ens = hgnc_ref.load_lookup("ensembl_id", "ncbigene_id", path)
    assert ens == {"ENSG1": "NCBIGene:1", "ENSG2": "NCBIGene:2"}       # ENSG11 has no value: left out
    up = hgnc_ref.load_lookup("uniprot_id", "ncbigene_id", path)
    assert up == {"P2": "NCBIGene:2", "P11": ""} or up == {"P2": "NCBIGene:2"}   # P1 is on two genes: ambiguous
    assert "P1" not in up
    # the same rules through the engine's ref_file lookup
    lk = engine.ref_lookup(path, "uniprot_id", "ncbigene_id", skip_ambiguous=True)
    assert lk == {"P2": "NCBIGene:2"}
    lk_all = engine.ref_lookup(path, "uniprot_id", "ncbigene_id", skip_ambiguous=False)
    assert "P1" in lk_all and "P11" not in lk_all                      # empty value never mapped


def test_committed_reference_table_is_usable():
    lk = engine.ref_lookup(hgnc_ref.OUT, "ensembl_id", "ncbigene_id", skip_ambiguous=True)
    assert len(lk) > 40_000 and all(v.startswith("NCBIGene:") for v in list(lk.values())[:100])
    assert lk["ENSG00000121410"] == "NCBIGene:1"                        # A1BG
    up = engine.ref_lookup(hgnc_ref.OUT, "uniprot_id", "ncbigene_id", skip_ambiguous=True)
    assert up["P04217"] == "NCBIGene:1"


def test_trial_layer_ids_are_renamed_to_pharos_conventions(tmp_path):
    (tmp_path / "data" / "reference").mkdir(parents=True)
    hgnc_ref.gene_id_table(hgnc_fixture()).to_csv(tmp_path / "data" / "reference" / "hgnc_gene_ids.tsv",
                                                  sep="\t", index=False)
    kgx = tmp_path / "outputs" / "kgx"
    kgx.mkdir(parents=True)
    pd.DataFrame({"id": ["ENSEMBL:ENSG1", "ENSEMBL:ENSG11", "CHEMBL:CHEMBL25", "MONDO:1"],
                  "category": ["biolink:Gene", "biolink:Gene", "biolink:SmallMolecule", "biolink:Disease"]}
                 ).to_csv(kgx / "nodes.tsv", sep="\t", index=False)
    pd.DataFrame({"id": ["t1", "t2"], "subject": ["CHEMBL:CHEMBL25", "ENSEMBL:ENSG1"],
                  "predicate": ["biolink:treats", "biolink:gene_associated_with_condition"],
                  "object": ["MONDO:1", "MONDO:1"]}).to_csv(kgx / "edges.tsv", sep="\t", index=False)
    nodes, edges, verdicts = mg.read_layer(mg.TRIAL_LAYER, tmp_path)
    assert set(nodes["id"]) == {"NCBIGene:1", "ENSEMBL:ENSG11", "CHEMBL.COMPOUND:CHEMBL25", "MONDO:1"}
    assert list(edges["subject"]) == ["CHEMBL.COMPOUND:CHEMBL25", "NCBIGene:1"] and verdicts is None
    assert mg.trial_id_map(tmp_path / "nowhere") == {}                 # no reference file: nothing renamed
    n_nodes, n_edges, n = mg.rename_ids(nodes, edges, {})
    assert n == 0
