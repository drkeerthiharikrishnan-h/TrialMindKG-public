"""
Build the five link-prediction graphs G0-G4 (EXECUTION_PLAN.md Phase 1e) as PyG HeteroData files.

    G0 = Pharos (L0)                    G1 = G0 with pruning-v1 'prune' edges removed
    G2 = Pharos + L1 + L2 layers        G3 = G2 with every layer's 'prune' edges removed
    G4 = G3 + the trial layer (L3: the 34-program TrialMindKG graph, outputs/kgx/)

Usage:   python pipelines/make_graphs.py [G0,G1,...] [--allow-missing]
         (after python pipelines/build_kg.py <card> and python pipelines/prune_kg.py <card> for each layer)
Reads:   data/kg/<card name>/nodes.tsv.gz, edges.tsv.gz, edge_verdicts.tsv.gz (KGX; written by build_kg / prune_kg)
         outputs/kgx/nodes.tsv, edges.tsv (trial layer, written by seed_kg.py); its ENSEMBL gene ids are renamed to
         NCBIGene through data/reference/hgnc_gene_ids.tsv and CHEMBL: to CHEMBL.COMPOUND:, so they join Pharos
Writes:  outputs/<g0..g4>/heterodata.pt (git-ignored, large)
         outputs/<g0..g4>/summary.md (committed: which layers went in, edge counts, layers missing or not pruned)

A graph whose layers are not all built yet is NOT written (it would silently equal a smaller graph), unless
--allow-missing is given; the summary then lists the missing layers.
Pruning uses each layer's own verdict file; a layer without one is kept whole and listed as "not pruned".
"""
import gzip, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kgbuilder import engine  # noqa: E402
import hgnc_ref  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRIAL_LAYER = "trialmindkg"
# layer = source card stem in sources/ (its 'name' field names data/kg/<name>/); TRIAL_LAYER is outputs/kgx/
L1_L2 = ["opentargets_layer", "chembl37", "hpo_hierarchy", "go_hierarchy", "mondo_hierarchy"]
# The Drug Target Ontology classes Pharos delivers (sources/dto.yaml, CC-34; review m12) join the enriched graphs G2-G4 only:
# G0 / G1 stay "Pharos alone" as the frozen benchmark was run (docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md section 3).
# The DTO layer must be built (python pipelines/build_kg.py sources/dto.yaml) before make_graphs, or run it with --allow-missing.
DTO_LAYER = "dto"
RECIPES = {
    "G0": {"layers": ["pharos"], "prune": False},
    "G1": {"layers": ["pharos"], "prune": True},
    "G2": {"layers": ["pharos", DTO_LAYER] + L1_L2, "prune": False},
    "G3": {"layers": ["pharos", DTO_LAYER] + L1_L2, "prune": True},
    "G4": {"layers": ["pharos", DTO_LAYER] + L1_L2 + [TRIAL_LAYER], "prune": True},
}


# One BioLink class per identifier prefix, whatever a layer said: layers disagree (Open Targets' disease index
# lists HP and GO terms as Disease; Pharos types EFO traits as DiseaseOrPhenotypicFeature, Open Targets as Disease),
# and with "first layer wins" the same kind of node ends up under three node types and the edge types multiply
# (the 2026-11-01 smoke run: 42 edge types in G2 vs 12 in G0, e.g. Disease|has_phenotype|Disease,
# BiologicalProcess|part_of|Disease, Gene|gene_associated_with_condition|PhenotypicFeature), each with its own
# R-GCN weights. GO terms take the class the GO layer gives them (process / function / component).
PREFIX_CATEGORY = {"MONDO": "biolink:Disease", "DOID": "biolink:Disease", "Orphanet": "biolink:Disease",
                   "ORDO": "biolink:Disease", "OMIM": "biolink:Disease", "UMLS": "biolink:Disease",
                   "EFO": "biolink:DiseaseOrPhenotypicFeature", "HP": "biolink:PhenotypicFeature",
                   "MP": "biolink:PhenotypicFeature",
                   "OBA": "biolink:PhenotypicFeature"}   # Ontology of Biological Attributes: measurement traits, not diseases
PREFIX_OWNER = {"GO": "go_hierarchy"}       # prefix -> the layer whose class wins for it


def harmonise_categories(nodes):
    """Takes the combined node table (id, category, optional layer). Returns (nodes, n_retyped): every id with a
    prefix in PREFIX_CATEGORY gets that class; a prefix in PREFIX_OWNER gets the class its owner layer assigns
    (when that layer holds the id); other ids keep the class they came with."""
    prefix = nodes["id"].str.extract(r"^([^:]+):", expand=False)
    fixed = prefix.map(PREFIX_CATEGORY)
    new = nodes["category"].where(fixed.isna(), fixed)
    if "layer" in nodes:
        for pre, owner in PREFIX_OWNER.items():
            owned = nodes[(prefix == pre) & (nodes["layer"] == owner)]
            cls = dict(zip(owned["id"], owned["category"]))
            m = (prefix == pre) & nodes["id"].isin(cls)
            new = new.where(~m, nodes["id"].map(cls))
    n = int((new != nodes["category"]).sum())
    return nodes.assign(category=new), n


def layer_dir(layer, root=ROOT):
    """Folder with a layer's built KGX files, or None if its card does not exist yet."""
    if layer == TRIAL_LAYER:
        return root / "outputs" / "kgx"
    card = root / "sources" / f"{layer}.yaml"
    if not card.exists():
        return None
    return root / "data" / "kg" / engine.load_card(card)["name"]


def trial_id_map(root=ROOT):
    """Renames that make the trial layer's ids match Pharos: ENSEMBL:ENSG... -> NCBIGene:... through the HGNC
    reference table (data/reference/hgnc_gene_ids.tsv; a gene HGNC does not map keeps its id). Returns a dict."""
    ref = root / "data" / "reference" / "hgnc_gene_ids.tsv"
    if not ref.exists():
        return {}
    return {"ENSEMBL:" + ens: ncbi for ens, ncbi in hgnc_ref.load_lookup("ensembl_id", "ncbigene_id", ref).items()}


def rename_ids(nodes, edges, mapping):
    """Applies trial_id_map plus the prefix CHEMBL: -> CHEMBL.COMPOUND: (BioLink's prefix, used by Pharos) to node
    ids and edge endpoints. Returns (nodes, edges, n_renamed); nodes that become the same id are merged."""
    def one(v):
        if v in mapping:
            return mapping[v]
        return "CHEMBL.COMPOUND:" + v[len("CHEMBL:"):] if v.startswith("CHEMBL:") else v
    new_ids = nodes["id"].map(one)
    n = int((new_ids != nodes["id"]).sum())
    nodes = nodes.assign(id=new_ids).drop_duplicates("id", keep="first")
    edges = edges.assign(subject=edges["subject"].map(one), object=edges["object"].map(one))
    return nodes, edges, n


def read_layer(layer, root=ROOT):
    """Takes a layer name. Returns (nodes [id, category], edges [id, subject, predicate, object, knowledge_level,
    layer], verdicts or None),
    or None when the layer is not built yet. Multi-valued categories ('a|b') keep the first (most specific) class."""
    d = layer_dir(layer, root)
    if d is None:
        return None
    ext = ".tsv" if layer == TRIAL_LAYER else ".tsv.gz"
    nodes_p, edges_p = d / f"nodes{ext}", d / f"edges{ext}"
    if not (nodes_p.exists() and edges_p.exists()):
        return None
    nodes = pd.read_csv(nodes_p, sep="\t", dtype=str, usecols=["id", "category"], keep_default_na=False)
    nodes["category"] = nodes["category"].str.split("|").str[0]
    nodes["layer"] = layer
    cols = ["id", "subject", "predicate", "object", "knowledge_level"]
    edges = pd.read_csv(edges_p, sep="\t", dtype=str, usecols=lambda c: c in cols, keep_default_na=False)
    if "knowledge_level" not in edges:
        edges["knowledge_level"] = "not_provided"
    if layer == TRIAL_LAYER:
        nodes, edges, _ = rename_ids(nodes, edges, trial_id_map(root))
    vp = d / "edge_verdicts.tsv.gz"
    verdicts = pd.read_csv(vp, sep="\t", dtype=str, usecols=["id", "verdict"]) if vp.exists() else None
    return nodes, edges.assign(layer=layer), verdicts


def drop_pruned(edges, verdicts):
    """Edges minus those whose verdict is 'prune' (matched on edge id). Returns a new DataFrame."""
    pruned = set(verdicts.loc[verdicts["verdict"] == "prune", "id"])
    return edges[~edges["id"].isin(pruned)]


def combine(parts, report=None):
    """parts: list of (nodes, edges). Returns (nodes, edges): nodes unique by id, with ONE class per id prefix
    (harmonise_categories; otherwise the first layer's class wins), edges unique by id, each edge with
    subject_category / object_category from the node table; edges whose endpoints have no node row are dropped
    (counted by the caller). report: a dict that receives 'retyped' = number of nodes whose class was changed."""
    nodes = pd.concat([n for n, _ in parts], ignore_index=True)
    if "layer" in nodes:
        nodes, n_retyped = harmonise_categories(nodes)
        nodes = nodes.drop(columns="layer")
    else:
        nodes, n_retyped = harmonise_categories(nodes)
    if report is not None:
        report["retyped"] = n_retyped
    nodes = nodes.drop_duplicates("id", keep="first")
    edges = pd.concat([e for _, e in parts], ignore_index=True).drop_duplicates("id", keep="first")
    cat = dict(zip(nodes["id"], nodes["category"]))
    edges = edges.assign(subject_category=edges["subject"].map(cat), object_category=edges["object"].map(cat))
    edges = edges[edges["subject_category"].notna() & edges["object_category"].notna()]
    return nodes.sort_values("id", ignore_index=True), edges.sort_values("id", ignore_index=True)


def assemble(name, root=ROOT, allow_missing=False):
    """Takes a graph name (G0-G4). Returns (nodes, edges, report dict) or (None, None, report) when layers are
    missing and allow_missing is False. report: layers used / missing / not pruned, edges per layer, pruned count."""
    recipe = RECIPES[name]
    rep = {"graph": name, "used": [], "missing": [], "not_pruned": [], "edges_by_layer": {}, "pruned": 0}
    parts = []
    for layer in recipe["layers"]:
        got = read_layer(layer, root)
        if got is None:
            rep["missing"].append(layer)
            continue
        nodes, edges, verdicts = got
        if recipe["prune"]:
            if verdicts is None:
                rep["not_pruned"].append(layer)
            else:
                kept = drop_pruned(edges, verdicts)
                rep["pruned"] += len(edges) - len(kept)
                edges = kept
        rep["used"].append(layer)
        parts.append((nodes, edges))
    if not parts or (rep["missing"] and not allow_missing):
        return None, None, rep
    nodes, edges = combine(parts, rep)
    rep["n_nodes"], rep["n_edges"] = len(nodes), len(edges)
    rep["dropped_no_node"] = sum(len(e) for _, e in parts) - len(edges)   # duplicates across layers included
    rep["edges_by_layer"] = edges["layer"].value_counts().sort_index().to_dict()
    return nodes, edges, rep


def summary_markdown(rep, hd_summary=""):
    """Report dict (+ optional PyG summary text) -> markdown, computed from the counts."""
    lines = [f"# {rep['graph']} ({' + '.join(rep['used']) or 'no layers'})", ""]
    if "n_nodes" in rep:
        lines += [f"{rep['n_nodes']:,} nodes, {rep['n_edges']:,} edges; {rep['pruned']:,} edges removed by pruning; "
                  f"{rep['dropped_no_node']:,} edge rows not used (duplicate id across layers or endpoint without "
                  f"a node row); {rep.get('retyped', 0):,} node rows re-typed to one class per id prefix.", "",
                  "| layer | edges |", "|---|---|"]
        lines += [f"| {k} | {v:,} |" for k, v in rep["edges_by_layer"].items()]
    lines += ["", f"Layers missing (not built yet): {', '.join(rep['missing']) or 'none'}",
              f"Layers kept whole (no verdict file): {', '.join(rep['not_pruned']) or 'none'}"]
    if hd_summary:
        lines += ["", hd_summary]
    return "\n".join(lines) + "\n"


def make(name, root=ROOT, allow_missing=False):
    """Build one graph and write outputs/<name>/heterodata.pt + summary.md. Returns the report dict
    (report['written'] tells whether the .pt file was written)."""
    from kgbuilder.pyg_export import to_heterodata, save_pyg, summary
    nodes, edges, rep = assemble(name, root, allow_missing)
    out = root / "outputs" / name.lower()
    out.mkdir(parents=True, exist_ok=True)
    rep["written"] = nodes is not None
    hd_text = ""
    if nodes is not None:
        hd = to_heterodata(engine.Build({"name": name}, nodes, edges, pd.DataFrame()))
        save_pyg(hd, out / "heterodata.pt")
        hd_text = summary(hd)
    (out / "summary.md").write_text(summary_markdown(rep, hd_text))
    return rep


def main(argv):
    allow = "--allow-missing" in argv
    names = next((a for a in argv if not a.startswith("--")), ",".join(RECIPES)).split(",")
    for name in names:
        rep = make(name, allow_missing=allow)
        state = "written" if rep["written"] else "NOT written (missing: " + ", ".join(rep["missing"]) + ")"
        print(f"{name}: {state}; layers {rep['used']}; outputs/{name.lower()}/summary.md")


if __name__ == "__main__":
    main(sys.argv[1:])
