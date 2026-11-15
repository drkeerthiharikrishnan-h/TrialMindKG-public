"""The gold-set verdict file (pipelines/verdict_file.py): one row per source row with its edge and verdict."""
import gzip
import sys
sys.path.insert(0, "pipelines")

import pandas as pd

from kgbuilder import engine
from kgbuilder.biolink import BioLink
import verdict_file as vf

BL = BioLink()


def write(d, name, df):
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{name}.tsv.gz", "wt") as f:
        df.to_csv(f, sep="\t", index=False)


CARD = {
    "name": "toy", "version": "v1", "aggregator": "infores:pharos", "layer": "L0",
    "lookups": {"gene": {"table": "genes", "key": "UniProt", "value": "GeneID", "prefix": "NCBIGene"},
                "alt": {"table": "altmap", "key": "Alt", "value": "Mondo"}},
    "nodes": [{"table": "genes", "id": {"column": "GeneID", "prefix": "NCBIGene"}, "category": "biolink:Gene",
               "name": "Symbol"}],
    "edges": [
        {"name": "gd", "table": "assoc", "subject": {"column": "UniProt", "lookup": "gene", "category": "biolink:Gene"},
         "predicate": "biolink:gene_associated_with_condition",
         "object": {"columns": ["Mondo", {"column": "Alt", "lookup": "alt"}], "category": "biolink:Disease",
                    "name": "Label"},
         "record_columns": ["UniProt", "Mondo", "Alt"],
         "source": {"column": "Src"}, "primary_knowledge_source": "infores:ctd",
         "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent", "explode_json": ["Mondo"]},
        {"name": "bp", "table": "go", "filter": {"Type": "Process"},
         "subject": {"column": "UniProt", "prefix": "UniProtKB", "category": "biolink:Protein"},
         "predicate": "biolink:actively_involved_in",
         "object": {"column": "GO", "category": "biolink:BiologicalProcess"},
         "source": "GO", "primary_knowledge_source": "infores:goa",
         "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent"}]}


def _setup(tmp_path):
    tables, kg = tmp_path / "tables", tmp_path / "kg"
    write(tables, "genes", pd.DataFrame({"UniProt": ["P1", "P2"], "GeneID": ["1", "2"], "Symbol": ["A", "B"]}))
    write(tables, "altmap", pd.DataFrame({"Alt": ["UMLS:C9"], "Mondo": ["MONDO:0000005"]}))
    write(tables, "assoc", pd.DataFrame({
        # row 0 and row 1: same gene, same source, different native ids -> ONE merged edge to MONDO:0000005
        "UniProt": ["P1", "P1", "P2", "P9", "P2"],
        "Mondo": ["MONDO:0000005", None, '["MONDO:0000002","MONDO:0000003"]', "MONDO:0000005", None],
        "Alt": [None, "UMLS:C9", None, None, "UMLS:C404"],
        "Label": ["d5", "d5", None, "d5", None], "Src": ["CTD", "CTD", "CTD", "CTD", "CTD"]}))
    write(tables, "go", pd.DataFrame({"UniProt": ["P1", "P2", "P1"], "GO": ["GO:1", "GO:2", "GO:3"],
                                      "Type": ["Process", "Process", "Component"]}))
    b = engine.build(CARD, tables_dir=tables, bl=BL)
    assert b.problems == []
    engine.export(b, kg)
    return tables, kg, b


def _verdicts(kg, ids, drop=()):
    write(kg, "edge_verdicts", pd.DataFrame({"id": [i for i in ids if i not in drop],
                                             "verdict": "keep", "confidence": "0.9", "reasons": "r"}))


def _rows(path):
    return pd.read_csv(path, sep="\t", dtype=str)


def test_every_source_row_listed_with_its_own_native_id_and_the_merged_edge(tmp_path):
    tables, kg, b = _setup(tmp_path)
    _verdicts(kg, b.edges["id"])
    path, text, problems = vf.write(CARD, tables_dir=tables, kg_dir=kg, out_dir=tmp_path / "out")
    assert problems == []
    r = _rows(path)
    gd = r[r["source_table"] == "assoc"]
    # rows 0 and 1 share the merged edge, each keeps its OWN raw disease id
    r0, r1 = gd[gd["source_row"] == "0"].iloc[0], gd[gd["source_row"] == "1"].iloc[0]
    assert r0["edge_id"] == r1["edge_id"] and r0["object"] == r1["object"] == "MONDO:0000005"
    assert r0["source_object"] == "MONDO:0000005" and r1["source_object"] == "UMLS:C9"
    assert r0["source_subject"] == "P1" and r0["verdict"] == "keep" and r0["source_name"] == "CTD"
    # the JSON list row gives one row per value, same source_row
    assert (gd["source_row"] == "2").sum() == 2
    # rows that could not become an edge are listed with the reason, no edge id, no verdict
    p9 = gd[gd["source_row"] == "3"].iloc[0]
    assert p9["status"] == "not_in_graph" and p9["status_reason"] == "no subject identifier" and pd.isna(p9["edge_id"])
    assert gd[gd["source_row"] == "4"].iloc[0]["status_reason"] == "no usable object identifier"
    # the go row removed by the card's filter is listed too
    go = r[r["source_table"] == "go"]
    assert go[go["source_row"] == "2"].iloc[0]["status_reason"] == "excluded by the card filter"
    assert len(go) == 3 and len(gd) == 6
    # every in-graph row's edge id is a real edge with the same subject / predicate / object
    e = b.edges.set_index("id")
    ing = r[r["status"] == "in_graph"]
    assert set(ing["edge_id"]) == set(b.edges["id"])
    for x in ing.itertuples():
        assert (e.loc[x.edge_id, "subject"], e.loc[x.edge_id, "object"]) == (x.subject, x.object)
    assert "| assoc | 6 | 4 | 4 |" in text and "every graph edge is reached" in text


def test_checks_flag_missing_verdicts_and_unreached_edges(tmp_path):
    tables, kg, b = _setup(tmp_path)
    first = b.edges["id"].iloc[0]
    _verdicts(kg, b.edges["id"], drop=(first,))
    path, text, problems = vf.write(CARD, tables_dir=tables, kg_dir=kg, out_dir=tmp_path / "out")
    assert any("have no verdict" in p for p in problems) and "PROBLEM" in text
    r = _rows(path)
    assert (r["status_reason"] == "no verdict for this edge id").sum() >= 1
    cov = vf.add_coverage(vf.new_coverage(), vf.attach_verdicts(
        vf.spec_rows(engine.read_table(tables, "go", {}), CARD["edges"][1], CARD, {}), {}))
    probs = vf.checks(cov, list(cov["edge_ids"]) + ["e_not_reached"])
    assert any("have no source row" in p for p in probs)
    assert any("not in edges.tsv.gz" in p for p in vf.checks(cov, []))


def test_file_is_deterministic(tmp_path):
    tables, kg, b = _setup(tmp_path)
    _verdicts(kg, b.edges["id"])
    p1, _, _ = vf.write(CARD, tables_dir=tables, kg_dir=kg, out_dir=tmp_path / "out")
    one = p1.read_bytes()
    p2, _, _ = vf.write(CARD, tables_dir=tables, kg_dir=kg, out_dir=tmp_path / "out")
    assert p2.read_bytes() == one
    p3, _, _ = vf.write(CARD, tables_dir=tables, kg_dir=kg, out_dir=tmp_path / "out", chunk=2)   # chunked = whole
    assert gzip.decompress(p3.read_bytes()) == gzip.decompress(one)


def test_side_columns_and_first_raw():
    assert vf.side_columns({"column": "A"}) == ["A"]
    assert vf.side_columns({"columns": ["A", {"column": "B", "lookup": "x"}]}) == ["A", "B"]
    df = pd.DataFrame({"A": [None, "a", ""], "B": ["b", "x", None]})
    assert vf.first_raw(df, ["A", "B"]).tolist()[:2] == ["b", "a"]


# --- gold-set id columns (CC-1) ---
HGNC = pd.DataFrame({"hgnc_id": ["HGNC:1", "HGNC:2"], "symbol": ["A", "B"], "ensembl_id": ["ENSG1", "ENSG2"],
                     "ncbigene_id": ["NCBIGene:1", "NCBIGene:2"], "uniprot_id": ["P04637", "P00533"],
                     "locus_group": ["protein-coding gene"] * 2})


def _hgnc(tmp_path):
    p = tmp_path / "hgnc.tsv"
    HGNC.to_csv(p, sep="\t", index=False)
    return p


def test_load_hgnc_maps_every_id_kind(tmp_path):
    h = vf.load_hgnc(_hgnc(tmp_path))
    assert h["uniprot"]["P04637"] == {"HGNC:1"} and h["ncbigene"]["NCBIGene:2"] == {"HGNC:2"}
    assert h["ensembl"]["ENSG1"] == {"HGNC:1"} and h["hgnc"]["HGNC:2"] == {"P00533"}


def test_add_gold_ids_for_each_subject_kind_and_object(tmp_path):
    h = vf.load_hgnc(_hgnc(tmp_path))
    rows = pd.DataFrame({
        "subject": ["NCBIGene:1", "UniProtKB:P00533", "ENSEMBL:ENSG2", "HGNC:1", "CHEMBL.COMPOUND:CHEMBL25", pd.NA,
                    "NCBIGene:99"],
        "source_subject": ["P04637", "P00533", "ENSG2", pd.NA, "CHEMBL25", "Q9Y6K9", "P12345"],
        "object": ["MONDO:0000005", "HP:0001250", "MONDO:0000002", pd.NA, "MONDO:0000003", pd.NA, pd.NA],
        "source_object": ["MONDO:0000005", "HP:0001250", pd.NA, pd.NA, pd.NA, "MONDO:0000007", "DOID:4"]})
    out = vf.add_gold_ids(rows, h)
    assert list(out.columns) == list(rows.columns) + vf.ID_COLUMNS
    # gene id -> HGNC + the source's own accession
    assert out.loc[0, "subject_uniprot"] == "P04637" and out.loc[0, "subject_hgnc"] == "HGNC:1"
    assert out.loc[1, "subject_uniprot"] == "P00533" and out.loc[1, "subject_hgnc"] == "HGNC:2"
    # Ensembl id (not an accession) -> accession and HGNC through the reference table
    assert out.loc[2, "subject_uniprot"] == "P00533" and out.loc[2, "subject_hgnc"] == "HGNC:2"
    assert out.loc[3, "subject_hgnc"] == "HGNC:1" and out.loc[3, "subject_uniprot"] == "P04637"
    # a compound is not a gene: nothing is guessed
    assert pd.isna(out.loc[4, "subject_uniprot"]) and pd.isna(out.loc[4, "subject_hgnc"])
    # a row with no subject id but a UniProt accession of its own keeps the accession; HGNC unknown for it
    assert out.loc[5, "subject_uniprot"] == "Q9Y6K9" and pd.isna(out.loc[5, "subject_hgnc"])
    # an id HGNC does not know: empty hgnc, the accession the source gave is kept
    assert pd.isna(out.loc[6, "subject_hgnc"]) and out.loc[6, "subject_uniprot"] == "P12345"
    # object: MONDO ids only; a row that is not in the graph keeps its own MONDO id
    assert out.loc[0, "object_mondo"] == "MONDO:0000005" and pd.isna(out.loc[1, "object_mondo"])
    assert out.loc[2, "object_mondo"] == "MONDO:0000002" and out.loc[4, "object_mondo"] == "MONDO:0000003"
    assert out.loc[5, "object_mondo"] == "MONDO:0000007" and pd.isna(out.loc[6, "object_mondo"])
    assert out["subject_nodenorm"].isna().all() and out["object_nodenorm"].isna().all()


def test_add_gold_ids_nodenorm_columns():
    rows = pd.DataFrame({"subject": ["NCBIGene:1", "NCBIGene:7"], "object": ["MONDO:0000005", pd.NA],
                         "source_subject": ["P04637", "P07"], "source_object": [pd.NA, pd.NA]})
    out = vf.add_gold_ids(rows, {"uniprot": {}, "ncbigene": {}, "ensembl": {}, "hgnc": {}},
                          {"NCBIGene:1": "NCBIGene:1", "MONDO:0000005": "MONDO:0000005"})
    assert out["subject_nodenorm"].tolist()[0] == "NCBIGene:1" and pd.isna(out["subject_nodenorm"].iloc[1])
    assert out["object_nodenorm"].iloc[0] == "MONDO:0000005" and pd.isna(out["object_nodenorm"].iloc[1])


def test_nodenorm_map_asks_babel_and_skips_licensed(monkeypatch):
    import babel
    asked = []

    def fake_normalize(snap, curies, batch=200):
        asked.extend(curies)
        return {c: ({"id": "NCBIGene:6647"} if c == "ENSEMBL:ENSG00000142168" else None) for c in curies}
    monkeypatch.setattr(babel, "normalize", fake_normalize)
    out = vf.nodenorm_map(object(), ["ENSEMBL:ENSG00000142168", "UMLS:C1", "MONDO:1", "MONDO:1", None])
    assert out == {"ENSEMBL:ENSG00000142168": "NCBIGene:6647"}
    assert sorted(asked) == ["ENSEMBL:ENSG00000142168", "MONDO:1"]      # UMLS is licensed: never sent


def test_written_file_has_the_id_columns_and_report_counts_match(tmp_path):
    tables, kg, b = _setup(tmp_path)
    _verdicts(kg, b.edges["id"])
    path, text, problems = vf.write(CARD, tables_dir=tables, kg_dir=kg, out_dir=tmp_path / "out",
                                    hgnc_path=_hgnc(tmp_path))
    assert problems == []
    r = _rows(path)
    assert list(r.columns) == vf.ALL_COLUMNS
    gd = r[(r["source_table"] == "assoc") & (r["status"] == "in_graph")]
    # the fixture's subject genes are NCBIGene:1 / :2, which the HGNC fixture maps to P04637 / P00533
    assert (gd["subject_uniprot"].isin(["P04637", "P00533"])).all() and gd["subject_hgnc"].notna().all()
    assert set(gd["object_mondo"]) == {"MONDO:0000005", "MONDO:0000002", "MONDO:0000003"}
    # a row that could not become an edge is still listed, with no invented gold id (P9 is not an accession)
    p9 = r[(r["source_table"] == "assoc") & (r["source_row"] == "3")].iloc[0]
    assert p9["status"] == "not_in_graph" and pd.isna(p9["subject_uniprot"]) and pd.isna(p9["subject_hgnc"])
    # same file with a Babel map: the two extra columns are filled for known ids only
    nn = {"NCBIGene:1": "NCBIGene:1"}
    p2, _, _ = vf.write(CARD, tables_dir=tables, kg_dir=kg, out_dir=tmp_path / "out", hgnc_path=_hgnc(tmp_path),
                        nodenorm=nn)
    r2 = _rows(p2)
    assert set(r2["subject_nodenorm"].dropna()) <= {"NCBIGene:1"} and r2["subject_nodenorm"].notna().any()
