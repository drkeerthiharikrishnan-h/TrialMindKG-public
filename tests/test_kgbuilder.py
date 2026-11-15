"""Tests for the generic KG builder (pipelines/kgbuilder) on tiny fixture tables; no network."""
import gzip
import pandas as pd
import pytest
from kgbuilder import engine
from kgbuilder.biolink import BioLink

BL = BioLink()


def test_biolink_domain_range_and_names():
    assert BL.check_edge_type("biolink:Gene", "biolink:has_gene_product", "biolink:Protein") == []
    assert BL.check_edge_type("biolink:Gene", "biolink:translates_to", "biolink:Protein")  # needs a transcript
    assert BL.check_edge_type("biolink:Protein", "biolink:capable_of", "biolink:MolecularActivity") == []
    assert "unknown predicate biolink:made_up" in BL.check_edge_type("biolink:Gene", "biolink:made_up", "biolink:Gene")
    assert "UniProtKB" in BL.prefixes("biolink:Protein")


def test_resolve_ids_rules():
    df = pd.DataFrame({"mondo": [None, "MONDO:1", None, None], "src": ["MIM:2", "DOID:3", "UMLS:C4", None],
                       "acc": ["P1", "P2", "P3", "P4"], "kind": ["Reactome", "KEGG", "x", "KEGG"],
                       "pw": ["R-HSA-1", "path:hsa1", "z", "path:hsa2"]})
    ids = engine.resolve_ids(df, {"columns": ["mondo", "src"], "prefix_map": {"MIM": "OMIM", "UMLS": None}}, {})
    assert ids[0] == "OMIM:2" and ids[1] == "MONDO:1" and ids.isna()[2]
    assert engine.resolve_ids(df, {"column": "acc", "prefix": "UniProtKB"}, {})[0] == "UniProtKB:P1"
    pw = engine.resolve_ids(df, {"column": "pw", "replace": {"^path:": ""},
                                 "prefix_by": {"column": "kind", "map": {"Reactome": "REACT", "KEGG": "KEGG.PATHWAY"}}}, {})
    assert pw[0] == "REACT:R-HSA-1" and pw[1] == "KEGG.PATHWAY:hsa1" and pw.isna()[2]
    g = engine.resolve_ids(df, {"columns": [{"column": "acc", "lookup": "a"}, {"column": "acc", "lookup": "b"}]},
                           {"a": {"P1": "NCBIGene:1"}, "b": {"P2": "ENSEMBL:ENSG2"}})
    assert g[0] == "NCBIGene:1" and g[1] == "ENSEMBL:ENSG2" and g.isna()[2]


def write(tmp_path, name, df):
    with gzip.open(tmp_path / f"{name}.tsv.gz", "wt") as f:
        df.to_csv(f, sep="\t", index=False)


CARD = {
    "name": "toy", "version": "v1", "aggregator": "infores:pharos", "layer": "L0",
    "lookups": {"gene": {"table": "genes", "key": "UniProt", "value": "GeneID", "prefix": "NCBIGene"}},
    "nodes": [{"table": "genes", "id": {"column": "GeneID", "prefix": "NCBIGene"}, "category": "biolink:Gene",
               "name": "Symbol"}],
    "edges": [{"name": "gd", "table": "assoc", "subject": {"column": "UniProt", "lookup": "gene",
                                                           "category": "biolink:Gene"},
               "predicate": "biolink:gene_associated_with_condition",
               "object": {"column": "Disease", "category": "biolink:Disease", "name": "Label"},
               "source": {"column": "Src"},
               "primary_knowledge_source": {"column": "Src", "map": {"CTD": "infores:ctd", "*": "infores:pharos"}},
               "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
               "explode_json": ["Disease"],
               "properties": {"score": "Score", "note": "Note"}}]}


def test_build_merges_counts_and_reports_unmapped(tmp_path):
    write(tmp_path, "genes", pd.DataFrame({"UniProt": ["P1", "P2"], "GeneID": ["1", "2"], "Symbol": ["A", "B"]}))
    write(tmp_path, "assoc", pd.DataFrame({
        "UniProt": ["P1", "P1", "P2", "P9", "P2"],
        "Disease": ["MONDO:0000001", "MONDO:0000001", '["MONDO:0000002","MONDO:0000003"]', "MONDO:0000001", None],
        "Label": ["d1", "d1", None, "d1", None], "Src": ["CTD", "CTD", "Other", "CTD", "CTD"],
        "Score": ["0.5", "0.9", "1", "1", "1"], "Note": ["x", "y", None, None, None]}))
    b = engine.build(CARD, tables_dir=tmp_path, bl=BL)
    assert b.problems == []
    e = b.edges.set_index("object")
    assert len(b.edges) == 3                                        # two CTD rows merged; JSON list exploded
    assert e.loc["MONDO:0000001", "n_records"] == 2 and e.loc["MONDO:0000001", "score"] == 0.9
    assert e.loc["MONDO:0000001", "note"] == "x|y"
    assert e.loc["MONDO:0000002", "primary_knowledge_source"] == "infores:pharos"
    um = {r.reason: r.rows for r in b.unmapped.itertuples()}
    assert um == {"no subject identifier": 1, "no usable object identifier": 1}
    nodes = b.nodes.set_index("id")
    assert nodes.loc["NCBIGene:1", "name"] == "A" and nodes.loc["MONDO:0000001", "category"] == "biolink:Disease"
    again = engine.build(CARD, tables_dir=tmp_path, bl=BL)
    assert again.edges["id"].tolist() == b.edges["id"].tolist()       # stable ids
    n_path, e_path = engine.export(b, tmp_path / "out")
    assert gzip.decompress(e_path.read_bytes()).decode().splitlines()[0].startswith("id\tsubject\tpredicate\tobject")
    assert "toy graph" in engine.summary_markdown(b)


def test_card_check_catches_domain_violation():
    bad = {**CARD, "edges": [{**CARD["edges"][0], "predicate": "biolink:translates_to",
                              "object": {"column": "Disease", "category": "biolink:Protein"}}]}
    assert any("transcript" in p for p in engine.check_card(bad, BL))


def test_pharos_card_is_valid_biolink():
    card = engine.load_card(engine.ROOT / "sources" / "pharos.yaml")
    assert engine.check_card(card, BL) == []


def test_opentargets_card_is_valid_biolink():
    card = engine.load_card(engine.ROOT / "sources" / "opentargets_layer.yaml")
    assert engine.check_card(card, BL) == []


def test_ref_file_lookup_and_mondo_mapping(tmp_path):
    """ref_file lookups load external TSVs and map UMLS/DOID -> MONDO in resolve_ids."""
    # Write a tiny ref file
    ref = tmp_path / "umls_mondo.tsv"
    ref.write_text("# source: test\numls_id\tmondo_id\tambiguous\nUMLS:C001\tMONDO:0001\t\nUMLS:C002\tMONDO:0002\tY\n")
    card = {"lookups": {"umls_to_mondo": {"ref_file": str(ref), "key": "umls_id", "value": "mondo_id",
                                          "skip_ambiguous": True}}}
    # Build the lookup dict directly (mirrors what engine.build does for ref_file)
    ref_df = pd.read_csv(ref, sep="\t", comment="#", dtype=str, keep_default_na=False)
    ref_df = ref_df[ref_df["ambiguous"] == ""]
    lookups = {"umls_to_mondo": dict(zip(ref_df["umls_id"], ref_df["mondo_id"]))}
    assert lookups["umls_to_mondo"]["UMLS:C001"] == "MONDO:0001"
    assert "UMLS:C002" not in lookups["umls_to_mondo"]   # ambiguous -> skipped
    # Test resolve_ids with cascading columns (MONDO first, then UMLS via lookup)
    df = pd.DataFrame({"mondo": [None, "MONDO:9999", None], "src": ["UMLS:C001", "UMLS:C001", "UMLS:C999"]})
    ids = engine.resolve_ids(df, {"columns": ["mondo", {"column": "src", "lookup": "umls_to_mondo"}, "src"],
                                  "prefix_map": {"UMLS": None}}, lookups)
    assert ids[0] == "MONDO:0001"     # UMLS:C001 mapped via lookup
    assert ids[1] == "MONDO:9999"     # Mondo ID took priority
    assert ids.isna()[2]              # UMLS:C999 not in lookup, dropped by prefix_map


def test_table_exists_and_single_parquet(tmp_path):
    """_table_exists detects .tsv.gz, single .parquet, and parquet directories."""
    write(tmp_path, "a", pd.DataFrame({"x": [1]}))
    assert engine._table_exists(tmp_path, "a")
    assert not engine._table_exists(tmp_path, "b")
    # single .parquet file
    pd.DataFrame({"x": [1]}).to_parquet(tmp_path / "c.parquet")
    assert engine._table_exists(tmp_path, "c")
    t = engine.read_table(tmp_path, "c", {})
    assert len(t) == 1


def test_pyg_export(tmp_path):
    """PyG export: Build -> HeteroData -> save -> load, with split masks."""
    pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    from kgbuilder.pyg_export import to_heterodata, save_pyg, load_pyg, split_edges, summary

    write(tmp_path, "genes", pd.DataFrame({"UniProt": ["P1", "P2"], "GeneID": ["1", "2"], "Symbol": ["A", "B"]}))
    write(tmp_path, "assoc", pd.DataFrame({
        "UniProt": ["P1", "P1", "P2"],
        "Disease": ["MONDO:0000001", "MONDO:0000002", "MONDO:0000001"],
        "Label": ["d1", "d2", "d1"], "Src": ["CTD", "CTD", "Other"],
        "Score": ["0.5", "0.9", "1"], "Note": [None, None, None]}))
    b = engine.build(CARD, tables_dir=tmp_path, bl=BL)
    hd = to_heterodata(b)
    assert "Gene" in hd.node_types
    assert "Disease" in hd.node_types
    assert hd["Gene"].num_nodes == 2
    # Should have gene_associated_with_condition edges
    edge_types = [et[1] for et in hd.edge_types]
    assert "gene_associated_with_condition" in edge_types
    # Save and reload
    pt = tmp_path / "test.pt"
    save_pyg(hd, pt)
    hd2 = load_pyg(pt)
    assert hd2["Gene"].num_nodes == 2
    # Split
    split_edges(hd, ["gene_associated_with_condition"], fractions=(0.5, 0.25, 0.25), seed=0)
    for key in hd.edge_types:
        if key[1] == "gene_associated_with_condition":
            assert hasattr(hd[key], "train_mask")
            assert hd[key].train_mask.sum() + hd[key].val_mask.sum() + hd[key].test_mask.sum() == hd[key].edge_index.size(1)
    # Summary
    s = summary(hd)
    assert "Gene" in s and "Total" in s


def test_filter_rows_min_value_keeps_numbers_at_or_above_the_minimum():
    import pandas as pd
    from kgbuilder.engine import filter_rows
    df = pd.DataFrame({"targetId": list("abcde"), "associationScore": ["0.05", "0.1", "0.5", None, "x"]})
    out = filter_rows(df, {"min_value": {"associationScore": 0.1}})
    assert list(out["targetId"]) == ["b", "c"]          # 0.1 is kept (>=); missing and non-numeric values are dropped
    assert len(filter_rows(df, {})) == 5                # no min_value: unchanged


# --- CC-22: MeSH -> MONDO fallback (key_prefix lookups, route_property, route report) ---------------------------

def _mesh_ref(tmp_path):
    p = tmp_path / "mondo_mesh.tsv"
    p.write_text("# source: test\nmondo_id\tmesh_id\nMONDO:0000001\tMESH:D006255\nMONDO:0000002\tMESH:D000002\n"
                 "MONDO:0000003\tMESH:D000002\nMONDO:0000004\tMESH:D000004\n")
    return p


def test_ref_lookup_key_prefix_matches_bare_values(tmp_path):
    lk = engine.ref_lookup(_mesh_ref(tmp_path), "mesh_id", "mondo_id", skip_ambiguous=True, key_prefix="MESH")
    assert lk["D006255"] == "MONDO:0000001" and lk["MESH:D006255"] == "MONDO:0000001"
    assert "D000002" not in lk and "MESH:D000002" not in lk          # one MeSH -> two MONDO: skipped, not guessed
    assert "D006255" not in engine.ref_lookup(_mesh_ref(tmp_path), "mesh_id", "mondo_id", True)   # no key_prefix


def test_resolve_ids_route_order_efo_then_mesh_then_raw(tmp_path):
    lookups = {"efo_to_mondo": {"EFO:1": "MONDO:0000009", "EFO:2": "MONDO:0000008"},
               "mesh_to_mondo": engine.ref_lookup(_mesh_ref(tmp_path), "mesh_id", "mondo_id", True, "MESH")}
    df = pd.DataFrame({"efo_id": ["EFO:1", "EFO:7", "EFO:7", None, "HP:1", None],
                       "mesh_id": ["D006255", "D006255", "D000002", "D000004", None, None]})
    spec = {"columns": [{"column": "efo_id", "lookup": "efo_to_mondo"}, {"column": "mesh_id", "lookup": "mesh_to_mondo"},
                        {"column": "efo_id"}]}
    ids, route = engine.resolve_ids_route(df, spec, lookups)
    assert ids.tolist()[:5] == ["MONDO:0000009", "MONDO:0000001", "EFO:7", "MONDO:0000004", "HP:1"]
    assert route.tolist()[:5] == ["efo_to_mondo", "mesh_to_mondo", "as_given", "mesh_to_mondo", "as_given"]
    assert ids.isna()[5] and route.isna()[5]
    assert engine.resolve_ids(df, spec, lookups).equals(ids)             # resolve_ids unchanged = the ids part


def test_route_property_on_edges_and_in_report(tmp_path):
    write(tmp_path, "ind", pd.DataFrame({"drug": ["C1", "C1", "C2", "C3"], "efo_id": ["EFO:1", None, "EFO:7", None],
                                         "mesh_id": ["D006255", "D006255", None, "D000002"]}))
    card = {"name": "t", "aggregator": "infores:t", "version": "1", "tables_dir": str(tmp_path),
            "lookups": {"efo_to_mondo": {"ref_file": str(tmp_path / "efo.tsv"), "key": "efo_id", "value": "mondo_id"},
                        "mesh_to_mondo": {"ref_file": str(_mesh_ref(tmp_path)), "key": "mesh_id", "value": "mondo_id",
                                          "skip_ambiguous": True, "key_prefix": "MESH"}},
            "edges": [{"name": "ind", "table": "ind", "predicate": "biolink:treats_or_applied_or_studied_to_treat",
                       "subject": {"column": "drug", "prefix": "CHEMBL.COMPOUND", "category": "biolink:SmallMolecule"},
                       "object": {"columns": [{"column": "efo_id", "lookup": "efo_to_mondo"},
                                              {"column": "mesh_id", "lookup": "mesh_to_mondo"}, {"column": "efo_id"}],
                                  "category_by_prefix": {"EFO": "biolink:Disease", "MONDO": "biolink:Disease"},
                                  "route_property": "object_mapped_from"},
                       "record_columns": ["drug", "efo_id"]}]}
    (tmp_path / "efo.tsv").write_text("efo_id\tmondo_id\nEFO:1\tMONDO:0000001\n")
    b = engine.build(card, bl=BL)
    e = b.edges.set_index("subject")
    # C1: one row via EFO, one via MeSH, same MONDO -> one merged edge carrying both routes
    assert e.loc["CHEMBL.COMPOUND:C1", "object"] == "MONDO:0000001"
    assert e.loc["CHEMBL.COMPOUND:C1", "object_mapped_from"] == "efo_to_mondo|mesh_to_mondo"
    assert e.loc["CHEMBL.COMPOUND:C2", "object_mapped_from"] == "as_given"
    assert "CHEMBL.COMPOUND:C3" not in e.index                           # ambiguous MeSH, no EFO -> unmapped
    assert b.routes == [("ind", "as_given", 1, 1), ("ind", "efo_to_mondo", 1, 1), ("ind", "mesh_to_mondo", 1, 1)]
    md = engine.summary_markdown(b)
    assert "## How object identifiers were mapped" in md and "| ind | mesh_to_mondo | 1 | 1 |" in md


def test_chembl37_card_routes_mesh_to_mondo(tmp_path):
    """The real card on fixture tables: bare ChEMBL MeSH ids map through data/reference/mondo_mesh_exact.tsv."""
    card = engine.load_card(engine.ROOT / "sources" / "chembl37.yaml")
    cols = card["edges"][2]["object"]["columns"]
    assert [c.get("lookup") for c in cols] == ["efo_to_mondo", None, "mesh_to_mondo", None]
    assert cols[1]["only_prefix"] == ["MONDO", "HP"]
    assert card["lookups"]["mesh_to_mondo"]["key_prefix"] == "MESH"
    assert engine.check_card(card, BL) == []
    write(tmp_path, "indications", pd.DataFrame({
        "molecule_chembl_id": ["CHEMBL1", "CHEMBL2", "CHEMBL3", "CHEMBL4", "CHEMBL5"],
        "efo_id": ["EFO:0000400", None, "EFO:9999999", "HP:0001250", "EFO:9999998"],
        "efo_term": ["diabetes mellitus", None, "x", "Seizure", "y"], "mesh_id": ["D012640", "D003920", None, "D012640", "D003920"],
        "mesh_heading": ["Seizures", "Diabetes Mellitus", None, "Seizures", "Diabetes Mellitus"],
        "max_phase_for_ind": ["4", "2", "1", "2", "3"]}))
    for t in ("activities", "mechanisms"):
        write(tmp_path, t, pd.DataFrame(columns=["molecule_chembl_id", "uniprot", "pchembl_max", "n_activities",
                                                 "first_year", "standard_types", "mechanism_of_action", "action_type",
                                                 "target_name", "max_phase"]))
    b = engine.build(card, tables_dir=tmp_path, bl=BL)
    ind = b.edges[b.edges["source"] == "ChEMBL 37 drug indication"].set_index("subject")
    assert ind.loc["CHEMBL.COMPOUND:CHEMBL1", ["object", "object_mapped_from"]].tolist() == ["MONDO:0005015", "efo_to_mondo"]
    assert ind.loc["CHEMBL.COMPOUND:CHEMBL2", ["object", "object_mapped_from"]].tolist() == ["MONDO:0005015", "mesh_to_mondo"]
    assert ind.loc["CHEMBL.COMPOUND:CHEMBL3", ["object", "object_mapped_from"]].tolist() == ["EFO:9999999", "as_given"]
    assert ind.loc["CHEMBL.COMPOUND:CHEMBL4", ["object", "object_mapped_from"]].tolist() == ["HP:0001250", "as_given"]
    assert ind.loc["CHEMBL.COMPOUND:CHEMBL5", ["object", "object_mapped_from"]].tolist() == ["MONDO:0005015", "mesh_to_mondo"]


def test_only_prefix_candidate():
    df = pd.DataFrame({"a": ["HP:1", "EFO:2", None]})
    ids, route = engine.resolve_ids_route(df, {"columns": [{"column": "a", "only_prefix": ["HP"]}]}, {})
    assert ids[0] == "HP:1" and ids[1:].isna().all() and route[0] == "as_given"


# --- CC-28 part B: skip_keys on a lookup ---------------------------------------------------------------------------

def test_skip_keys_removes_both_forms_and_warns_on_unknown(tmp_path, capsys):
    lk = engine.ref_lookup(_mesh_ref(tmp_path), "mesh_id", "mondo_id", True, "MESH",
                           skip_keys=["MESH:D006255", "D000004", "MESH:D999999"])
    assert "D006255" not in lk and "MESH:D006255" not in lk and "D000004" not in lk and "MESH:D000004" not in lk
    assert {"D006255", "MESH:D006255", "D000004", "MESH:D000004"} <= lk.skipped
    assert "skip_keys entry MESH:D999999 is not a key" in capsys.readouterr().out      # a warning, not an error
    plain = engine.ref_lookup(_mesh_ref(tmp_path), "mesh_id", "mondo_id", True, "MESH")
    assert plain["D006255"] == "MONDO:0000001" and not plain.skipped


def test_skipped_key_falls_through_and_the_report_counts_it(tmp_path):
    write(tmp_path, "ind", pd.DataFrame({"drug": ["C1", "C2", "C3"], "efo_id": ["EFO:5", "EFO:6", None],
                                         "mesh_id": ["D006255", "D000004", "D006255"]}))
    card = {"name": "t", "aggregator": "infores:t", "version": "1", "tables_dir": str(tmp_path),
            "lookups": {"mesh_to_mondo": {"ref_file": str(_mesh_ref(tmp_path)), "key": "mesh_id", "value": "mondo_id",
                                          "skip_ambiguous": True, "key_prefix": "MESH", "skip_keys": ["MESH:D006255"]}},
            "edges": [{"name": "ind", "table": "ind", "predicate": "biolink:treats_or_applied_or_studied_to_treat",
                       "subject": {"column": "drug", "prefix": "CHEMBL.COMPOUND", "category": "biolink:SmallMolecule"},
                       "object": {"columns": [{"column": "mesh_id", "lookup": "mesh_to_mondo"}, {"column": "efo_id"}],
                                  "category_by_prefix": {"EFO": "biolink:Disease", "MONDO": "biolink:Disease"},
                                  "route_property": "object_mapped_from"},
                       "record_columns": ["drug", "efo_id"]}]}
    b = engine.build(card, bl=BL)
    e = b.edges.set_index("subject")
    assert e.loc["CHEMBL.COMPOUND:C1", ["object", "object_mapped_from"]].tolist() == ["EFO:5", "as_given"]   # fell through
    assert e.loc["CHEMBL.COMPOUND:C2", ["object", "object_mapped_from"]].tolist() == ["MONDO:0000004", "mesh_to_mondo"]
    assert "CHEMBL.COMPOUND:C3" not in e.index                     # skipped key and no EFO id: no identifier, never guessed
    assert ("ind", "fell through: key in skip_keys", 1, 1) in b.routes      # C1: an edge on the next column
    um = b.unmapped.set_index("reason")["rows"]
    assert um["no usable object identifier"] == 1                          # C3: listed as unmapped, not as an edge
    assert "| ind | fell through: key in skip_keys | 1 | 1 |" in engine.summary_markdown(b)


def test_chembl37_card_skips_the_five_reviewed_mesh_keys(tmp_path):
    card = engine.load_card(engine.ROOT / "sources" / "chembl37.yaml")
    sk = card["lookups"]["mesh_to_mondo"]["skip_keys"]
    assert sk == ["MESH:D012772", "MESH:D020817", "MESH:D011213", "MESH:D000258", "MESH:D004416"]
    write(tmp_path, "indications", pd.DataFrame({
        "molecule_chembl_id": ["CHEMBL1"], "efo_id": ["EFO:9999997"], "efo_term": ["septic shock"],
        "mesh_id": ["D012772"], "mesh_heading": ["Shock, Septic"], "max_phase_for_ind": ["3"]}))
    for t in ("activities", "mechanisms"):
        write(tmp_path, t, pd.DataFrame(columns=["molecule_chembl_id", "uniprot", "pchembl_max", "n_activities",
                                                 "first_year", "standard_types", "mechanism_of_action", "action_type",
                                                 "target_name", "max_phase"]))
    b = engine.build(card, tables_dir=tmp_path, bl=BL)
    ind = b.edges[b.edges["source"] == "ChEMBL 37 drug indication"].iloc[0]
    # fixture EFO id without a MONDO match: before CC-28 the MeSH route gave MONDO:0001881 (toxic shock syndrome)
    assert (ind["object"], ind["object_mapped_from"]) == ("EFO:9999997", "as_given")
    assert ("chembl_indication", "fell through: key in skip_keys", 1, 1) in b.routes


# ---------------------------------------------------------------- CC-34 (review m4): standards on the Pharos card
def _pharos_card():
    import yaml
    from pathlib import Path
    return yaml.safe_load((Path(__file__).resolve().parents[1] / "sources" / "pharos.yaml").read_text())


def test_a_node_property_can_be_mapped_through_a_lookup():
    df = pd.DataFrame({"GeneID": ["41", "999999999"], "Symbol": ["ASIC1", "X"]})
    spec = {"id": {"column": "GeneID", "prefix": "NCBIGene"}, "category": "biolink:Gene", "name": "Symbol",
            "properties": {"hgnc": {"column": "GeneID", "lookup": "g2h"}, "symbol": "Symbol"}}
    n = engine.build_nodes(df, spec, {"g2h": {"41": "HGNC:100"}}).set_index("id")
    assert n.loc["NCBIGene:41", "hgnc"] == "HGNC:100" and pd.isna(n.loc["NCBIGene:999999999", "hgnc"])
    assert n.loc["NCBIGene:41", "symbol"] == "ASIC1"


def test_pharos_card_types_proteins_with_pr_and_genes_carry_hgnc():
    card = _pharos_card()
    nodes = {n["table"]: n for n in card["nodes"]}
    assert nodes["targets"]["constants"]["type"] == "PR:000000001" and nodes["targets"]["category"] == "biolink:Protein"
    assert nodes["gene_ids"]["properties"]["hgnc"] == {"column": "NCBI Gene ID", "lookup": "ncbigene_to_hgnc"}
    lk = card["lookups"]["ncbigene_to_hgnc"]
    look = engine.ref_lookup(engine.ROOT / lk["ref_file"], lk["key"], lk["value"], lk["skip_ambiguous"], lk["key_prefix"])
    assert look["41"] == "HGNC:100" == look["NCBIGene:41"]          # the first row of data/reference/hgnc_gene_ids.tsv


def test_pharos_tiga_traits_map_efo_to_mondo_and_keep_the_rest():
    card = _pharos_card()
    spec = next(e for e in card["edges"] if e["name"] == "gene_gwas_trait")["object"]
    lk = card["lookups"]["efo_to_mondo"]
    lookups = {"efo_to_mondo": engine.ref_lookup(engine.ROOT / lk["ref_file"], lk["key"], lk["value"], lk["skip_ambiguous"])}
    df = pd.DataFrame({"EFO ID": ["EFO_0000094", "EFO_9999999", "Orphanet_558"]})
    ids, route = engine.resolve_ids_route(df, spec, lookups)
    assert list(ids) == ["MONDO:0004947", "EFO:9999999", "ORDO:558"]        # mondo_efo_exact.tsv: EFO:0000094 -> MONDO:0004947
    assert list(route) == ["efo_to_mondo", "as_given", "as_given"] and spec["route_property"] == "disease_route"


def test_dto_joins_the_enriched_graphs_and_g0_stays_pharos_alone():
    import make_graphs
    assert all(make_graphs.RECIPES[g]["layers"][:2] == ["pharos", "dto"] for g in ("G2", "G3", "G4"))
    assert make_graphs.RECIPES["G0"]["layers"] == make_graphs.RECIPES["G1"]["layers"] == ["pharos"]
