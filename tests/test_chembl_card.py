"""Tests for pipelines/chembl_prepare.py and sources/chembl37.yaml on a tiny SQLite database shaped like ChEMBL;
no network, no real data. BioLink checks need data/external/biolink/biolink-model.yaml."""
import sqlite3
import sys

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import chembl_prepare as cp  # noqa: E402
from kgbuilder import engine  # noqa: E402
from kgbuilder.biolink import DEFAULT as BIOLINK_YAML, BioLink  # noqa: E402


def toy_chembl(path):
    """A ChEMBL-shaped SQLite file: 2 compounds, 1 human single-protein target + 1 rat target + 1 protein complex."""
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE version (name TEXT);  INSERT INTO version VALUES ('Bioassay Ontology 2.0'), ('ChEMBL_37');
    CREATE TABLE molecule_dictionary (molregno INT, chembl_id TEXT, max_phase REAL);
    INSERT INTO molecule_dictionary VALUES (1, 'CHEMBL25', 4), (2, 'CHEMBL100', NULL);
    CREATE TABLE target_dictionary (tid INT, target_type TEXT, organism TEXT, pref_name TEXT);
    INSERT INTO target_dictionary VALUES (10, 'SINGLE PROTEIN', 'Homo sapiens', 'PTGS2'),
                                         (11, 'SINGLE PROTEIN', 'Rattus norvegicus', 'Ptgs2'),
                                         (12, 'PROTEIN COMPLEX', 'Homo sapiens', 'complex');
    CREATE TABLE component_sequences (component_id INT, accession TEXT);
    INSERT INTO component_sequences VALUES (100, 'P35354'), (101, 'P00000'), (102, 'Q1'), (103, 'Q2');
    CREATE TABLE target_components (tid INT, component_id INT);
    INSERT INTO target_components VALUES (10, 100), (11, 101), (12, 102), (12, 103);
    CREATE TABLE assays (assay_id INT, tid INT);
    INSERT INTO assays VALUES (1000, 10), (1001, 11), (1002, 12);
    CREATE TABLE docs (doc_id INT, year INT);
    INSERT INTO docs VALUES (5, 1999), (6, 2021);
    CREATE TABLE activities (activity_id INT, assay_id INT, molregno INT, doc_id INT, pchembl_value REAL,
                             standard_type TEXT);
    INSERT INTO activities VALUES (1, 1000, 1, 6, 6.5, 'IC50'), (2, 1000, 1, 5, 7.2, 'Ki'),
                                  (3, 1000, 1, 6, NULL, 'IC50'), (4, 1001, 1, 6, 8.0, 'IC50'),
                                  (5, 1002, 2, 6, 5.0, 'IC50'), (6, 1000, 2, 6, 5.5, 'IC50');
    CREATE TABLE drug_mechanism (molregno INT, tid INT, mechanism_of_action TEXT, action_type TEXT);
    INSERT INTO drug_mechanism VALUES (1, 10, 'Cyclooxygenase inhibitor', 'INHIBITOR'), (1, 11, 'rat', 'INHIBITOR');
    CREATE TABLE drug_indication (molregno INT, efo_id TEXT, efo_term TEXT, mesh_id TEXT, mesh_heading TEXT,
                                  max_phase_for_ind REAL);
    INSERT INTO drug_indication VALUES (1, 'EFO:0000400', 'diabetes mellitus', 'D003920', 'Diabetes Mellitus', 4),
                                       (1, 'HP:0001250', 'Seizure', 'D012640', 'Seizures', 2),
                                       (2, 'Orphanet:1', 'rare', 'D1', 'Rare', 1);
    """)
    con.commit()
    con.close()


def test_convert_writes_sorted_tables(tmp_path):
    db = tmp_path / "chembl_37.db"
    toy_chembl(db)
    rep = cp.convert(db, tmp_path / "chembl37")
    assert rep["release"] == "ChEMBL_37" and rep["rows"] == {"activities": 2, "mechanisms": 1, "indications": 3}
    act = pd.read_csv(tmp_path / "chembl37" / "activities.tsv.gz", sep="\t")
    a = act.set_index("molecule_chembl_id").loc["CHEMBL25"]
    assert a["uniprot"] == "P35354" and a["pchembl_max"] == 7.2 and a["n_activities"] == 2 and a["first_year"] == 1999
    assert set(a["standard_types"].split(",")) == {"IC50", "Ki"}
    assert "P00000" not in set(act["uniprot"])                         # rat target dropped
    mech = pd.read_csv(tmp_path / "chembl37" / "mechanisms.tsv.gz", sep="\t")
    assert mech.iloc[0]["mechanism_of_action"] == "Cyclooxygenase inhibitor" and mech.iloc[0]["max_phase"] == 4
    assert "| activities | 2 |" in cp.report_markdown(rep)
    assert cp.find_db(tmp_path) is None
    assert a["standard_types"] == "IC50,Ki"                               # unique, sorted -> stable bytes


def test_chunked_and_one_pass_give_identical_files(tmp_path):
    """With a molregno index on activities the aggregation runs in chunks; the bytes must equal the one-pass result."""
    db = tmp_path / "chembl_37.db"
    toy_chembl(db)
    con = sqlite3.connect(db)
    assert cp.has_molregno_index(con) is False
    assert cp.molregno_ranges(con, 1) == [(1, 2)]
    con.execute("CREATE INDEX idx_act_molregno ON activities(molregno)")
    con.commit()
    assert cp.has_molregno_index(con) is True
    assert cp.molregno_ranges(con, 5) == [(1, 1), (2, 2)]
    con.close()
    logs = []
    rep1 = cp.convert(db, tmp_path / "one", chunks=1, log=logs.append)
    rep2 = cp.convert(db, tmp_path / "many", chunks=5, log=logs.append)
    assert rep1 == rep2
    for name in ("activities", "mechanisms", "indications"):
        assert (tmp_path / "one" / f"{name}.tsv.gz").read_bytes() == (tmp_path / "many" / f"{name}.tsv.gz").read_bytes()
    assert any("chunk" in m and "min left" in m for m in logs)
    assert cp.sorted_types("Ki,IC50,Ki") == "IC50,Ki" and cp.sorted_types(None) is None
    assert cp.report_from_files(db, tmp_path / "many") == rep2     # --report-only: no queries, same report


@pytest.mark.skipif(not BIOLINK_YAML.exists(), reason="biolink-model.yaml not downloaded")
def test_chembl37_card_builds_on_fixture(tmp_path):
    import make_graphs as mg
    bl = BioLink()
    card = engine.load_card("sources/chembl37.yaml")
    assert engine.check_card(card, bl) == [] and card["layer"] == "L1" and card["name"] in mg.L1_L2
    db = tmp_path / "chembl_37.db"
    toy_chembl(db)
    cp.convert(db, tmp_path / "chembl37")
    b = engine.build(card, tables_dir=tmp_path / "chembl37", bl=bl)
    assert b.problems == []
    by_pred = b.edges.groupby("predicate").size().to_dict()
    assert by_pred == {"biolink:directly_physically_interacts_with": 3, "biolink:treats_or_applied_or_studied_to_treat": 2}
    act = b.edges[b.edges["source"] == "ChEMBL 37 activity"].set_index("subject")
    assert act.loc["CHEMBL.COMPOUND:CHEMBL25", "object"] == "UniProtKB:P35354"
    assert float(act.loc["CHEMBL.COMPOUND:CHEMBL25", "pchembl_max"]) == 7.2
    assert int(float(act.loc["CHEMBL.COMPOUND:CHEMBL25", "first_year"])) == 1999
    ind = b.edges[b.edges["predicate"] == "biolink:treats_or_applied_or_studied_to_treat"]
    assert set(ind["object"]) == {"MONDO:0005015", "HP:0001250"}     # EFO:0000400 -> MONDO is the intended join; Orphanet: no class in the card -> unmapped
    cats = dict(zip(b.nodes["id"], b.nodes["category"]))
    assert cats["HP:0001250"] == "biolink:PhenotypicFeature" and cats["MONDO:0005015"] == "biolink:Disease"
    assert (b.edges["layer"] == "L1").all() and (b.edges["source_version"] == "37").all()
    assert b.unmapped.set_index("edge_spec").loc["chembl_indication", "rows"] == 1
