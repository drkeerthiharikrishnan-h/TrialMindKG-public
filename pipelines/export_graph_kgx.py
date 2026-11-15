"""
One KGX TSV pair (nodes + edges) for an ASSEMBLED graph (G0-G4, make_graphs.RECIPES), with the pruning verdict on
every edge: the file the organisers asked for ("the pruned graph and every edge with keep / prune and a confidence").

    python pipelines/export_graph_kgx.py G3 [--include-pruned] [--allow-missing] [--out DIR]

Reads:   the layers' data/kg/<layer>/{nodes,edges,edge_verdicts}.tsv.gz (outputs/kgx/*.tsv for the trial layer),
         exactly as make_graphs.py does, and applies the same id renames and one-class-per-id-prefix rule.
Writes:  <out>/nodes.tsv.gz, <out>/edges.tsv.gz (KGX TSV, columns in the kgbuilder order, byte-deterministic) and
         <out>/kgx_report.md (row counts and validation). Default <out> = data/kg/assembled/<graph>/ (git-ignored:
         tens of millions of rows for Pharos).

Edge columns added to the kgbuilder KGX columns: verdict (keep | prune), confidence (0-1, the pruner's belief that
the edge is a real statement), reasons. A layer without an edge_verdicts file keeps its edges with empty verdict
columns and is listed in the report ("kept whole"): nothing is labelled keep that the pruner never looked at.
By default pruned edges are dropped (the pruned graph); --include-pruned keeps them, each marked verdict=prune.

validate_kgx() is the gate: required columns, unique node and edge ids, every edge endpoint is a node, node classes
and biolink: predicates exist in the BioLink model (other predicates must be tmkg:), knowledge_level / agent_type /
verdict values are legal, confidence is a number in [0, 1], every edge names its primary knowledge source. It is THIS
PROJECT's validator, not the kgx package: the report says "passes the project validator", never "valid KGX".

EXPORT FIXES (the block "export fixes" below; shared with pharos_index.py and neptune_export.py). Four review findings
are corrected when a product is written, never in the layer files, so the training graphs, the frozen benchmark and the
verdict file keep the labels they were built with:
  - every Gene node gets a Sequence Ontology `type` from its Open Targets biotype (protein_coding -> SO:0001217 ...);
  - knowledge_level / agent_type of the score-based protein-protein rows are corrected (edge labels only);
  - compounds are named from the ChEMBL name table, list-like text cells (definition) are written as plain text;
  - mistyped classes and malformed identifiers are corrected (Orphanet: -> ORDO:, UniProtKB:ENSG... -> a gene id ...).
The rules are DATA: the `export_overrides` block of each source card (sources/*.yaml). Every SO term must be listed in
data/reference/so_gene_terms.tsv and every infores id a rule writes in data/reference/infores_checked.tsv (both taken
from the ontology / registry files; check_overrides() stops the export otherwise). What each rule changed is counted
and written to outputs/export_overrides/<graph>.json, which the schema card reads.

    python pipelines/export_graph_kgx.py --sample [--kgx DIR] [--sample-out DIR] [--seeds ID,ID]

writes a small sample of an exported graph (default: EGFR and its capped two-hop neighbourhood) as KGX + PyG tables
with a README and SHA256SUMS: the first thing a visitor can open without downloading the whole graph.
"""
import ast
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_graphs as mg  # noqa: E402
import pharos_app  # noqa: E402
import subgraph_export  # noqa: E402
from kgbuilder import engine  # noqa: E402
from kgbuilder.biolink import BioLink  # noqa: E402

ROOT = mg.ROOT
VERDICT_COLUMNS = ["verdict", "confidence", "reasons"]
VERDICTS = {"keep", "prune"}
# BioLink KnowledgeLevelEnum (KL/AT standard), the same set as kg_schema.KNOWLEDGE_LEVELS. 7 Nov: text_co_occurrence was
# missing here, so the G3 export stopped on the Pharos text-mining edges although BioLink lists the value.
KNOWLEDGE_LEVELS = {"knowledge_assertion", "logical_entailment", "prediction", "statistical_association",
                    "text_co_occurrence", "observation", "not_provided"}
AGENT_TYPES = {"manual_agent", "automated_agent", "data_analysis_pipeline", "computational_model",
               "text_mining_agent", "image_processing_agent", "manual_validation_of_automated_agent", "not_provided"}
NODE_COLS = engine.KGX_NODE_COLS
EDGE_COLS = engine.KGX_EDGE_COLS + VERDICT_COLUMNS


# =====================================================================================================================
# EXPORT FIXES (review findings on gene types, edge labels, names / definitions, classes and identifiers). One block,
# used by this file, pharos_index.py and neptune_export.py. Rules live in the source cards (`export_overrides`); the
# layer files, the training graphs and the verdict file are never changed.
#
#   load_overrides(root, layers)        -> the rules of the layers' cards, one dict
#   check_overrides(ov, root, bl)       -> [problems]: an SO term / infores id / class / label value that is not in its
#                                          reference file (the export stops; nothing is invented)
#   id_renames(ids, ov, root)           -> ({old id: new id}, {new id: class})      identifier corrections
#   rename_node_ids(nodes, renames, classes) -> node table with the corrected ids (before the layers are merged)
#   fix_nodes(nodes, ov, chembl)        -> (node table, counts): classes, gene types, text cells, compound names
#   fix_edges(edges, layer, ov, renames, counts) -> edge table: corrected endpoints and labels (counts filled in place)
#   rule_columns(ov, layer)             -> the edge columns a layer's label rules read
#   prefix_report(nodes, bl)            -> {class: {prefix: nodes}} for prefixes BioLink does not list for the class
#   overrides_report(...)               -> the JSON-ready summary;  write_report(root, name, report) -> its path
# =====================================================================================================================
OVERRIDES_KEY = "export_overrides"
UNCOVERED = "no rule and no listed exception"   # count key of fix_edges for rows no rule and no kept_as_is `when` names
SO_TERMS = Path("data") / "reference" / "so_gene_terms.tsv"          # SO gene / pseudogene terms (from so.obo)
INFORES_CHECKED = Path("data") / "reference" / "infores_checked.tsv"   # infores ids looked up in the registry
REPORT_DIR = Path("outputs") / "export_overrides"
GENE = "biolink:Gene"
TEXT_LIST_COLUMNS = ("definition",)
EDGE_LABEL_VALUES = {"knowledge_level": KNOWLEDGE_LEVELS, "agent_type": AGENT_TYPES}
EXAMPLES = 100                    # at most this many retyped / renamed nodes are listed by id in a report
RENAMED = "ends rewritten to a corrected identifier (rows read)"
NO_BIOTYPE, NO_TERM = "no biotype in any layer", "biotype without a sequence-ontology gene term"


def _reference(root, rel):
    """A committed reference table: under the given root when it is there, else the repository's own copy."""
    p = Path(root) / rel
    return p if p.exists() else ROOT / rel


def so_terms(root=ROOT):
    """Takes the repo root. Returns {SO id: name} from data/reference/so_gene_terms.tsv ({} when the file is absent)."""
    p = _reference(root, SO_TERMS)
    if not p.exists():
        return {}
    df = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False, comment="#")
    return dict(zip(df["so_id"], df["so_name"]))


def infores_status(root=ROOT):
    """Takes the repo root. Returns {infores id: registry status ('released', 'not in registry' ...)} from
    data/reference/infores_checked.tsv ({} when the file is absent)."""
    p = _reference(root, INFORES_CHECKED)
    if not p.exists():
        return {}
    df = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False, comment="#")
    return dict(zip(df["infores_id"], df["registry_status"]))


def load_overrides(root=ROOT, layers=None):
    """Takes the repo root and layer names (source card stems; None = every card in sources/). Returns the rules of
    those cards' `export_overrides` blocks as one dict: {layers: [cards that have rules], rename_ids: [{layer, name,
    match, to, via_hgnc, category, why}], retype_nodes: [{layer, name, prefixes, from, to, why}], gene_type: {layer,
    property, unknown, map {biotype: SO id}} or None, relabel_edges: {layer: [{name, when {column: value or [values]},
    set {column: value}, why}]}, kept_as_is: [{layer, what, why}], problems: [...]}. A layer without a card (the trial
    layer) or without the block contributes nothing, so a graph without rules is exported exactly as before."""
    import yaml
    root = Path(root)
    if layers is None:
        layers = sorted(p.stem for p in (root / "sources").glob("*.yaml"))
    ov = dict(layers=[], rename_ids=[], retype_nodes=[], gene_type=None, relabel_edges={}, kept_as_is=[], problems=[])
    for layer in layers:
        card = root / "sources" / f"{layer}.yaml"
        if not card.exists():
            continue
        block = (yaml.safe_load(card.read_text(encoding="utf-8")) or {}).get(OVERRIDES_KEY) or {}
        if not block:
            continue
        ov["layers"].append(layer)
        for key in ("rename_ids", "retype_nodes", "kept_as_is"):
            ov[key] += [dict(r, layer=layer) for r in block.get(key) or []]
        if block.get("relabel_edges"):
            ov["relabel_edges"][layer] = [dict(r) for r in block["relabel_edges"]]
        if block.get("gene_type"):
            if ov["gene_type"] is not None:
                ov["problems"].append(f"gene_type is set by two cards ({ov['gene_type']['layer']} and {layer}); the first is used")
            else:
                ov["gene_type"] = dict(block["gene_type"], layer=layer)
    return ov


def check_overrides(ov, root=ROOT, bl=None):
    """Takes the loaded rules, the repo root and optionally the BioLink model. Returns a list of problems (empty = every
    identifier a rule writes was found in its reference file): an SO term that is not in so_gene_terms.tsv, an infores
    id that is not 'released' in infores_checked.tsv, a knowledge_level / agent_type that BioLink does not have, a class
    that is not a BioLink class, a rule without a name or with an unknown column to set, a bad rename pattern."""
    problems = list(ov.get("problems") or [])
    gt = ov.get("gene_type")
    if gt:
        known = so_terms(root)
        for label, term in [("unknown", gt.get("unknown"))] + sorted((gt.get("map") or {}).items()):
            if term not in known:
                problems.append(f"gene_type ({gt['layer']}): {term!r} for {label!r} is not in {SO_TERMS.name}")
        if not gt.get("property"):
            problems.append(f"gene_type ({gt['layer']}): no `property` (the node column that holds the biotype)")
    for k in ov.get("kept_as_is") or []:
        if "when" in k and (not isinstance(k["when"], dict) or not k["when"]):
            problems.append(f"kept_as_is ({k['layer']}) {k.get('what', '')[:40]!r}: `when` must be {{column: value or [values]}}")
    status = None
    for layer, rules in sorted(ov.get("relabel_edges", {}).items()):
        for r in rules:
            name = r.get("name") or "(no name)"
            if not r.get("name") or not r.get("when") or not r.get("set"):
                problems.append(f"relabel_edges ({layer}) {name}: a rule needs name, when and set")
                continue
            for col, val in r["set"].items():
                if col in EDGE_LABEL_VALUES:
                    if val not in EDGE_LABEL_VALUES[col]:
                        problems.append(f"relabel_edges ({layer}) {name}: {col} {val!r} is not a BioLink value")
                elif col == "primary_knowledge_source":
                    status = infores_status(root) if status is None else status
                    if status.get(val) != "released":
                        problems.append(f"relabel_edges ({layer}) {name}: {val!r} is not a released entry of the infores "
                                        f"registry ({INFORES_CHECKED.name})")
                else:
                    problems.append(f"relabel_edges ({layer}) {name}: column {col!r} may not be set at export")
    for r in ov.get("retype_nodes") or []:
        if not (r.get("name") and r.get("prefixes") and r.get("from") and r.get("to")):
            problems.append(f"retype_nodes ({r['layer']}): a rule needs name, prefixes, from and to")
        elif bl is not None and not bl.has_class(r["to"]):
            problems.append(f"retype_nodes ({r['layer']}) {r['name']}: {r['to']!r} is not a BioLink class")
    for r in ov.get("rename_ids") or []:
        if not (r.get("name") and r.get("match") and r.get("to")):
            problems.append(f"rename_ids ({r['layer']}): a rule needs name, match and to")
            continue
        try:
            re.compile(r["match"])
        except re.error as err:
            problems.append(f"rename_ids ({r['layer']}) {r['name']}: bad pattern ({err})")
        if r.get("category") and bl is not None and not bl.has_class(r["category"]):
            problems.append(f"rename_ids ({r['layer']}) {r['name']}: {r['category']!r} is not a BioLink class")
    return problems


def id_renames(ids, ov, root=ROOT):
    """Takes node ids (any iterable of strings), the rules and the repo root. Returns (renames {old id: new id}, classes
    {new id: the class its rule gives}, counts {rule name: ids renamed}). A rule rewrites the ids its pattern matches
    (`to` may use the pattern's groups); with via_hgnc the result is looked up in the HGNC table (ENSEMBL gene -> NCBIGene,
    the same table the Open Targets and trial layers use) and keeps the rewritten id when HGNC has no entry."""
    rules = ov.get("rename_ids") or []
    renames, classes, counts = {}, {}, {}
    if not rules:
        return renames, classes, counts
    s = pd.Series(pd.unique(pd.Series(list(ids), dtype=object)), dtype=object)
    hgnc = None
    for r in rules:
        pat = re.compile(r["match"])
        n = 0
        for old in s[s.map(lambda v: isinstance(v, str) and pat.search(v) is not None)]:
            new = pat.sub(r["to"], old)
            if r.get("via_hgnc"):
                hgnc = mg.trial_id_map(root) if hgnc is None else hgnc
                new = hgnc.get(new, new)
            if new != old and old not in renames:
                renames[old] = new
                n += 1
                if r.get("category"):
                    classes[new] = r["category"]
        counts[r["name"]] = n
    return renames, classes, counts


def rename_node_ids(nodes, renames, classes=None):
    """Takes one layer's node table, id_renames' maps. Returns the table with the corrected ids (and the class a rename
    rule gives, on the renamed rows only); the same object when nothing matches. Rows that now share an id are merged
    later, per field, like any node two layers hold."""
    if not renames:
        return nodes
    hit = nodes["id"].isin(renames)
    if not hit.any():
        return nodes
    out = nodes.copy()
    out.loc[hit, "id"] = nodes.loc[hit, "id"].map(renames)
    if classes:
        cls = out.loc[hit, "id"].map(classes)
        out.loc[cls[cls.notna()].index, "category"] = cls[cls.notna()]
    return out


def clean_text_list(value):
    """Takes one cell. A list written as text ('["a", "a"]' or "['a', 'b']") becomes its distinct items, in order,
    joined by '|' (the KGX separator): 'a' / 'a|b'. Anything else, and a list this function cannot read safely, is
    returned unchanged."""
    v = str(value).strip()
    if len(v) < 2 or v[0] != "[" or v[-1] != "]":
        return value
    try:
        items = json.loads(v)
    except ValueError:
        if re.search(r"""['"]\s+['"]""", v):          # numpy style without commas: literal_eval would glue the items
            return value
        try:
            items = ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return value
    if not isinstance(items, (list, tuple)) or any(isinstance(x, (list, tuple, dict)) for x in items):
        return value
    seen, out = set(), []
    for x in items:
        t = "" if x is None else " ".join(str(x).split())
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return "|".join(out)


def fix_nodes(nodes, ov, chembl=None):
    """Takes the MERGED node table (one row per id, strings, '' = empty), the rules and optionally
    pharos_app.chembl_names(root). Returns (nodes, counts). In order:
      1. retype_nodes: ids with the rule's prefixes and class get the rule's class;
      2. gene_type: every biolink:Gene row gets `type` = the SO term of its biotype; a biotype without an SO gene term
         gives the rule's `unknown` term (the biotype is the authority, so a card's type does not survive it); without
         any biotype the type a source card already gave is kept, else `unknown`. Skipped when
         the table has neither the biotype column nor `type` (the Neptune node files carry neither);
      3. list-like text cells of TEXT_LIST_COLUMNS are written as plain text (clean_text_list);
      4. with `chembl`: a compound without a name gets its ChEMBL name (pharos_app.name_compounds; never replaces one).
    counts: {retyped: {rule: n}, retyped_nodes: {rule: [[id, name], ...] (at most EXAMPLES)}, genes, gene_types: {SO id: n}, gene_type_basis: {basis: n}, gene_type_changed:
    {'old -> new': n}, biotypes_without_term: {biotype: n}, text_cells_cleaned: {column: n}, named_from_chembl,
    unnamed: {class: n}}."""
    nodes = nodes.copy()
    counts = dict(retyped={}, retyped_nodes={}, text_cells_cleaned={})
    prefix = nodes["id"].str.extract(r"^([^:]+):", expand=False)
    for r in ov.get("retype_nodes") or []:
        m = prefix.isin(r["prefixes"]) & (nodes["category"] == r["from"])
        nodes.loc[m, "category"] = r["to"]
        counts["retyped"][r["name"]] = int(m.sum())
        if m.any():                                            # the nodes themselves, so a reviewer can read every one
            names = nodes.loc[m, "name"] if "name" in nodes else pd.Series("", index=nodes.index[m])
            counts["retyped_nodes"][r["name"]] = [list(x) for x in sorted(zip(nodes.loc[m, "id"], names.fillna("")))[:EXAMPLES]]
    gt = ov.get("gene_type")
    if gt and (gt["property"] in nodes or "type" in nodes):
        g = nodes["category"] == GENE
        empty = pd.Series("", index=nodes.index, dtype=object)
        bio = nodes[gt["property"]].fillna("") if gt["property"] in nodes else empty
        old = nodes["type"].fillna("") if "type" in nodes else empty
        mapped = bio.map(gt.get("map") or {})
        new = mapped.where(mapped.notna(), old.where((old != "") & (bio == ""), gt["unknown"]))
        if "type" not in nodes:
            nodes["type"] = ""
        nodes.loc[g, "type"] = new[g]
        basis = np.select([mapped.notna(), bio != "", old != ""], ["biotype", NO_TERM, "type given by a source card"],
                          default=NO_BIOTYPE)
        counts["genes"] = int(g.sum())
        counts["gene_types"] = {k: int(n) for k, n in new[g].value_counts().sort_index().items()}
        counts["gene_type_basis"] = {k: int(n) for k, n in pd.Series(basis[g.to_numpy()]).value_counts().sort_index().items()}
        ch = g & (old != "") & (new != old)
        counts["gene_type_changed"] = {k: int(n) for k, n in (old[ch] + " -> " + new[ch]).value_counts().sort_index().items()}
        nt = g & (bio != "") & mapped.isna()
        counts["biotypes_without_term"] = {k: int(n) for k, n in bio[nt].value_counts().sort_index().items()}
    for col in TEXT_LIST_COLUMNS:
        if col in nodes:
            listy = nodes[col].fillna("").str.startswith("[")
            if listy.any():
                cleaned = nodes.loc[listy, col].map(clean_text_list)
                counts["text_cells_cleaned"][col] = int((cleaned != nodes.loc[listy, col]).sum())
                nodes.loc[listy, col] = cleaned
    if chembl is not None and "name" in nodes:
        names = pd.Series(chembl["names"], dtype=object)
        m = (nodes["name"].fillna("") == "") & nodes["id"].isin(names.index)
        nodes.loc[m, "name"] = nodes.loc[m, "id"].map(names)
        counts["named_from_chembl"] = int(m.sum())
    if "name" in nodes:
        counts["unnamed"] = {k: int(n) for k, n in nodes.loc[nodes["name"].fillna("") == "", "category"]
                             .value_counts().sort_index().items()}
    return nodes, counts


def rule_columns(ov, layer):
    """Takes the rules and a layer. Returns the set of edge columns that layer's label rules read (their `when` keys)."""
    return {col for r in ov.get("relabel_edges", {}).get(layer, []) for col in r["when"]}


def fix_edges(edges, layer, ov, renames=None, counts=None, labels=True):
    """Takes one layer's edge table (or a chunk of it), the layer name, the rules, id_renames' map and a dict to count
    into. CHANGES THE TABLE IN PLACE and returns it (no copy: the Pharos layer has 26 million rows): (1) subject / object
    are rewritten where a node id was corrected and (2) each relabel rule of the layer is applied: rows where every `when`
    column has the given value (or one of a list) get the `set` values, for the `set` columns the table has. A rule whose
    `when` column is not in the table is not applied and counted under 'not applied: <rule>'. counts (filled in place):
    {'endpoints renamed': n, rule name: rows}. Edge ids never change; a table without matching rows is left untouched.
    labels=False does step (1) only: the exporters rename the ends first (the node check needs them) and apply the label
    rules AFTER duplicates and pruned edges are dropped, so each rule's count is the number of rows WRITTEN."""
    if renames:
        for end in ("subject", "object"):
            hit = edges[end].isin(renames)
            if hit.any():
                edges.loc[hit, end] = edges.loc[hit, end].map(renames)
                if counts is not None:
                    counts[RENAMED] = counts.get(RENAMED, 0) + int(hit.sum())
    for r in ov.get("relabel_edges", {}).get(layer, []) if labels else []:
        if any(col not in edges for col in r["when"]):
            if counts is not None:
                counts[f"not applied: {r['name']}"] = counts.get(f"not applied: {r['name']}", 0) + len(edges)
            continue
        m = None
        for col, val in r["when"].items():
            hit = edges[col].isin(val if isinstance(val, list) else [val])
            m = hit if m is None else m & hit
        if counts is not None:
            counts[r["name"]] = counts.get(r["name"], 0) + int(m.sum())
        if m.any():
            for col, val in r["set"].items():
                if col in edges:
                    edges.loc[m, col] = val
    if labels and counts is not None:
        for key, n in uncovered_rows(edges, layer, ov).items():
            counts[key] = counts.get(key, 0) + n
    return edges


def _match(edges, when):
    """Takes an edge table and a `when` dict {column: value or [values]}. Returns the boolean mask of the rows where every
    column has one of the values, or None when a column is not in the table."""
    m = None
    for col, val in when.items():
        if col not in edges:
            return None
        hit = edges[col].isin(val if isinstance(val, list) else [val])
        m = hit if m is None else m & hit
    return m


def uncovered_rows(edges, layer, ov):
    """Takes one layer's edge table (labels already applied), the layer name and the rules. Returns {UNCOVERED + ': '
    + the row's values of the rule columns: rows} for the rows of a predicate that the layer's relabel rules name which
    match NO relabel rule and NO `kept_as_is` entry that has a `when`. Empty when every such row is covered, when the
    layer has no rules or when the table lacks the rule columns. A new source cell (finding H of the S03 review: the
    'BioPlex,Reactome,STRINGDB' rows were named nowhere) is therefore counted instead of passing unseen."""
    rules = ov.get("relabel_edges", {}).get(layer, [])
    preds = {v for r in rules for v in ([r["when"]["predicate"]] if isinstance(r["when"].get("predicate"), str)
                                       else r["when"].get("predicate") or [])}
    if not preds or "predicate" not in edges:
        return {}
    scope = edges["predicate"].isin(preds)
    covered = pd.Series(False, index=edges.index)
    for when in [r["when"] for r in rules] + [k["when"] for k in ov.get("kept_as_is") or []
                                               if k.get("layer") == layer and k.get("when")]:
        m = _match(edges, when)
        if m is not None:
            covered |= m
    left = edges[scope & ~covered]
    if left.empty:
        return {}
    cols = sorted(c for c in rule_columns(ov, layer) if c != "predicate" and c in left)
    key = left[cols].astype(str).agg(lambda row: ", ".join(f"{c}={v}" for c, v in zip(cols, row)), axis=1) if cols \
        else pd.Series("predicate only", index=left.index)
    return {f"{UNCOVERED}: {k}": int(n) for k, n in key.value_counts().sort_index().items()}


def prefix_report(nodes, bl):
    """Takes the node table and the BioLink model. Returns {class: {prefix: nodes}} for the identifier prefixes that
    BioLink's own id_prefixes list of that class does not hold (a class for which BioLink lists no prefixes reports all
    of its prefixes)."""
    prefix = nodes["id"].str.extract(r"^([^:]+):", expand=False).fillna("")
    out = {}
    for (cat, pre), n in nodes.groupby([nodes["category"], prefix]).size().items():
        if not cat.startswith("biolink:") or not bl.has_class(cat) or pre in set(bl.prefixes(cat)):
            continue
        out.setdefault(cat, {})[pre] = int(n)
    return {c: dict(sorted(v.items())) for c, v in sorted(out.items())}


def overrides_report(product, ov, node_counts=None, edge_counts=None, rename_counts=None, prefixes=None, totals=None,
                     renames=None):
    """Takes the product name ('kgx G3', 'card index' ...), the rules, the counts the fix functions returned and
    id_renames' map. Returns a JSON-ready dict: what was corrected in this product and how many rows each rule touched,
    with the first EXAMPLES renamed ids (no dates, no paths)."""
    rep = dict(product=product, cards_with_rules=list(ov.get("layers") or []),
               nodes=dict(node_counts or {}), ids_renamed=dict(rename_counts or {}),
               ids_renamed_examples=[list(x) for x in sorted((renames or {}).items())[:EXAMPLES]],
               edges={k: dict(sorted(v.items())) for k, v in sorted((edge_counts or {}).items())},
               kept_as_is=[dict(layer=k["layer"], what=k["what"], why=k["why"]) for k in ov.get("kept_as_is") or []])
    if prefixes is not None:
        rep["prefixes_not_listed_by_biolink"] = prefixes
    if totals:
        rep["totals"] = totals
    return rep


def write_report(root, name, report):
    """Takes the repo root, a file stem ('g3') and overrides_report's dict. Writes outputs/export_overrides/<name>.json
    (sorted keys, so equal counts give equal bytes). Returns the path."""
    p = Path(root) / REPORT_DIR / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return p


def fixes_markdown(report):
    """Takes overrides_report's dict. Returns the lines of the 'Corrections made at export' section of a report: one
    line per rule with its count, in plain words (no paths, no internal codes)."""
    n, lines = report.get("nodes") or {}, []
    if n.get("gene_types"):
        lines.append(f"- Gene nodes: {n['genes']:,}, each with a Sequence Ontology type: "
                     + ", ".join(f"{k} {v:,}" for k, v in sorted(n["gene_types"].items(), key=lambda kv: (-kv[1], kv[0])))
                     + ". Set from: " + ", ".join(f"{k} {v:,}" for k, v in n.get("gene_type_basis", {}).items()) + ".")
        if n.get("gene_type_changed"):
            lines.append("- Gene types replaced because the biotype says otherwise: "
                         + ", ".join(f"{k} {v:,}" for k, v in n["gene_type_changed"].items()) + ".")
        if n.get("biotypes_without_term"):
            lines.append("- Biotypes without a Sequence Ontology gene term: "
                         + ", ".join(f"{k} {v:,}" for k, v in n["biotypes_without_term"].items()) + ".")
    for rule, k in (n.get("retyped") or {}).items():
        lines.append(f"- Node class corrected ({rule}): {k:,} nodes.")
    for rule, k in (report.get("ids_renamed") or {}).items():
        lines.append(f"- Identifiers corrected ({rule}): {k:,} nodes.")
    for layer, rules in (report.get("edges") or {}).items():
        for rule, k in rules.items():
            lines.append(f"- Edge rows of {layer}, {rule}: {k:,}.")
    for col, k in (n.get("text_cells_cleaned") or {}).items():
        lines.append(f"- Text cells written as plain text ({col}): {k:,}.")
    if "named_from_chembl" in n:
        lines.append(f"- Compounds named from the ChEMBL name table: {n['named_from_chembl']:,}.")
    if n.get("unnamed"):
        lines.append("- Nodes still without a name: " + ", ".join(f"{k.split(':')[-1]} {v:,}" for k, v in n["unnamed"].items()) + ".")
    for cat, pre in (report.get("prefixes_not_listed_by_biolink") or {}).items():
        lines.append(f"- Identifier prefixes BioLink does not list for {cat.split(':')[-1]}: "
                     + ", ".join(f"{k} {v:,}" for k, v in pre.items()) + ".")
    return lines


def read_layer_full(layer, root=ROOT):
    """Takes a layer name. Returns (nodes, edges, verdicts or None) with ALL columns the layer wrote (strings), the
    trial layer's ids renamed as make_graphs.read_layer does; None when the layer is not built."""
    d = mg.layer_dir(layer, root)
    if d is None:
        return None
    ext = ".tsv" if layer == mg.TRIAL_LAYER else ".tsv.gz"
    nodes_p, edges_p = d / f"nodes{ext}", d / f"edges{ext}"
    if not (nodes_p.exists() and edges_p.exists()):
        return None
    nodes = pd.read_csv(nodes_p, sep="\t", dtype=str, keep_default_na=False)
    nodes["category"] = nodes["category"].str.split("|").str[0]
    nodes["layer"] = layer
    edges = pd.read_csv(edges_p, sep="\t", dtype=str, keep_default_na=False)
    if layer == mg.TRIAL_LAYER:
        nodes, edges, _ = mg.rename_ids(nodes, edges, mg.trial_id_map(root))
    vp = d / "edge_verdicts.tsv.gz"
    verdicts = None
    if vp.exists():
        verdicts = pd.read_csv(vp, sep="\t", dtype=str, keep_default_na=False, usecols=["id"] + VERDICT_COLUMNS)
    return nodes, edges, verdicts


def attach_verdicts(edges, verdicts):
    """Takes a layer's edges and its verdict table (or None). Returns the edges with verdict / confidence / reasons
    (empty strings for an edge without a verdict, or for every edge when there is no table)."""
    if verdicts is None:
        return edges.assign(**{c: "" for c in VERDICT_COLUMNS})
    v = verdicts.drop_duplicates("id").set_index("id")
    out = edges.copy()
    for c in VERDICT_COLUMNS:
        out[c] = out["id"].map(v[c]).fillna("")
    return out


def assemble_kgx(name, root=ROOT, include_pruned=False, allow_missing=False):
    """Takes a graph name (G0-G4). Returns (nodes, edges, report) or (None, None, report) when layers are missing
    and allow_missing is False. Nodes: one row per id, each FIELD the first non-empty value over the layers
    (pharos_app.merge_node_frames, as the card index and the Neptune files; so a gene Pharos lists keeps the biotype Open
    Targets gives it), one class per id prefix (make_graphs.harmonise_categories), then the export fixes (fix_nodes);
    edges: unique by id, endpoints all present as nodes, verdict columns attached, labels corrected (fix_edges), pruned
    edges dropped unless include_pruned. report: layers used / missing / kept whole, pruned edge count, edges dropped for
    a missing endpoint, `overrides` (overrides_report) and `override_problems` (check_overrides)."""
    recipe = mg.RECIPES[name]
    rep = {"graph": name, "used": [], "missing": [], "not_pruned": [], "pruned": 0, "include_pruned": include_pruned}
    read = []
    for layer in recipe["layers"]:
        got = read_layer_full(layer, root)
        if got is None:
            rep["missing"].append(layer)
            continue
        if recipe["prune"] and got[2] is None:
            rep["not_pruned"].append(layer)
        rep["used"].append(layer)
        read.append((layer,) + got)
    if not read or (rep["missing"] and not allow_missing):
        return None, None, rep
    ov = load_overrides(root, rep["used"])
    renames, classes, rename_counts = id_renames(pd.concat([n["id"] for _, n, _, _ in read]), ov, root)
    parts, edge_counts = [], {}
    for layer, nodes, edges, verdicts in read:
        edges = attach_verdicts(edges, verdicts)          # keeps the card's own `layer` column (L0 / L1 / L2 / L3)
        edges = fix_edges(edges, layer, ov, renames, edge_counts.setdefault(layer, {}), labels=False).assign(_from=layer)
        parts.append((rename_node_ids(nodes, renames, classes), edges))
    n_rows = sum(len(e) for _, e in parts)
    _, edges = mg.combine(parts, rep)
    allf, _ = mg.harmonise_categories(pd.concat([n for n, _ in parts], ignore_index=True).fillna(""))
    nodes = pharos_app.merge_node_frames([allf.drop(columns="layer")])
    nodes, node_counts = fix_nodes(nodes, ov, chembl=pharos_app.chembl_names(root))
    nodes = nodes.sort_values("id", ignore_index=True)
    n_before = len(edges)
    edges = edges.drop(columns=["subject_category", "object_category"])
    pruned = edges["verdict"] == "prune"
    rep["pruned"] = int(pruned.sum())
    if recipe["prune"] and not include_pruned:
        edges = edges[~pruned]
    edges = edges.reset_index(drop=True)
    for layer in rep["used"]:                          # label rules on the rows that are written: the counts are rows written
        mine = edges["_from"] == layer
        if mine.any() and ov["relabel_edges"].get(layer):
            part = fix_edges(edges[mine].copy(), layer, ov, None, edge_counts.setdefault(layer, {}))
            for col in {c for r in ov["relabel_edges"][layer] for c in r["set"]} & set(edges.columns):
                edges.loc[mine, col] = part[col]
    edges = edges.drop(columns="_from")
    rep["n_nodes"], rep["n_edges"] = len(nodes), len(edges)
    rep["dropped_no_node"] = n_rows - n_before                           # endpoint without a node row or duplicate id
    rep["override_problems"] = check_overrides(ov, root)
    rep["overrides"] = overrides_report(f"kgx {name}", ov, node_counts, {k: v for k, v in edge_counts.items() if v},
                                        rename_counts, totals=dict(nodes=len(nodes), edges=len(edges)), renames=renames)
    return nodes, edges, rep


def validate_kgx(nodes, edges, bl=None):
    """Takes the two KGX tables. Returns a list of problems (empty = the tables pass THIS PROJECT's validator: columns,
    ids, endpoints, BioLink terms, allowed values). It does not call the kgx package, so it is not a claim of 'valid KGX'."""
    bl = bl or BioLink()
    problems = []
    for need, df, label in ((NODE_COLS, nodes, "nodes"), (EDGE_COLS[:4] + ["primary_knowledge_source", "knowledge_level",
                                                                          "agent_type"] + VERDICT_COLUMNS, edges, "edges")):
        miss = [c for c in need if c not in df]
        if miss:
            problems.append(f"{label}: missing columns {miss}")
    if problems:
        return problems
    if nodes["id"].duplicated().any():
        problems.append(f"nodes: {int(nodes['id'].duplicated().sum()):,} duplicate ids")
    if edges["id"].duplicated().any():
        problems.append(f"edges: {int(edges['id'].duplicated().sum()):,} duplicate ids")
    if (nodes["id"] == "").any() or nodes["id"].isna().any():
        problems.append("nodes: empty id")
    ids = set(nodes["id"])
    for end in ("subject", "object"):
        gone = ~edges[end].isin(ids)
        if gone.any():
            problems.append(f"edges: {int(gone.sum()):,} edges have a {end} that is not a node "
                            f"(e.g. {edges.loc[gone, end].iloc[0]})")
    for cat in sorted(set(nodes["category"].str.split("|").explode().dropna())):
        if not cat.startswith("biolink:") or not bl.has_class(cat):
            problems.append(f"nodes: category {cat!r} is not a BioLink class")
    for pred in sorted(edges["predicate"].dropna().unique()):
        if pred.startswith("biolink:"):
            if not bl.has_predicate(pred):
                problems.append(f"edges: predicate {pred!r} is not a BioLink predicate")
        elif not pred.startswith("tmkg:"):
            problems.append(f"edges: predicate {pred!r} is neither biolink: nor tmkg:")
    for col, ok in (("knowledge_level", KNOWLEDGE_LEVELS), ("agent_type", AGENT_TYPES)):
        bad = sorted(set(edges[col].dropna()) - ok - {""})
        if bad:
            problems.append(f"edges: {col} values {bad} are not BioLink values")
    if (edges["primary_knowledge_source"].fillna("") == "").any():
        problems.append(f"edges: {int((edges['primary_knowledge_source'].fillna('') == '').sum()):,} edges have no "
                        "primary_knowledge_source")
    v = edges["verdict"].fillna("")
    bad = sorted(set(v) - VERDICTS - {""})
    if bad:
        problems.append(f"edges: verdict values {bad} are not keep / prune")
    c = pd.to_numeric(edges["confidence"].replace("", pd.NA), errors="coerce")
    if (c.isna() & (edges["confidence"].fillna("") != "")).any():
        problems.append("edges: confidence is not a number on some rows")
    if ((c < 0) | (c > 1)).any():
        problems.append("edges: confidence outside [0, 1] on some rows")
    if ((v == "") != (edges["confidence"].fillna("") == "")).any():
        problems.append("edges: verdict and confidence are not both set or both empty")
    return problems


def write_kgx(nodes, edges, out_dir):
    """Takes the two tables and a folder. Writes nodes.tsv.gz / edges.tsv.gz in the kgbuilder column order (extra
    columns after, sorted), gzip without a timestamp so the same tables give the same bytes. Returns the two paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for fname, df, first in (("nodes", nodes.drop(columns=["layer"], errors="ignore"), NODE_COLS),
                             ("edges", edges, EDGE_COLS)):
        cols = [c for c in first if c in df] + sorted(c for c in df if c not in first)
        p = out_dir / f"{fname}.tsv.gz"
        with gzip.GzipFile(p, "wb", mtime=0) as gz:
            gz.write(df[cols].to_csv(sep="\t", index=False).encode())
        paths.append(p)
    return tuple(paths)


def report_markdown(rep, nodes, edges, problems, files=None):
    """Counts per class, predicate and verdict, computed from the tables, plus the validation result. Files are listed by
    name only (CC-37: the report ships in public downloads, so it must not carry the build machine's absolute paths)."""
    lines = [f"# KGX export of {rep['graph']} ({' + '.join(rep['used']) or 'no layers'})", "",
             f"{len(nodes):,} nodes, {len(edges):,} edges. Pruned edges: {rep['pruned']:,} "
             f"({'kept, marked verdict=prune' if rep['include_pruned'] else 'dropped'}). "
             f"Edge rows not used (duplicate id or endpoint without a node): {rep['dropped_no_node']:,}.", "",
             "| verdict | edges |", "|---|---|"]
    lines += [f"| {k or '(none: layer kept whole)'} | {v:,} |"
              for k, v in edges["verdict"].fillna("").value_counts().sort_index().items()]
    lines += ["", "| node class | nodes |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in nodes["category"].value_counts().items()]
    lines += ["", f"Layers missing (not built): {', '.join(rep['missing']) or 'none'}",
              f"Layers kept whole (no verdict file): {', '.join(rep['not_pruned']) or 'none'}", "", "## Validation", ""]
    lines += [f"- PROBLEM: {p}" for p in problems] or ["- passes the project validator (columns, ids, endpoints, BioLink "
                                                       "terms), plus allowed label values and verdict + confidence. "
                                                       "This is the project's own check, not the kgx package."]
    fixes = fixes_markdown(rep["overrides"]) if rep.get("overrides") else []
    if fixes:
        lines += ["", "## Corrections made at export", "",
                  "The layer files, the training graphs and the verdict file keep their original values; these corrections "
                  "are made when the tables are written.", ""] + fixes
    if files:
        lines += ["", "Files: " + ", ".join(Path(f).name for f in files)]     # names only: no machine paths in a public file
    return "\n".join(lines) + "\n"


def export_graph(name, root=ROOT, out_dir=None, include_pruned=False, allow_missing=False, bl=None):
    """Assembles, validates and writes one graph. Returns (report dict, problems, paths); paths is empty when the
    graph could not be assembled (layers missing) or a check failed (nothing is written then). Problems = validate_kgx
    plus check_overrides (an export rule that names a term missing from its reference file). On success the counts of the
    export fixes are also written to outputs/export_overrides/<graph>.json (small, committed; the schema card reads it)."""
    nodes, edges, rep = assemble_kgx(name, root, include_pruned, allow_missing)
    if nodes is None:
        return rep, [f"layers missing: {', '.join(rep['missing'])}"], ()
    bl = bl or BioLink()
    problems = validate_kgx(nodes, edges, bl) + [f"export rule: {p}" for p in rep["override_problems"]]
    problems += [f"export rule: {p}" for p in check_overrides(load_overrides(root, rep["used"]), root, bl)
                 if p not in rep["override_problems"]]
    rep["overrides"]["prefixes_not_listed_by_biolink"] = prefix_report(nodes, bl)
    out = Path(out_dir or root / "data" / "kg" / "assembled" / name.lower())
    paths = () if problems else write_kgx(nodes, edges, out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "kgx_report.md").write_text(report_markdown(rep, nodes, edges, problems, paths))
    if paths:
        write_report(root, name.lower(), rep["overrides"])
    return rep, problems, paths


# ------------------------------------------------------------------------------------------------ the small sample
def sample_seeds(root=ROOT):
    """Takes the repo root. Returns the start nodes of the sample: `export_overrides.sample_seeds` of sources/pharos.yaml
    (the EGFR gene and its protein); () when the card does not name any."""
    import yaml
    card = Path(root) / "sources" / "pharos.yaml"
    block = (yaml.safe_load(card.read_text(encoding="utf-8")) or {}).get(OVERRIDES_KEY) or {} if card.exists() else {}
    return tuple(block.get("sample_seeds") or ())


PANDAS_SNIPPET = """import pandas as pd
nodes = pd.read_csv("kgx/nodes.tsv", sep="\\t", dtype=str)
edges = pd.read_csv("kgx/edges.tsv", sep="\\t", dtype=str)
print(len(nodes), "nodes,", len(edges), "edges")
print(edges.groupby(["predicate", "primary_knowledge_source"]).size().sort_values(ascending=False).head(20))"""


def _top(frame, keys, n):
    """The first n rows per `keys` group: higher confidence first (no confidence last), then edge id."""
    f = frame.assign(_c=-pd.to_numeric(frame["confidence"], errors="coerce").fillna(-1.0))
    return f.sort_values(list(keys) + ["_c", "id"], kind="mergesort").groupby(list(keys), sort=False).head(n).drop(columns="_c")


def sample_edges(edges_path, seeds, hop1=50, hop2=3, chunk=2_000_000):
    """Takes the exported edges.tsv.gz, the seed node ids and the two caps. Streams the file twice. Returns (edges
    DataFrame sorted by id, info): hop 1 = the edges that touch a seed, at most `hop1` per (predicate, primary knowledge
    source), the most confident first; hop 2 = for every node reached, at most `hop2` further edges per predicate, the
    most confident first (edges that touch a seed are only taken at hop 1). info: {seeds, hop1, hop2, seed_edges_in_graph,
    hop1_edges, hop2_edges, neighbours}."""
    seeds = set(seeds)
    read = lambda: pd.read_csv(edges_path, sep="\t", dtype=str, keep_default_na=False, chunksize=chunk)   # noqa: E731
    first = [c[c["subject"].isin(seeds) | c["object"].isin(seeds)] for c in read()]
    first = pd.concat(first, ignore_index=True)
    one = _top(first, ["predicate", "primary_knowledge_source"], hop1)
    near = (set(one["subject"]) | set(one["object"])) - seeds
    best = None
    for c in read():
        c = c[~(c["subject"].isin(seeds) | c["object"].isin(seeds))]
        sides = [c[c[end].isin(near)].assign(_node=lambda d, end=end: d[end]) for end in ("subject", "object")]
        cand = pd.concat(([best] if best is not None else []) + sides, ignore_index=True)
        best = _top(cand, ["_node", "predicate"], hop2) if len(cand) else best
    two = (best.drop(columns="_node").drop_duplicates("id") if best is not None and len(best)
           else first.iloc[0:0])
    out = pd.concat([one, two], ignore_index=True).drop_duplicates("id").sort_values("id", ignore_index=True)
    return out, dict(seeds=sorted(seeds), hop1=hop1, hop2=hop2, seed_edges_in_graph=len(first), hop1_edges=len(one),
                     hop2_edges=len(out) - len(one), neighbours=len(near))


def sample_readme(names, info, n_nodes, n_edges, files):
    """Takes the seeds' names, sample_edges' info, the row counts and [(file, bytes)]. Returns the README text: what the
    sample is, how it was cut (the numbers are the ones just computed) and the five-line pandas snippet."""
    total = sum(b for _, b in files)
    lines = [f"# A small sample of the graph: {' / '.join(names)} and its neighbourhood", "",
             f"{n_nodes:,} nodes and {n_edges:,} edges ({total:,} bytes in {len(files)} files), cut from the full graph so that "
             "you can look at the format before downloading it. Same columns, classes, predicates, sources and verdicts as "
             "the full KGX tables.", "",
             "## How it was cut", "",
             f"- Start: {', '.join(info['seeds'])}.",
             f"- First step: the edges that touch a start node, at most {info['hop1']} per predicate and primary source, the "
             f"most confident first: {info['hop1_edges']:,} of {info['seed_edges_in_graph']:,} edges, reaching "
             f"{info['neighbours']:,} nodes.",
             f"- Second step: for each of those nodes, at most {info['hop2']} more edges per predicate, the most confident "
             f"first: {info['hop2_edges']:,} edges.",
             "- It is a sample: a node here has more edges in the full graph. Do not compute statistics on it.", "",
             "## Open it in pandas", "", "```python", PANDAS_SNIPPET, "```", "",
             "## Files", "",
             "- `kgx/nodes.tsv`, `kgx/edges.tsv`: KGX tables (BioLink classes and predicates; source, knowledge level, "
             "verdict and confidence on every edge).",
             "- `pyg/nodes_<Class>.csv`, `pyg/edges_<Subject>__<predicate>__<Object>.csv`: one index table per node class and "
             "per edge type; `pyg/load_pyg.py` builds a PyTorch Geometric HeteroData object from them.",
             "- `SHA256SUMS`: a checksum for every file.", ""]
    return "\n".join(lines)


def write_sample(kgx_dir, out_dir, seeds=None, hop1=50, hop2=3, chunk=2_000_000):
    """Takes the folder of an exported graph (nodes.tsv.gz, edges.tsv.gz), the folder to write, the seed ids and the caps.
    Writes <out>/kgx/{nodes,edges}.tsv, <out>/pyg/*.csv + load_pyg.py (subgraph_export's tables), README.md and SHA256SUMS.
    Returns {nodes, edges, bytes, files, downloads_row}: downloads_row is the entry to put FIRST in downloads.json once the
    folder is uploaded as sample/. Raises ValueError when no edge touches a seed (wrong ids or wrong folder)."""
    kgx_dir, out_dir = Path(kgx_dir), Path(out_dir)
    seeds = tuple(seeds) if seeds else sample_seeds()
    edges, info = sample_edges(kgx_dir / "edges.tsv.gz", seeds, hop1, hop2, chunk)
    if not len(edges):
        raise ValueError(f"no edge touches {', '.join(seeds)} in {kgx_dir.name}/edges.tsv.gz")
    ids = set(edges["subject"]) | set(edges["object"])
    nodes = pd.concat([c[c["id"].isin(ids)] for c in pd.read_csv(kgx_dir / "nodes.tsv.gz", sep="\t", dtype=str,
                                                                 keep_default_na=False, chunksize=chunk)], ignore_index=True)
    clean = lambda r: {k: v for k, v in r.items() if v != ""}                                    # noqa: E731
    node_recs = {r["id"]: clean(r) for r in nodes.sort_values("id").to_dict("records")}
    edge_recs = [clean(r) for r in edges.to_dict("records")]
    n_tsv, e_tsv = subgraph_export.kgx_tables(node_recs, edge_recs)
    files = {"kgx/nodes.tsv": n_tsv, "kgx/edges.tsv": e_tsv, **subgraph_export.pyg_tables(node_recs, edge_recs),
             "pyg/load_pyg.py": subgraph_export.LOAD_PYG}
    names = [node_recs.get(s, {}).get("name") or s for s in info["seeds"]]
    names = list(dict.fromkeys(names))
    sizes = [(k, len(v.encode("utf-8"))) for k, v in sorted(files.items())]
    files["README.md"] = sample_readme(names, info, len(node_recs), len(edge_recs), sizes)
    for old in list(out_dir.glob("kgx/*.tsv")) + list(out_dir.glob("pyg/*")):          # a previous sample leaves no stray table
        old.unlink()
    sums = []
    for rel, text in sorted(files.items()):
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        sums.append(f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}  {rel}")
    (out_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    total = sum(len(v.encode("utf-8")) for v in files.values()) + (out_dir / "SHA256SUMS").stat().st_size
    row = dict(title=f"A small sample to open first: {' / '.join(names)} and its neighbourhood", path="sample/",
               what=f"{len(node_recs):,} nodes and {len(edge_recs):,} edges cut from the pruned graph, as KGX tables and as "
                    "PyTorch Geometric index tables, with a README that shows how to open them in pandas in five lines. "
                    "SHA256SUMS lists a checksum for every file.", bytes=total)
    return dict(nodes=len(node_recs), edges=len(edge_recs), bytes=total, files=sorted(files) + ["SHA256SUMS"],
                downloads_row=row, info=info)


def _arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--sample" in argv:                                   # the small sample of an already exported graph
        kgx = Path(_arg(argv, "--kgx", ROOT / "data" / "kg" / "assembled" / "g3"))
        out = Path(_arg(argv, "--sample-out", kgx.parent / (kgx.name + "_sample")))
        seeds = tuple(x for x in _arg(argv, "--seeds", "").split(",") if x)
        got = write_sample(kgx, out, seeds, int(_arg(argv, "--hop1", 50)), int(_arg(argv, "--hop2", 3)))
        print(f"sample: {got['nodes']:,} nodes, {got['edges']:,} edges, {got['bytes']:,} bytes, {len(got['files'])} files -> {out}")
        print("downloads.json row (put it first, after uploading the folder as sample/):")
        print(json.dumps(got["downloads_row"], indent=2))
        return 0
    out = _arg(argv, "--out")
    names = [a for a in argv if not a.startswith("--") and a != out]
    if len(names) != 1 or names[0] not in mg.RECIPES:
        sys.exit(__doc__)
    rep, problems, paths = export_graph(names[0], out_dir=out, include_pruned="--include-pruned" in argv,
                                        allow_missing="--allow-missing" in argv)
    print(f"{names[0]}: {rep.get('n_nodes', 0):,} nodes, {rep.get('n_edges', 0):,} edges; "
          f"{'written ' + ', '.join(map(str, paths)) if paths else 'NOT written'}")
    for line in fixes_markdown(rep["overrides"]) if rep.get("overrides") else []:
        print(line)
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
