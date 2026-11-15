"""The export fixes (pipelines/export_graph_kgx.py, block "export fixes"; rules in the source cards' `export_overrides`):
gene types from the biotype, corrected edge labels, compound names / plain-text definitions / the small sample, corrected
classes and identifiers. A fixture of four tiny layers (no real data) is exported with the REAL rules of sources/*.yaml as
KGX tables, as a card index and as Neptune files; the layer files and the graphs the benchmark reads must stay unchanged."""
import gzip
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, "pipelines")
import export_graph_kgx as xk  # noqa: E402
import make_graphs as mg  # noqa: E402
import neptune_export as nx  # noqa: E402
import pharos_index  # noqa: E402
import schema_card  # noqa: E402
from kgbuilder.biolink import DEFAULT as BIOLINK_YAML, BioLink  # noqa: E402

from fixtures.pharos_fixture import edge, write  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not BIOLINK_YAML.exists(), reason="biolink-model.yaml not available")

EGFR, AS1, TP53, PLAIN, PSEUDO = "NCBIGene:1956", "NCBIGene:100507500", "NCBIGene:7157", "NCBIGene:999", "NCBIGene:394269"
NOBIO, TEC = "ENSEMBL:ENSG00000000001", "ENSEMBL:ENSG00000000002"
P_EGFR, P_TP53 = "UniProtKB:P00533", "UniProtKB:P04637"
PPI = "biolink:physically_interacts_with"
CARDS = ("pharos", "dto", "opentargets_layer", "chembl37")
KA = dict(knowledge_level="knowledge_assertion", agent_type="manual_agent")


def _e(i, s, p, o, source, layer, **props):
    return edge(i, s, p, o, source, layer, "v1", **props)


def _nodes(folder, rows, source):
    """Writes a layer's node table (KGX columns the builder always writes: provided_by)."""
    write(folder, "nodes", pd.DataFrame([dict(r, provided_by=source) for r in rows]))


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """Four layers under a temporary root, with the repository's own source cards (so the real rules run)."""
    root = tmp_path_factory.mktemp("fixes")
    (root / "sources").mkdir()
    for c in CARDS:
        shutil.copy(ROOT / "sources" / f"{c}.yaml", root / "sources" / f"{c}.yaml")
    kg = root / "data" / "kg"
    # ---- pharos: genes typed protein-coding by the card's constant, two spellings of one Orphanet number, PPI rows
    _nodes(kg / "pharos", [
        dict(id=EGFR, category="biolink:Gene", name="EGFR", type="SO:0001217"),
        dict(id=AS1, category="biolink:Gene", name="EGFR-AS1", type="SO:0001217"),      # Open Targets says lncRNA
        dict(id=PLAIN, category="biolink:Gene", name="GENE999", type="SO:0001217"),     # no biotype anywhere
        dict(id=TP53, category="biolink:Gene", name="TP53", type=""),                   # an endpoint gene: no type
        dict(id=NOBIO, category="biolink:Gene", name="", type=""),                      # no type, no biotype
        dict(id=TEC, category="biolink:Gene", name="", type="SO:0001217"),              # typed by the card; biotype TEC
        dict(id=P_EGFR, category="biolink:Protein", name="Epidermal growth factor receptor", type="PR:000000001"),
        dict(id=P_TP53, category="biolink:Protein", name="Cellular tumor antigen p53", type="PR:000000001"),
        dict(id="ORDO:99", category="biolink:Disease", name="", type=""),
        dict(id="MONDO:0000001", category="biolink:Disease", name="disease", type="")], "infores:pharos")
    pe = [_e(1, EGFR, "biolink:has_gene_product", P_EGFR, "Pharos target-gene mapping", "L0"),
          _e(2, P_EGFR, PPI, P_TP53, "STRINGDB", "L0", stringdb_score="0.46", primary_knowledge_source="infores:string"),
          _e(3, P_TP53, PPI, P_EGFR, "BioPlex", "L0", bioplex_probability="0.9"),
          _e(4, P_EGFR, PPI, P_EGFR, "mock", "L0"),
          _e(5, P_TP53, PPI, P_TP53, "Reactome", "L0"),
          _e(6, P_EGFR, PPI, P_TP53, "Reactome,STRINGDB", "L0"),
          _e(7, P_TP53, PPI, P_EGFR, "BioPlex,STRINGDB", "L0", primary_knowledge_source="infores:string"),
          _e(8, EGFR, "biolink:gene_associated_with_condition", "ORDO:99", "eRAM", "L0"),
          _e(9, EGFR, "biolink:gene_associated_with_condition", "MONDO:0000001", "TIN-X", "L0",
             primary_knowledge_source="infores:pharos", knowledge_level="text_co_occurrence", agent_type="text_mining_agent")]
    write(kg / "pharos", "edges", pd.DataFrame(pe).fillna(""))
    write(kg / "pharos", "edge_verdicts", pd.DataFrame(
        [dict(id=f"e{i:016x}", verdict="prune" if i == 4 else "keep", confidence="0.1" if i == 4 else f"0.{i}", reasons="")
         for i in range(1, 10)]))
    # ---- dto: a protein family with a list-like definition, and a ChEBI class the table also lists
    _nodes(kg / "dto", [
        dict(id="DTO:05007741", category="biolink:ProteinFamily", name="Kinase", definition="['A kinase.', 'A kinase.']"),
        dict(id="CHEBI:24431", category="biolink:ProteinFamily", name="Chemical entity",
             definition='["A chemical entity.", "A molecular entity."]')], "infores:pharos")
    write(kg / "dto", "edges", pd.DataFrame([_e(30, P_EGFR, "biolink:member_of", "DTO:05007741", "Drug Target Ontology class", "L0"),
                                             _e(31, P_EGFR, "biolink:member_of", "CHEBI:24431", "Drug Target Ontology class", "L0")]))
    # ---- opentargets: the biotypes, the same Orphanet number spelled Orphanet:, index terms that are not diseases
    _nodes(kg / "opentargets", [
        dict(id=EGFR, category="biolink:Gene", name="EGFR", biotype="protein_coding"),
        dict(id=AS1, category="biolink:Gene", name="EGFR-AS1", biotype="lncRNA"),
        dict(id=TP53, category="biolink:Gene", name="TP53", biotype="protein_coding"),
        dict(id=PSEUDO, category="biolink:Gene", name="BRCA1P1", biotype="unprocessed_pseudogene"),
        dict(id=TEC, category="biolink:Gene", name="", biotype="TEC"),
        dict(id="Orphanet:99", category="biolink:Disease", name="a rare disorder", biotype=""),
        dict(id="Orphanet:100", category="biolink:Disease", name="another rare disorder", biotype=""),
        dict(id="PATO:0000070", category="biolink:Disease", name="amount", biotype=""),
        dict(id="UBERON:0000104", category="biolink:Disease", name="life cycle", biotype=""),
        dict(id="NCIT:C1", category="biolink:Disease", name="an index term", biotype=""),
        dict(id="MONDO:0000001", category="biolink:Disease", name="disease", biotype="")], "infores:opentargets")
    write(kg / "opentargets", "edges", pd.DataFrame([
        _e(40, TP53, "biolink:gene_associated_with_condition", "Orphanet:100", "Open Targets 26.09 overall direct", "L1"),
        _e(41, TP53, "biolink:gene_associated_with_condition", "Orphanet:99", "Open Targets 26.09 overall direct", "L1"),
        _e(42, TP53, "biolink:gene_associated_with_condition", "PATO:0000070", "Open Targets 26.09 overall direct", "L1")]))
    # ---- chembl37: an unnamed compound, and "protein" targets whose accession is an Ensembl gene
    _nodes(kg / "chembl37", [
        dict(id="CHEMBL.COMPOUND:CHEMBL941", category="biolink:SmallMolecule", name=""),
        dict(id="UniProtKB:ENSG00000000001", category="biolink:Protein", name=""),
        dict(id="UniProtKB:ENSG00000000003", category="biolink:Protein", name=""),
        dict(id=P_EGFR, category="biolink:Protein", name="")], "infores:chembl")
    write(kg / "chembl37", "edges", pd.DataFrame([
        _e(50, "CHEMBL.COMPOUND:CHEMBL941", "biolink:directly_physically_interacts_with", "UniProtKB:ENSG00000000003",
           "ChEMBL 37 activity", "L1"),
        _e(51, "CHEMBL.COMPOUND:CHEMBL941", "biolink:directly_physically_interacts_with", "UniProtKB:ENSG00000000001",
           "ChEMBL 37 activity", "L1"),
        _e(52, "CHEMBL.COMPOUND:CHEMBL941", "biolink:directly_physically_interacts_with", P_EGFR, "ChEMBL 37 activity", "L1")]))
    ref = root / "data" / "reference"
    ref.mkdir(parents=True)
    with gzip.open(ref / "chembl_synonyms.tsv.gz", "wt") as f:
        f.write("# source: ChEMBL (fixture)\nchembl_id\tparent_chembl_id\tpref_name\tmax_phase\tmolecule_type\tsynonyms\n"
                "CHEMBL941\tCHEMBL941\tIMATINIB\t4.0\tSmall molecule\tGleevec::TRADE_NAME\n")
    (ref / "hgnc_gene_ids.tsv").write_text("hgnc_id\tsymbol\tensembl_id\tncbigene_id\tuniprot_id\tlocus_group\n"
                                           "HGNC:11998\tTP53\tENSG00000000003\tNCBIGene:7157\tP04637\tprotein-coding gene\n")
    return root


@pytest.fixture(scope="module")
def kgx(repo):
    """The fixture exported as KGX tables (pruned edges kept and marked, so every rule has a row to act on)."""
    mg.RECIPES["GX"] = {"layers": list(CARDS), "prune": True}
    try:
        rep, problems, paths = xk.export_graph("GX", repo, out_dir=repo / "kgx_out", include_pruned=True)
    finally:
        mg.RECIPES.pop("GX")
    assert problems == [] and len(paths) == 2
    n = pd.read_csv(paths[0], sep="\t", dtype=str, keep_default_na=False).set_index("id")
    e = pd.read_csv(paths[1], sep="\t", dtype=str, keep_default_na=False).set_index("id")
    return rep, n, e


def _eid(i):
    return f"e{i:016x}"


# ------------------------------------------------------------------------------------------- the rules themselves
def test_every_term_the_real_rules_write_is_in_its_reference_file():
    ov = xk.load_overrides(ROOT)
    assert xk.check_overrides(ov, ROOT, BioLink()) == []
    so, gt = xk.so_terms(ROOT), ov["gene_type"]
    assert so["SO:0001217"] == "protein_coding_gene" and so["SO:0000704"] == "gene" and so["SO:0002127"] == "lncRNA_gene"
    assert gt["map"]["protein_coding"] == "SO:0001217" and gt["map"]["lncRNA"] == "SO:0002127" and gt["unknown"] == "SO:0000704"
    assert set(gt["map"].values()) | {gt["unknown"]} <= set(so)
    # the name written next to each id in the card is the ontology's own name for it
    text = (ROOT / "sources" / "opentargets_layer.yaml").read_text()
    named = re.findall(r'^\s+\w+: "(SO:\d+)"\s+# (\S+)', text, re.M)
    assert len(named) == len(gt["map"]) + 1 and all(so[i] == name for i, name in named)
    status = xk.infores_status(ROOT)
    assert status["infores:string"] == "released" and status["infores:tinx"] == status["infores:tin-x"] == "not in registry"
    assert status["infores:bioplex"] == "not in registry" and status["infores:open-targets"] == "released"


def test_a_rule_with_a_term_that_is_not_in_the_reference_files_stops_the_export(repo):
    ov = xk.load_overrides(ROOT)
    bad = json.loads(json.dumps(ov))
    bad["gene_type"]["map"]["lncRNA"] = "SO:9999999"
    bad["relabel_edges"]["pharos"].append(dict(name="tinx", when={"source": "TIN-X"}, set={"primary_knowledge_source": "infores:tinx"}))
    bad["relabel_edges"]["pharos"].append(dict(name="level", when={"source": "x"}, set={"knowledge_level": "curated"}))
    bad["relabel_edges"]["pharos"].append(dict(name="other", when={"source": "x"}, set={"predicate": "biolink:related_to"}))
    bad["retype_nodes"].append(dict(layer="dto", name="r", prefixes=["X"], **{"from": "biolink:Disease", "to": "biolink:NotAClass"}))
    problems = " | ".join(xk.check_overrides(bad, ROOT, BioLink()))
    for needle in ("'SO:9999999' for 'lncRNA'", "'infores:tinx' is not a released entry", "knowledge_level 'curated'",
                   "column 'predicate' may not be set", "'biolink:NotAClass' is not a BioLink class"):
        assert needle in problems
    # and through the export: nothing is written
    card = repo / "sources" / "opentargets_layer.yaml"
    good = card.read_text()
    mg.RECIPES["GB"] = {"layers": list(CARDS), "prune": True}
    try:
        card.write_text(good.replace('lncRNA: "SO:0002127"', 'lncRNA: "SO:9999999"'))
        rep, problems, paths = xk.export_graph("GB", repo, out_dir=repo / "bad_out")
        with pytest.raises(ValueError, match="SO:9999999"):
            pharos_index.build_index(repo, out=repo / "bad.sqlite", log=lambda *_: None)
    finally:
        card.write_text(good)
        mg.RECIPES.pop("GB")
    assert paths == () and any("SO:9999999" in p for p in problems) and not (repo / "bad_out" / "nodes.tsv.gz").exists()
    assert "PROBLEM: export rule" in (repo / "bad_out" / "kgx_report.md").read_text()


def test_a_card_without_the_block_changes_nothing(tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "pharos.yaml").write_text("name: pharos\n")
    ov = xk.load_overrides(tmp_path, ["pharos", "trialmindkg"])
    assert ov["layers"] == [] and ov["gene_type"] is None and xk.check_overrides(ov, tmp_path) == []
    nodes = pd.DataFrame([dict(id="NCBIGene:1", category="biolink:Gene", name="A", definition="[plain text]")])
    edges = pd.DataFrame([dict(id="e", subject="NCBIGene:1", object="NCBIGene:1", predicate=PPI, source="STRINGDB", **KA)])
    fixed, counts = xk.fix_nodes(nodes, ov)
    assert fixed.equals(nodes) and "gene_types" not in counts
    assert xk.fix_edges(edges, "pharos", ov, {}) is edges and xk.id_renames(nodes["id"], ov, tmp_path) == ({}, {}, {})


def test_clean_text_list_only_touches_lists_it_can_read():
    assert xk.clean_text_list("['A kinase.', 'A kinase.']") == "A kinase."
    assert xk.clean_text_list('["a  b", "c", "a b"]') == "a b|c"
    assert xk.clean_text_list("['x' 'y']") == "['x' 'y']"                 # numpy style: not glued into 'xy'
    assert xk.clean_text_list("[see ref 3] a sentence") == "[see ref 3] a sentence" and xk.clean_text_list("") == ""
    assert xk.clean_text_list("[]") == "" and xk.clean_text_list('[["nested"]]') == '[["nested"]]'


def test_interaction_rows_named_by_no_rule_and_no_exception_are_counted():
    """Finding H of the S03 review: source cells 'BioPlex,Reactome,STRINGDB' and 'BioPlex,Reactome' were named nowhere.
    Now every kept_as_is entry with a `when` is checkable, those cells are listed, and an unlisted cell is counted."""
    ov = xk.load_overrides(ROOT)
    listed = [k for k in ov["kept_as_is"] if k.get("when")]
    assert listed and set(listed[0]["when"]["source"]) >= {"Reactome", "Reactome,STRINGDB", "BioPlex,Reactome", "BioPlex,Reactome,STRINGDB"}
    assert xk.check_overrides(ov, ROOT) == []
    rows = [dict(id=f"e{i}", subject="a", object="b", predicate=PPI, source=s, knowledge_level="knowledge_assertion", agent_type="manual_agent")
            for i, s in enumerate(["STRINGDB", "BioPlex,Reactome,STRINGDB", "BioPlex,Reactome", "Reactome", "mock", "NEWSOURCE", "NEWSOURCE"])]
    rows.append(dict(id="e9", subject="a", object="b", predicate="biolink:related_to", source="NEWSOURCE", knowledge_level="x", agent_type="y"))
    counts = {}
    out = xk.fix_edges(pd.DataFrame(rows), "pharos", ov, None, counts)
    assert out.loc[0, "knowledge_level"] == "statistical_association" and out.loc[1, "knowledge_level"] == "knowledge_assertion"
    assert counts[f"{xk.UNCOVERED}: source=NEWSOURCE"] == 2 and [k for k in counts if k.startswith(xk.UNCOVERED)] == [f"{xk.UNCOVERED}: source=NEWSOURCE"]
    md = "\n".join(xk.fixes_markdown(xk.overrides_report("kgx GX", ov, {}, {"pharos": counts})))
    assert "no rule and no listed exception: source=NEWSOURCE: 2." in md
    assert xk.uncovered_rows(pd.DataFrame(rows[:5]), "pharos", ov) == {}            # every listed cell is covered
    assert xk.uncovered_rows(pd.DataFrame(rows), "chembl37", ov) == {}              # a layer without rules: nothing to check
    bad = xk.load_overrides(ROOT)
    bad["kept_as_is"].append(dict(layer="pharos", what="x", why="y", when="source=Reactome"))
    assert any("kept_as_is (pharos)" in p for p in xk.check_overrides(bad, ROOT))


def test_a_label_rule_is_not_applied_when_its_column_is_missing():
    ov = xk.load_overrides(ROOT)
    assert xk.rule_columns(ov, "pharos") == {"predicate", "source"} and xk.rule_columns(ov, "chembl37") == set()
    chunk = pd.DataFrame([dict(id="e", subject="a", object="b", knowledge_level="knowledge_assertion", source="STRINGDB")])
    counts = {}
    out = xk.fix_edges(chunk, "pharos", ov, None, counts)
    assert out["knowledge_level"].tolist() == ["knowledge_assertion"] and counts["not applied: STRING score rows are a statistical association"] == 1


# ------------------------------------------------------------------------------------------- gene types (ES-02)
def test_every_gene_node_gets_the_so_type_of_its_biotype(kgx):
    rep, n, _ = kgx
    genes = n[n["category"] == "biolink:Gene"]
    assert (genes["type"] != "").all()
    want = {EGFR: "SO:0001217", TP53: "SO:0001217",        # protein_coding (TP53 had no type at all)
            AS1: "SO:0002127",                             # lncRNA: the card's protein-coding constant is replaced
            PSEUDO: "SO:0001760",                          # unprocessed_pseudogene
            PLAIN: "SO:0001217",                           # no biotype: the type the Pharos card gave is kept
            NOBIO: "SO:0000704",                           # nothing known
            TEC: "SO:0000704"}                             # a biotype without an SO gene term: the card's type does not survive
    assert {k: genes.loc[k, "type"] for k in want} == want
    assert n.loc[P_EGFR, "type"] == "PR:000000001"                              # other classes are not touched
    c = rep["overrides"]["nodes"]
    assert c["genes"] == len(genes) == sum(c["gene_types"].values())
    assert c["gene_types"] == genes["type"].value_counts().to_dict()
    assert c["gene_type_changed"] == {"SO:0001217 -> SO:0000704": 1, "SO:0001217 -> SO:0002127": 1}
    assert c["biotypes_without_term"] == {"TEC": 1}
    assert c["gene_type_basis"] == {"biotype": 4, "type given by a source card": 1, xk.NO_BIOTYPE: 1, xk.NO_TERM: 1}


# ------------------------------------------------------------------------------------------- edge labels (ES-08)
def test_score_based_ppi_rows_are_relabelled_and_curated_rows_are_not(kgx):
    rep, _, e = kgx
    stat = ("statistical_association", "data_analysis_pipeline")
    labels = {i: (e.loc[_eid(i), "knowledge_level"], e.loc[_eid(i), "agent_type"]) for i in range(1, 10)}
    assert labels[2] == labels[3] == labels[7] == stat                          # STRINGDB, BioPlex, BioPlex,STRINGDB
    assert labels[4] == ("not_provided", "not_provided")                        # the placeholder self-pair
    assert labels[5] == labels[6] == labels[1] == ("knowledge_assertion", "manual_agent")   # Reactome, mixed, gene -> protein
    assert labels[9] == ("text_co_occurrence", "text_mining_agent")
    assert e.loc[_eid(9), "primary_knowledge_source"] == "infores:pharos"       # TIN-X: no registry id, kept
    assert e.loc[_eid(2), "primary_knowledge_source"] == "infores:string"       # sources are not rewritten by the label rules
    assert (e.loc[_eid(2), "verdict"], e.loc[_eid(2), "confidence"]) == ("keep", "0.2")     # verdicts untouched
    assert rep["overrides"]["edges"]["pharos"] == {"BioPlex probability rows are a statistical association": 1,
                                                   "STRING score rows are a statistical association": 2,
                                                   "placeholder self-pairs have no knowledge level": 1}


def test_layer_files_and_the_benchmark_graphs_keep_the_original_labels_and_ids(repo, kgx):
    raw = pd.read_csv(repo / "data" / "kg" / "pharos" / "edges.tsv.gz", sep="\t", dtype=str, keep_default_na=False)
    assert set(raw.loc[raw["predicate"] == PPI, "knowledge_level"]) == {"knowledge_assertion"}
    mg.RECIPES["GX"] = {"layers": list(CARDS), "prune": True}
    try:
        nodes, edges, _ = mg.assemble("GX", repo)                               # what the training graphs are made of
    finally:
        mg.RECIPES.pop("GX")
    assert set(edges.loc[edges["predicate"] == PPI, "knowledge_level"]) == {"knowledge_assertion"}
    assert {"Orphanet:99", "ORDO:99", "UniProtKB:ENSG00000000003"} <= set(nodes["id"])
    assert dict(zip(nodes["id"], nodes["category"]))["CHEBI:24431"] == "biolink:ProteinFamily"


# ------------------------------------------------------------------------------------------- names, text, sample (ES-10)
def test_compounds_are_named_from_chembl_and_definitions_are_plain_text(kgx):
    rep, n, _ = kgx
    assert n.loc["CHEMBL.COMPOUND:CHEMBL941", "name"] == "IMATINIB"
    assert n.loc["DTO:05007741", "definition"] == "A kinase."
    assert n.loc["CHEBI:24431", "definition"] == "A chemical entity.|A molecular entity."
    c = rep["overrides"]["nodes"]
    assert c["named_from_chembl"] == 1 and c["text_cells_cleaned"] == {"definition": 2}
    assert c["unnamed"] == n.loc[n["name"] == "", "category"].value_counts().to_dict()
    assert n.loc[EGFR, "biotype"] == "protein_coding"                           # per-field merge: Pharos row + Open Targets biotype


def test_sample_folder_is_small_capped_and_opens_with_the_readme_snippet(repo, kgx):
    out = repo / "sample"
    got = xk.write_sample(repo / "kgx_out", out, hop1=1, hop2=1)
    n = pd.read_csv(out / "kgx" / "nodes.tsv", sep="\t", dtype=str, keep_default_na=False)
    e = pd.read_csv(out / "kgx" / "edges.tsv", sep="\t", dtype=str, keep_default_na=False)
    assert (len(n), len(e)) == (got["nodes"], got["edges"]) and set(e["subject"]) | set(e["object"]) == set(n["id"])
    seeds = {EGFR, P_EGFR}
    first = e[e["subject"].isin(seeds) | e["object"].isin(seeds)]
    assert first.groupby(["predicate", "primary_knowledge_source"]).size().max() == 1          # the first-step cap
    assert got["info"]["hop1_edges"] == len(first) < got["info"]["seed_edges_in_graph"]
    assert got["info"]["hop2_edges"] == len(e) - len(first) > 0                                # a second step exists
    # of EGFR's two STRING-source rows the more confident one is kept (0.7 before 0.2)
    assert _eid(7) in set(first["id"]) and _eid(2) not in set(e["id"])
    readme = (out / "README.md").read_text()
    assert "EGFR" in readme and f"{len(n):,} nodes and {len(e):,} edges" in readme and xk.PANDAS_SNIPPET in readme
    assert len(xk.PANDAS_SNIPPET.splitlines()) == 5
    assert not re.search(r"/tmp|/home|\bG[0-4]\b|PK\d|ES-\d", readme)                          # nothing internal in a public file
    run = subprocess.run([sys.executable, "-c", xk.PANDAS_SNIPPET], cwd=out, capture_output=True, text=True)
    assert run.returncode == 0 and f"{len(n)} nodes, {len(e)} edges" in run.stdout
    sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
    import hashlib
    assert set(sums) == set(got["files"]) - {"SHA256SUMS"} and "pyg/load_pyg.py" in sums
    assert all(hashlib.sha256((out / f).read_bytes()).hexdigest() == h for f, h in sums.items())
    assert any(f.startswith("pyg/nodes_Gene") for f in sums) and any(f.startswith("pyg/edges_") for f in sums)
    row = got["downloads_row"]
    assert row["path"] == "sample/" and row["bytes"] == sum((out / f).stat().st_size for f in got["files"])
    again = xk.write_sample(repo / "kgx_out", repo / "sample2", hop1=1, hop2=1)                # same input, same bytes
    assert (repo / "sample2" / "SHA256SUMS").read_text() == (out / "SHA256SUMS").read_text() and again["bytes"] == got["bytes"]
    with pytest.raises(ValueError, match="no edge touches"):
        xk.write_sample(repo / "kgx_out", repo / "sample3", seeds=("NCBIGene:0",))
    assert xk.sample_seeds(ROOT) == (EGFR, P_EGFR) and xk.sample_seeds(repo / "nowhere") == ()
    assert xk.main(["--sample", "--kgx", str(repo / "kgx_out"), "--sample-out", str(repo / "sample4")]) == 0
    assert (repo / "sample4" / "kgx" / "edges.tsv").exists()


# ------------------------------------------------------------------------------------------- classes, identifiers (ES-12)
def test_classes_and_identifiers_are_corrected_and_edges_follow(kgx):
    rep, n, e = kgx
    assert n.loc["PATO:0000070", "category"] == n.loc["UBERON:0000104", "category"] == "biolink:DiseaseOrPhenotypicFeature"
    assert n.loc["NCIT:C1", "category"] == "biolink:DiseaseOrPhenotypicFeature"
    assert n.loc["MONDO:0000001", "category"] == "biolink:Disease"                           # a disease stays a disease
    assert n.loc["CHEBI:24431", "category"] == "biolink:ChemicalEntity" and n.loc["DTO:05007741", "category"] == "biolink:ProteinFamily"
    # one spelling for Orphanet numbers: the two rows became one node, with the name the other layer had
    assert not any(i.startswith("Orphanet:") for i in n.index) and {"ORDO:99", "ORDO:100"} <= set(n.index)
    assert n.loc["ORDO:99", "name"] == "a rare disorder" and n.loc["ORDO:99", "category"] == "biolink:Disease"
    assert e.loc[_eid(41), "object"] == e.loc[_eid(8), "object"] == "ORDO:99" and e.loc[_eid(40), "object"] == "ORDO:100"
    # an Ensembl gene accession written as a UniProt id: a gene, with the NCBI Gene id where HGNC has one
    assert not any(i.startswith("UniProtKB:ENSG") for i in n.index)
    assert e.loc[_eid(50), "object"] == TP53 and e.loc[_eid(51), "object"] == NOBIO and e.loc[_eid(52), "object"] == P_EGFR
    assert n.loc[NOBIO, "category"] == "biolink:Gene" and n.loc[TP53, "name"] == "TP53"
    assert set(e["subject"]) | set(e["object"]) <= set(n.index) and e.index.is_unique      # ids of edges never change
    o = rep["overrides"]
    assert o["ids_renamed"] == {"Ensembl gene accession written as a UniProt id": 2, "Orphanet: written as ORDO:": 2}
    assert ["Orphanet:99", "ORDO:99"] in o["ids_renamed_examples"] and o["edges"]["chembl37"] == {xk.RENAMED: 2}
    assert o["nodes"]["retyped"] == {"ChEBI classes in the DTO table are chemical entities": 1,
                                     "Open Targets index terms that are not diseases": 3}
    assert ["PATO:0000070", "amount"] in o["nodes"]["retyped_nodes"]["Open Targets index terms that are not diseases"]
    pre = o["prefixes_not_listed_by_biolink"]
    assert "ORDO" not in pre.get("biolink:Disease", {}) and pre["biolink:ProteinFamily"] == {"DTO": 1}
    assert pre["biolink:DiseaseOrPhenotypicFeature"] == {"NCIT": 1, "PATO": 1, "UBERON": 1}


def test_report_says_project_validator_and_lists_the_corrections(repo, kgx):
    rep, _, _ = kgx
    text = (repo / "kgx_out" / "kgx_report.md").read_text()
    assert "passes the project validator (columns, ids, endpoints, BioLink terms)" in text and "valid KGX" not in text
    assert "## Corrections made at export" in text and "Compounds named from the ChEMBL name table: 1." in text
    assert "SO:0001217 3" in text and "STRING score rows are a statistical association: 2." in text
    assert "valid KGX" not in (xk.validate_kgx.__doc__.replace("not a claim of 'valid KGX'", ""))
    saved = json.loads((repo / xk.REPORT_DIR / "gx.json").read_text())
    assert saved == json.loads(json.dumps(rep["overrides"])) and saved["totals"]["nodes"] == rep["n_nodes"]
    assert str(repo) not in json.dumps(saved)                                   # no machine path in a committed file


# ------------------------------------------------------------------------------------------- the card index
@pytest.fixture(scope="module")
def index(repo):
    stats = pharos_index.build_index(repo, out=repo / "ix.sqlite", chunk=4, log=lambda *_: None)
    return pharos_index.CardIndex(stats["path"])


def test_card_index_carries_the_same_corrections(repo, index, kgx):
    rep, n, e = kgx
    node = lambda i: index._node(i)[0]                                                           # noqa: E731
    assert node(AS1)["type"] == "SO:0002127" and node(TP53)["type"] == "SO:0001217" and node(NOBIO)["type"] == "SO:0000704"
    assert node("CHEBI:24431")["category"] == "biolink:ChemicalEntity" and node("DTO:05007741")["definition"] == "A kinase."
    assert node("PATO:0000070")["category"] == "biolink:DiseaseOrPhenotypicFeature"
    assert index._node("Orphanet:99") is None and node("ORDO:99")["name"] == "a rare disorder"
    assert index._node("UniProtKB:ENSG00000000003") is None
    ids = {r["id"]: r for r in index.con.execute("SELECT id, type FROM (SELECT id, json_extract(props, '$.type') AS type, "
                                                 "category FROM nodes) WHERE category = 'biolink:Gene'")}
    assert {k: v["type"] for k, v in ids.items()} == n.loc[n["category"] == "biolink:Gene", "type"].to_dict()
    rows = {r["id"]: r["knowledge_level"] for r in index.con.execute("SELECT id, knowledge_level FROM rows")}
    assert rows[_eid(2)] == rows[_eid(3)] == "statistical_association" and rows[_eid(5)] == "knowledge_assertion"
    x = index.explain_edge(_eid(2))
    assert x["provenance"]["knowledge_level"] == "statistical_association" and x["provenance"]["agent_type"] == "data_analysis_pipeline"
    assert index.explain_edge(_eid(9))["provenance"]["primary_knowledge_source"] == "infores:pharos"
    card = index.card(TP53)
    others = {r["other"] for k in card["known"] for r in k["rows"]}
    assert "ORDO:100" in others and "CHEMBL.COMPOUND:CHEMBL941" in others and not any(o.startswith("Orphanet:") for o in others)
    fixes = json.loads(index.meta["export_overrides"])
    assert fixes["nodes"]["named_from_chembl"] == 1 and fixes["nodes"]["unnamed"] == rep["overrides"]["nodes"]["unnamed"]
    assert fixes["product"] == "card index" and fixes["nodes"]["gene_types"] == rep["overrides"]["nodes"]["gene_types"]
    assert fixes["edges"]["pharos"] == rep["overrides"]["edges"]["pharos"]      # each row counted once, not once per pass
    assert json.loads((repo / xk.REPORT_DIR / "card_index.json").read_text()) == fixes
    levels = set(pharos_index._value_sets(repo, chunk=4)["knowledge_level"])
    assert {"statistical_association", "not_provided", "text_co_occurrence", "knowledge_assertion"} == levels
    assert set(json.loads(index.meta["rel_values"])["knowledge_level"]) == levels


# ------------------------------------------------------------------------------------------- the Neptune files
def test_neptune_files_carry_the_corrected_classes_ids_and_labels(repo, tmp_path):
    mg.RECIPES["GX"] = {"layers": list(CARDS), "prune": True}
    try:
        man = nx.export(repo, tmp_path / "nep", "GX", chunk=4, log=lambda *_: None)
    finally:
        mg.RECIPES.pop("GX")
    oc = tmp_path / "nep" / "opencypher"
    assert nx.validate_export(oc) == []
    read = lambda pre: pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False) for f in sorted(oc.glob(f"{pre}_*.csv.gz"))])   # noqa: E731
    n, e = read("nodes").set_index(":ID"), read("edges").set_index(":ID")
    assert list(n.reset_index().columns) == nx.NODE_HEADER                      # the file layout is unchanged
    assert n.loc["CHEBI:24431", ":LABEL"] == "ChemicalEntity" and n.loc["PATO:0000070", ":LABEL"] == "DiseaseOrPhenotypicFeature"
    assert "Orphanet:99" not in n.index and n.loc["ORDO:99", "name:String"] == "a rare disorder"
    assert not any(i.startswith("UniProtKB:ENSG") for i in n.index) and e.loc[_eid(50), ":END_ID"] == TP53
    assert e.loc[_eid(2), "knowledge_level:String"] == "statistical_association" and e.loc[_eid(2), "agent_type:String"] == "data_analysis_pipeline"
    assert e.loc[_eid(5), "knowledge_level:String"] == "knowledge_assertion" and _eid(4) not in e.index     # the pruned placeholder
    fx = man["export_overrides"]
    assert fx["edges"]["pharos"]["STRING score rows are a statistical association"] == 2      # the parity pass is not counted
    assert fx["edges"]["pharos"]["placeholder self-pairs have no knowledge level"] == 0       # pruned, not written: not counted
    assert fx["nodes"]["named_from_chembl"] == 1 and "biolink:SmallMolecule" not in fx["nodes"]["unnamed"]
    assert fx["ids_renamed"]["Orphanet: written as ORDO:"] == 2 and "gene_types" not in fx["nodes"]
    readme = (tmp_path / "nep" / "README.md").read_text()
    assert "`biolink_predicate`" in readme and "`subClassOf`" in readme and "Corrections made when the files were written" in readme
    assert nx.readme_markdown({k: v for k, v in man.items() if k != "export_overrides"})       # an older manifest still renders


# ------------------------------------------------------------------------------------------- the schema card
def test_schema_card_lists_rules_now_and_counts_once_an_export_has_run(repo, kgx, index):
    ex = schema_card.export_section(ROOT)                                       # the repository: rules, no export run here
    md = "\n".join(schema_card.export_markdown(ex))
    assert md.startswith(schema_card.EXPORT_HEADING) and "the benchmark graphs used the original labels" in md
    assert "| `SO:0002127` | lncRNA_gene | lncRNA |" in md and "| `SO:0000704` | gene | no biotype" in md
    assert "source = STRINGDB or BioPlex,STRINGDB" in md and "knowledge_level = statistical_association" in md
    assert "TIN-X text-mining rows: primary_knowledge_source infores:pharos" in md and ".." not in md.replace("...", "")
    assert [u["id"] for u in ex["unregistered_infores"]] == ["infores:bioplex", "infores:opentargets"]
    assert {p["prefix"] for p in ex["prefix_rules"]} == {"EFO", "OBA", "Orphanet"} and ex["rule_problems"] == []
    if not ex["reports"]:
        assert "not measured yet" in md
    # the committed card's export section is the generated one (python pipelines/schema_card.py --export-section)
    committed = (ROOT / "docs" / "SCHEMA_CARD.md").read_text()
    assert committed.endswith(md + "\n") and committed.count(schema_card.EXPORT_HEADING) == 1
    # with the reports an export wrote: measured counts, per product
    ex2 = schema_card.export_section(repo)
    assert list(ex2["reports"]) == ["card_index", "gx"]
    md2 = "\n".join(schema_card.export_markdown(ex2))
    assert "not measured yet" not in md2 and "7 Gene nodes, all typed; 3 of them SO:0001217" in md2
    for text in (md, md2):                                                      # served by the app: plain words, no paths or stems
        assert not re.search(r"outputs/|\.json|re-run|card_index|kgx G|\(g[0-9x]\)", text), text[:200]
    assert "edge rows, the app's card index | edge rows, KGX tables (GX)" in md2 and "Measured on the app's card index: 7 Gene" in md2
    assert schema_card.product_words("g3") == "KGX tables of the release graph"
    assert "| `SO:0002127` | lncRNA_gene | lncRNA | 1 |" in md2 and "SO:0001217 -> SO:0002127 (1)" in md2 and "TEC (1)" in md2
    assert "computed by a pipeline; was knowledge_assertion + manual_agent | 2 | 2 |" in md2    # STRING rows: index and KGX
    assert "| ProteinFamily | DTO | 1 |" in md2 and "PATO:0000070 (amount)" in md2


def test_schema_card_export_section_is_rewritten_in_place(tmp_path):
    for c in CARDS:
        (tmp_path / "sources").mkdir(exist_ok=True)
        shutil.copy(ROOT / "sources" / f"{c}.yaml", tmp_path / "sources" / f"{c}.yaml")
    (tmp_path / "docs").mkdir()
    card = tmp_path / "docs" / "SCHEMA_CARD.md"
    card.write_text("# Schema card\n\n| layer counts from the last full run |\n")
    schema_card.write_export_section(tmp_path)
    once = card.read_text()
    assert once.startswith("# Schema card\n\n| layer counts from the last full run |\n\n" + schema_card.EXPORT_HEADING)
    schema_card.write_export_section(tmp_path)
    assert card.read_text() == once                                              # replaced, not appended twice


def test_the_brief_iri_row_states_a_measured_count_never_every_gene_node(repo, index):
    now = {r["curie"]: r["where"] for r in schema_card.brief_iris(ROOT / "sources" / "pharos.yaml")}
    assert "every Gene node" not in now["SO:0001217"] and "every Protein node" not in now["PR:000000001"]
    assert "protein-coding by Open Targets biotype" in now["SO:0001217"]
    measured = {r["curie"]: r["where"] for r in schema_card.brief_iris(repo / "sources" / "pharos.yaml")}
    assert measured["SO:0001217"].startswith("type of 3 of 7 Gene nodes (")      # from the card index report of the fixture
    for text in list(now.values()) + list(measured.values()):
        assert not re.search(r"outputs/|\.json|\.tsv|\bG[0-4]\b", text)          # shown on a page: no paths, no graph codes
