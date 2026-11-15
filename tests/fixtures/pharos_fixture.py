"""A tiny two-layer Pharos fixture (no real data): the entities the demo questions name, one pruned text-mining
edge, an Open Targets corroboration layer and a predictions file. Used by tests/test_pharos_app.py and by
scripts/ui_screenshots.js runs (python3 -m tests.fixtures.pharos_fixture <dir> writes it; TMKG_PHAROS_ROOT=<dir>
makes the app load it)."""
import gzip
import sys
from pathlib import Path

import pandas as pd

from pharos_app import KGX


def write(d, name, df):
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{name}.tsv.gz", "wt") as f:
        df.to_csv(f, sep="\t", index=False)


def edge(i, s, p, o, source, layer, version, **props):
    row = dict(id=f"e{i:016x}", subject=s, predicate=p, object=o, source=source, source_record=f"{source}|{s}|{o}",
               primary_knowledge_source="infores:" + source.split()[0].lower(), aggregator_knowledge_source="infores:x",
               knowledge_level="knowledge_assertion", agent_type="manual_agent", layer=layer, source_version=version,
               n_records="1")
    row.update(props)
    return row


GENE, PROT, DIS1, DIS2, CMP, BP = ("NCBIGene:5743", "UniProtKB:P35354", "MONDO:0004975", "MONDO:0005148",
                                  "CHEMBL.COMPOUND:CHEMBL521", "GO:0006954")



def build(root):
    """Writes the fixture layers under root (a Path). Returns root."""
    root = Path(root)
    (root / "sources").mkdir(parents=True, exist_ok=True)
    for stem in ("pharos", "opentargets_layer"):
        (root / "sources" / f"{stem}.yaml").write_text(f"name: {stem}\n")
    nodes = pd.DataFrame([
        dict(id=GENE, category="biolink:Gene", name="PTGS2"), dict(id="NCBIGene:6647", category="biolink:Gene", name="SOD1"),
        dict(id=PROT, category="biolink:Protein", name="Prostaglandin G/H synthase 2", symbol="PTGS2", tdl="Tclin"),
        dict(id=DIS1, category="biolink:Disease", name="Alzheimer disease"),
        dict(id=DIS2, category="biolink:Disease", name="type 2 diabetes mellitus"),
        dict(id=CMP, category="biolink:SmallMolecule", name="IBUPROFEN"),
        dict(id=BP, category="biolink:BiologicalProcess", name="inflammatory response"),
        dict(id="EFO:0004611", category="biolink:DiseaseOrPhenotypicFeature", name="low density lipoprotein cholesterol measurement")])
    edges = pd.DataFrame([
        edge(1, GENE, "biolink:gene_associated_with_condition", DIS1, "DisGeNET", "pharos", "pharos319", disgenet_score="0.4"),
        edge(2, GENE, "biolink:gene_associated_with_condition", DIS2, "CTD", "pharos", "pharos319"),
        edge(3, GENE, "biolink:gene_associated_with_condition", DIS1, "JensenLab Text Mining", "pharos", "pharos319"),
        edge(4, GENE, "biolink:has_gene_product", PROT, "Pharos target-gene mapping", "pharos", "pharos319"),
        edge(5, CMP, "biolink:directly_physically_interacts_with", PROT, "CHEMBL", "pharos", "pharos319",
             activity="6.2", activity_type="IC50", ligand_name="IBUPROFEN"),
        edge(6, PROT, "biolink:actively_involved_in", BP, "GO annotations", "pharos", "pharos319"),
        edge(7, "NCBIGene:6647", "biolink:genetically_associated_with", "EFO:0004611", "TIGA (GWAS Catalog)", "pharos",
             "pharos319", study_count="3", median_p_value="1e-9", mean_rank_score="0.7"),
        edge(8, GENE, "biolink:genetically_associated_with", DIS1, "TIGA (GWAS Catalog)", "pharos", "pharos319",
             study_count="1", median_p_value="1e-6", mean_rank_score="0.2")])
    write(root / "data" / "kg" / "pharos", "nodes", nodes)
    write(root / "data" / "kg" / "pharos", "edges", edges[KGX + ["disgenet_score", "activity", "activity_type",
                                                              "ligand_name", "study_count", "median_p_value", "mean_rank_score"]])
    write(root / "data" / "kg" / "pharos", "edge_verdicts", pd.DataFrame(
        [dict(id=f"e{i:016x}", verdict="keep" if i != 3 else "prune", confidence="0.9" if i != 3 else "0.1",
              reasons="" if i != 3 else "text mining only; no corroboration") for i in range(1, 9)]))
    ot_edges = pd.DataFrame([
        edge(101, GENE, "biolink:gene_associated_with_condition", DIS1, "Open Targets 26.09 overall direct",
             "opentargets_layer", "26.09", ot_score="0.55"),
        edge(102, CMP, "biolink:directly_physically_interacts_with", PROT, "ChEMBL 37 activity", "opentargets_layer",
             "26.09", first_year="1991")])
    write(root / "data" / "kg" / "opentargets", "nodes", nodes.iloc[:1])
    write(root / "data" / "kg" / "opentargets", "edges", ot_edges[KGX + ["ot_score", "first_year"]])
    (root / "sources" / "opentargets_layer.yaml").write_text("name: opentargets\n")
    write(root / "outputs" / "linkpred", "predictions", pd.DataFrame([
        dict(graph="G3", task="B", subject=GENE, object=DIS2, score="0.91", rank="1"),
        dict(graph="G3", task="B", subject="NCBIGene:6647", object=DIS1, score="0.77", rank="1"),
        dict(graph="G3", task="A", subject=CMP, object=PROT, score="0.5", rank="1")]))
    return root


if __name__ == "__main__":
    print(build(sys.argv[1]))
