"""
Schema card (Standards, 10% of the score; BUILD_ROLES section 2, Code item 4): every node class and predicate of the
Pharos core and its layers, with its BioLink IRI, the ontology terms the BioLink model itself maps it to (Protein ->
PR:000000001, Gene -> SO:0000704 ...), the identifier prefix -> class rule, and counts per layer. Nothing is typed by
hand: terms come from data/external/biolink/biolink-model.yaml, counts from each layer's committed build summary
(outputs/<layer>/summary.md, written by build_kg.py), the prefix rule from make_graphs.PREFIX_CATEGORY.

    python pipelines/schema_card.py        -> docs/SCHEMA_CARD.md + outputs/schema_card.json

The counts are those of the summaries on disk: in the cloud the committed ones, on the Mac the ones the last build
wrote. The card names the file and source version behind every column, so a stale summary is visible.

  parse_summary(text)          -> {title, source_version, n_nodes, n_edges, nodes_by_class, edges}: one build summary
  load_biolink(path)           -> {version, classes: {Name: {iri, exact_mappings}}, slots: {name: {iri, exact_mappings}}}
  layer_summaries(root)        -> [(layer name, path, parsed summary)] for every source card whose summary exists
  build_card(root, biolink)    -> the card dict (layers, classes, predicates, prefixes, problems, brief_iris, trial_layer,
                                  design, export)
  card_markdown(card)          -> docs/SCHEMA_CARD.md text
  brief_iris(card_path, labels, summary) -> the three IRIs the organisers' brief names: full IRI, where it is, how many
                                  nodes or edges carry it (PK5, finding ES-01)
  trial_layer(root, biolink)   -> the trial layer's own tmkg: terms with their BioLink parent and counts (finding ES-11)
  design_numbers(root)         -> the two counts the "why these classes" paragraph quotes, from outputs/g2/summary.md
  export_section(root)         -> the corrections made when the graph is exported: the rules of the source cards'
                                  `export_overrides` blocks, the counts the exports wrote (outputs/export_overrides/*.json),
                                  the knowledge sources that are not in the infores registry, the prefixes BioLink does not list
  export_markdown(export)      -> that section's Markdown ("## Corrections made when the graph is exported" to the end)

    python pipelines/schema_card.py --export-section   -> rewrites ONLY that section of docs/SCHEMA_CARD.md (the layer
                                                          counts above it stay as the last full run on the Mac wrote them)
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
BIOLINK_YAML = Path("data") / "external" / "biolink" / "biolink-model.yaml"
BIOLINK_REFERENCE = Path("data") / "reference" / "biolink-model.yaml"   # committed copy shipped in the image (CC-34)


def biolink_yaml(*roots):
    """Takes repo roots in order. Returns the first BioLink model file found: the downloaded copy, then the committed
    reference copy, under each root in turn; the first root's downloaded path when none exists (load_biolink then raises)."""
    for r in roots:
        for rel in (BIOLINK_YAML, BIOLINK_REFERENCE):
            if (Path(r) / rel).is_file():
                return Path(r) / rel
    return Path(roots[0]) / BIOLINK_YAML
VOCAB = "https://w3id.org/biolink/vocab/"


def _int(s):
    return int(s.replace(",", "").strip())


def parse_summary(text):
    """Takes the text of one outputs/<layer>/summary.md. Returns {title, source_version, n_nodes, n_edges,
    nodes_by_class: {biolink:Class: n}, edges: [{predicate, source, edges, rows}]} (empty parts when a table is
    missing)."""
    out = dict(title="", source_version="", n_nodes=None, n_edges=None, nodes_by_class={}, edges=[])
    m = re.search(r"^# (.+?) graph \(source version (.+)\)\s*$", text, re.M)
    if m:
        out["title"], out["source_version"] = m.group(1).strip(), m.group(2).strip()
    m = re.search(r"^([\d,]+) nodes, ([\d,]+) edges\.", text, re.M)
    if m:
        out["n_nodes"], out["n_edges"] = _int(m.group(1)), _int(m.group(2))
    section = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].lower()
            continue
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= set("-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if section and section.startswith("nodes by class") and len(cells) == 2 and cells[0].startswith("biolink:"):
            out["nodes_by_class"][cells[0]] = _int(cells[1])
        elif section and section.startswith("edges by predicate") and len(cells) == 4 and ":" in cells[0]:
            out["edges"].append(dict(predicate=cells[0], source=cells[1], edges=_int(cells[2]), rows=_int(cells[3])))
    return out


NO_EXACT_MAPPING = "BioLink defines no exact mapping for this class"   # the ontology-terms cell of such a class


def _camel(name):
    return "".join(w[:1].upper() + w[1:] for w in name.split())


def load_biolink(path):
    """Takes the BioLink model YAML path. Returns {version, classes: {CamelName: {iri, exact_mappings}}, slots:
    {snake_name: {iri, exact_mappings}}}; FileNotFoundError when it is missing (scripts/download_external_data.sh)."""
    import yaml
    y = yaml.safe_load(Path(path).read_text())
    classes = {_camel(k): dict(iri=VOCAB + _camel(k), exact_mappings=list((v or {}).get("exact_mappings") or []))
               for k, v in (y.get("classes") or {}).items()}
    slots = {k.replace(" ", "_"): dict(iri=VOCAB + k.replace(" ", "_"),
                                       exact_mappings=list((v or {}).get("exact_mappings") or []))
             for k, v in (y.get("slots") or {}).items()}
    return dict(version=str(y.get("version", "")), classes=classes, slots=slots)


def layer_summaries(root=ROOT):
    """Takes the repo root. Returns [(layer, summary path relative to root, parsed summary)] for every
    sources/*.yaml card whose outputs/<name>/summary.md exists, Pharos first, then by name."""
    import yaml
    root = Path(root)
    found = []
    for card in sorted((root / "sources").glob("*.yaml")):
        name = (yaml.safe_load(card.read_text()) or {}).get("name") or card.stem
        path = root / "outputs" / name / "summary.md"
        if path.exists():
            found.append((name, str(path.relative_to(root)), parse_summary(path.read_text())))
    found.sort(key=lambda x: (x[0] != "pharos", x[0]))
    return found


def _local(term):
    return term.split(":", 1)[1] if ":" in term else term


def build_card(root=ROOT, biolink=None):
    """Takes the repo root and the loaded BioLink model (load_biolink; read from root when None). Returns
    {brief_iris (brief_iris()), trial_layer (trial_layer()), design (design_numbers()),
    biolink_version, layers: [{layer, file, source_version, n_nodes, n_edges}], classes: [{name, curie, iri,
    exact_mappings, is_biolink, nodes: {layer: n}}], predicates: [{name, curie, iri, exact_mappings, is_biolink,
    edges: {layer: n}, sources: {layer: [primary sources]}}], prefixes: [{prefix, class}], problems: [...]}:
    problems lists every class or predicate that is not a BioLink term (CLAUDE.md rule 6)."""
    import make_graphs
    root = Path(root)
    biolink = biolink or load_biolink(biolink_yaml(root))
    layers = layer_summaries(root)
    classes, preds, problems = {}, {}, []
    for layer, _, s in layers:
        for curie, n in s["nodes_by_class"].items():
            classes.setdefault(curie, {})[layer] = n
        for e in s["edges"]:
            p = preds.setdefault(e["predicate"], {"edges": {}, "sources": {}})
            p["edges"][layer] = p["edges"].get(layer, 0) + e["edges"]
            p["sources"].setdefault(layer, []).append(e["source"])

    def term(curie, table, kind):
        name = _local(curie)
        hit = table.get(name) if curie.startswith("biolink:") else None
        if hit is None:
            problems.append(f"{kind} {curie} is not a BioLink {kind} in model {biolink['version']}")
        return dict(name=name, curie=curie, iri=hit["iri"] if hit else "", exact_mappings=hit["exact_mappings"] if hit
                    else [], is_biolink=hit is not None)

    out_classes = [dict(term(c, biolink["classes"], "class"), nodes=n)
                   for c, n in sorted(classes.items(), key=lambda kv: -sum(kv[1].values()))]
    out_classes += export_classes(root, set(classes), lambda c: term(c, biolink["classes"], "class"))   # FIX1 F11 (G1-ES-04)
    out_preds = [dict(term(p, biolink["slots"], "predicate"), edges=v["edges"],
                      sources={k: sorted(set(x)) for k, x in v["sources"].items()})
                 for p, v in sorted(preds.items(), key=lambda kv: -sum(kv[1]["edges"].values()))]
    prefixes = [dict(prefix=k, **{"class": v}) for k, v in sorted(make_graphs.PREFIX_CATEGORY.items())]
    pharos = next((s for l, _, s in layers if l == "pharos"), None)
    card_path = root / "sources" / "pharos.yaml"
    return dict(export=export_section(root), brief_iris=brief_iris(card_path, summary=pharos) if card_path.is_file() else [],
                trial_layer=trial_layer(root, biolink), design=design_numbers(root),
                biolink_version=biolink["version"],
                layers=[dict(layer=l, file=f, source_version=s["source_version"], n_nodes=s["n_nodes"],
                             n_edges=s["n_edges"]) for l, f, s in layers],
                classes=out_classes, predicates=out_preds, prefixes=prefixes, problems=problems)


LAYER_WORDS = {"dto": "the Drug Target Ontology table"}      # FIX1 F11: reader words for a layer named in a new sentence


def export_classes(root, known, term):
    """FIX1 F11 (gate 1, G1-ES-04). Takes the repo root, the class CURIEs the layer summaries already hold and term(curie)
    (build_card's lookup in the BioLink model). Returns one class row per class that only the export writes (a retype_nodes
    rule of a source card, e.g. ChEBI terms of the DTO table written as biolink:ChemicalEntity): the term, nodes = {its
    layer: the number of nodes the export re-typed, read from the first export report}, at_export True. [] before an export
    has counted it."""
    import export_graph_kgx as xk
    reports = export_reports(root)
    first = next(iter(reports.values()), None)
    retyped = ((first or {}).get("nodes") or {}).get("retyped") or {}
    out = []
    for r in xk.load_overrides(root)["retype_nodes"]:
        n = retyped.get(r["name"])
        if r["to"] in known or not n or any(c["curie"] == r["to"] for c in out):
            continue
        out.append(dict(term(r["to"]), nodes={r["layer"]: n}, at_export=True))
    return out


def gene_class_note(card):
    """FIX1 F11 (gate 1, G1-ES-03). Takes the card. Returns the words after the Gene class's BioLink defaults: how many Gene
    nodes carry SO:0001217 out of all Gene nodes, measured at export (the first report with gene type counts), and that every
    other one carries the term of its own biotype; the rule alone (no count) before an export has measured it."""
    got = _gene_report((card.get("export") or {}).get("reports") or {})
    if got:
        n = got[1]["nodes"]
        return (f"{n['gene_types'].get('SO:0001217', 0):,} of the {n['genes']:,} Gene nodes carry the more specific type "
                f"`SO:0001217` (protein-coding), every other Gene node the Sequence Ontology term of its own biotype "
                f"(counted in the {product_words(got[0])}, not in the layer columns; see the first section)")
    return ("protein-coding Gene nodes carry the more specific type `SO:0001217`, every other Gene node the Sequence Ontology "
            "term of its own biotype (set at export; see the first section)")


def _n(v):
    return "-" if v is None else f"{v:,}"


def prefix_rule_sentence(prefix_category):
    """Takes make_graphs.PREFIX_CATEGORY. Returns one sentence stating the rule, the prefixes re-typed as phenotypic
    feature and the class EFO terms get, computed from the dict (CC-27)."""
    pheno = sorted(p for p, c in prefix_category.items() if c == "biolink:PhenotypicFeature")
    dis = sorted(p for p, c in prefix_category.items() if c == "biolink:Disease")
    efo = prefix_category.get("EFO")
    s = (f"One class per identifier prefix in every product (assembled graphs, card index and app, KGX and Neptune files): "
         f"{'/'.join(pheno)} terms are phenotypic features (biolink:PhenotypicFeature), {'/'.join(dis)} terms are diseases")
    if efo:
        s += (f", and EFO terms without an exact MONDO class (an exact match is mapped to MONDO when the layer is built) "
              f"are {efo}")
    return s + ", whatever class a single layer gave them."


def card_markdown(card):
    """Takes the card dict. Returns the Markdown of docs/SCHEMA_CARD.md (every count names the layer summary it
    comes from; problems listed first when there are any)."""
    L = [l["layer"] for l in card["layers"]]
    lines = ["# Schema card: the Pharos core, its layers and the trial layer", "",
             "Generated by `python pipelines/schema_card.py` from the BioLink model file and the layer build summaries; "
             "do not edit by hand. Counts are per layer (a node present in two layers is counted in each).", "",
             f"BioLink model version: {card['biolink_version']}.", ""]
    lines += brief_iris_markdown(card.get("brief_iris") or [])
    if card["problems"]:
        lines += ["## Problems (terms that are not BioLink)", ""] + [f"- {p}" for p in card["problems"]] + [""]
    else:
        lines += ["Every class and predicate of the Pharos core and the enrichment layers below is a BioLink Model term "
                  "(checked against the model file). The trial layer adds its own terms; they are listed, each with its "
                  "BioLink parent, in the last section.", ""]
    lines += ["## Layers and the files the counts come from", "", "| layer | source version | nodes | edges | file |",
              "|---|---|---|---|---|"]
    lines += [f"| {l['layer']} | {l['source_version']} | {_n(l['n_nodes'])} | {_n(l['n_edges'])} | `{l['file']}` |"
              for l in card["layers"]]
    head = "| class | IRI | ontology terms (BioLink exact mappings) | " + " | ".join(f"nodes in {x}" for x in L) + " |"
    lines += ["", "## Node classes", "", head, "|" + "---|" * (3 + len(L))]
    node_types = {r["class"]: r for r in card.get("brief_iris") or [] if r.get("class") and r["in_card"]}
    for c in card["classes"]:
        maps = ", ".join(f"`{m}`" for m in c["exact_mappings"]) or (
            NO_EXACT_MAPPING if c.get("is_biolink") else "-")       # G1-ES-07 (owner, 14 Nov): say so, never invent one
        nt = node_types.get(c["name"])
        if nt and nt["curie"] not in c["exact_mappings"]:      # the class default is more general than the node-level type
            if c["name"] == "Gene" and nt["curie"] == "SO:0001217":   # FIX1 F11: measured, never "every Gene node"
                maps += f" (the BioLink defaults for the class; {gene_class_note(card)})"
            else:
                maps += (f" (the BioLink defaults for the class; every Pharos {c['name']} node carries the more specific "
                         f"type `{nt['curie']}`, see the first section)")
        if c.get("at_export"):
            maps += " (written when the graph is exported: " + "; ".join(
                f"{n:,} nodes of {LAYER_WORDS.get(layer, layer)} re-typed" for layer, n in c["nodes"].items()) + ", see the last section)"
        lines.append(f"| {c['name']} | {c['iri'] or 'NOT BIOLINK'} | {maps} | "
                     + " | ".join(_n(c["nodes"].get(x)) for x in L) + " |")
    head = "| predicate | IRI | relation ontology (BioLink exact mappings) | primary sources | " + \
        " | ".join(f"edges in {x}" for x in L) + " |"
    lines += ["", "## Predicates", "", head, "|" + "---|" * (4 + len(L))]
    for p in card["predicates"]:
        maps = ", ".join(f"`{m}`" for m in p["exact_mappings"]) or "-"
        srcs = "; ".join(f"{k}: {', '.join(v)}" for k, v in p["sources"].items())
        lines.append(f"| {p['name']} | {p['iri'] or 'NOT BIOLINK'} | {maps} | {srcs} | "
                     + " | ".join(_n(p["edges"].get(x)) for x in L) + " |")
    lines += ["", "## One class per identifier prefix (make_graphs.PREFIX_CATEGORY)", "",
              "Layers disagree on classes (Open Targets lists some HP and GO terms as diseases); when the graphs are "
              "assembled every identifier with these prefixes gets this class:", "",
              prefix_rule_sentence({p["prefix"]: p["class"] for p in card["prefixes"]}), "", "| prefix | class |", "|---|---|"]
    lines += [f"| {p['prefix']} | {p['class']} |" for p in card["prefixes"]]
    lines += design_markdown(card)
    lines += trial_layer_markdown(card.get("trial_layer"))
    if card.get("export"):
        lines += [""] + export_markdown(card["export"])
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ corrections made at export (the export fixes)
EXPORT_HEADING = "## Corrections made when the graph is exported"
REPORT_ORDER = ("g3", "g4", "card_index")          # the products in the order their count columns are shown
PRODUCT_WORDS = {"g3": "KGX tables of the release graph", "g4": "KGX tables of the graph with the trial layer",
                 "card_index": "the app's card index"}   # plain words for the count columns (the page shows no file stems)


def product_words(stem):
    """Takes a report file stem ('g3', 'card_index', 'gx'). Returns the plain words the schema card prints for that
    product: the entry of PRODUCT_WORDS, else 'KGX tables (<stem in capitals>)' for any other graph export."""
    return PRODUCT_WORDS.get(stem, f"KGX tables ({stem.upper()})")


def export_reports(root=ROOT):
    """Takes the repo root. Returns {file stem: report dict} for outputs/export_overrides/*.json (written by the KGX export
    and the card index build), the release graph first; {} when no export has been run with the rules yet."""
    import export_graph_kgx as xk
    d = Path(root) / xk.REPORT_DIR
    found = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))} if d.is_dir() else {}
    return {k: found[k] for k in sorted(found, key=lambda k: (REPORT_ORDER.index(k) if k in REPORT_ORDER else 99, k))}


def card_infores_ids(root=ROOT):
    """Takes the repo root. Returns the sorted infores ids the source cards WRITE into the graph (every sources/*.yaml,
    its export_overrides block left out: that block only quotes ids in its explanations, except the ones a rule sets)."""
    import yaml
    import export_graph_kgx as xk
    ids = set()
    for card in sorted((Path(root) / "sources").glob("*.yaml")):
        y = yaml.safe_load(card.read_text(encoding="utf-8")) or {}
        block = y.pop(xk.OVERRIDES_KEY, None) or {}
        ids |= set(re.findall(r"infores:[A-Za-z0-9._-]+", json.dumps(y)))
        ids |= {v for r in block.get("relabel_edges") or [] for v in (r.get("set") or {}).values()
                if str(v).startswith("infores:")}
    return sorted(ids)


def export_section(root=ROOT):
    """Takes the repo root. Returns {gene_type: {property, unknown, terms: [{so_id, so_name, biotypes}]} or None,
    relabel_edges: [{layer, name, when, set, why}], node_rules: [{layer, name, what, why}], kept_as_is: [{layer, what,
    why}], unregistered_infores: [{id, status}], prefix_rules: [{prefix, class, listed_by_biolink, note}], reports:
    {product: report}, rule_problems: [...]}. Rules come from the cards, names from data/reference/so_gene_terms.tsv,
    registry status from data/reference/infores_checked.tsv, counts from the reports the exports wrote."""
    import make_graphs
    import export_graph_kgx as xk
    from kgbuilder.biolink import BioLink
    root = Path(root)
    ov = xk.load_overrides(root)
    so = xk.so_terms(root)
    gene = None
    if ov["gene_type"]:
        gt = ov["gene_type"]
        by_term = {}
        for biotype, term in sorted(gt["map"].items()):
            by_term.setdefault(term, []).append(biotype)
        by_term.setdefault(gt["unknown"], [])
        gene = dict(property=gt["property"], unknown=gt["unknown"], layer=gt["layer"],
                    terms=[dict(so_id=t, so_name=so.get(t, ""), biotypes=b) for t, b in sorted(by_term.items())])
    cond = lambda w: "; ".join(f"{k} = {' or '.join(v) if isinstance(v, list) else v}" for k, v in w.items())   # noqa: E731
    relabel = [dict(layer=layer, name=r["name"], when=cond(r["when"]), set=cond(r["set"]), why=r.get("why", ""))
               for layer, rules in sorted(ov["relabel_edges"].items()) for r in rules]
    node_rules = [dict(layer=r["layer"], name=r["name"], why=r.get("why", ""),
                       what=f"{'/'.join(r['prefixes'])} identifiers typed {r['from']} are written as {r['to']}")
                  for r in ov["retype_nodes"]]
    node_rules += [dict(layer=r["layer"], name=r["name"], why=r.get("why", ""),
                        what=f"identifiers matching `{r['match']}` are written as `{r['to']}`"
                             + (", then as the NCBI Gene id when HGNC maps it" if r.get("via_hgnc") else "")
                             + (f", class {r['category']}" if r.get("category") else "")) for r in ov["rename_ids"]]
    status = xk.infores_status(root)
    unregistered = [dict(id=i, status=status.get(i, "not looked up")) for i in card_infores_ids(root)
                    if status.get(i) != "released"]
    try:
        bl = BioLink(biolink_yaml(root, ROOT))
        renamed_away = {r["match"].lstrip("^").split(":")[0] for r in ov["rename_ids"]}
        prefix_rules = []
        for pre, cls in sorted(make_graphs.PREFIX_CATEGORY.items()):
            listed = bl.prefixes(cls)
            note = ("rewritten at export, see the identifier rules above" if pre in renamed_away else
                    "BioLink lists no prefixes for this class" if not listed else "")
            if pre not in listed:
                prefix_rules.append(dict(prefix=pre, **{"class": cls}, listed_by_biolink=False, note=note))
    except FileNotFoundError:
        prefix_rules = []
    return dict(gene_type=gene, relabel_edges=relabel, node_rules=node_rules,
                kept_as_is=[dict(layer=k["layer"], what=k["what"], why=" ".join(k["why"].split())) for k in ov["kept_as_is"]],
                unregistered_infores=unregistered, prefix_rules=prefix_rules, reports=export_reports(root),
                rule_problems=xk.check_overrides(ov, root))


def _gene_report(reports):
    """The first report that holds gene type counts (the release graph's KGX export, else the card index), or None."""
    return next(((k, r) for k, r in reports.items() if (r.get("nodes") or {}).get("gene_types")), None)


def export_markdown(ex):
    """Takes export_section's dict. Returns the Markdown lines of the section. Counts are printed only when an export has
    written them; until then every count cell says 'not measured yet'."""
    reports = ex["reports"]
    names = list(reports)
    none = "not measured yet"
    lines = [EXPORT_HEADING, "",
             "The layer files, the training graphs of the link-prediction benchmark and the verdict file keep the values the "
             "source cards wrote: **the benchmark graphs used the original labels**. The corrections below are made when the "
             "KGX tables, the card index and the Neptune files are written. The rules are the `export_overrides` blocks of "
             "`sources/*.yaml`; every Sequence Ontology term was looked up in `data/reference/so_gene_terms.tsv` and every "
             "knowledge-source id in `data/reference/infores_checked.tsv` (extracts of so.obo and of the infores registry).", ""]
    words = {n: product_words(n) for n in names}
    if names:
        lines += ["Counts were measured when these products were written: " + ", ".join(words[n] for n in names) + ".", ""]
    else:
        lines += ["Counts: not measured yet. They are measured when the KGX tables and the card index are next written.", ""]
    for p in ex.get("rule_problems") or []:
        lines.append(f"- PROBLEM in the rules: {p}")
    g = ex["gene_type"]
    if g:
        got = _gene_report(reports)
        n = (got[1]["nodes"] if got else {})
        lines += ["### Gene nodes: one Sequence Ontology type each", "",
                  f"The node property `type` of every Gene node is the Sequence Ontology term of its Open Targets biotype "
                  f"(`{g['property']}`). A gene without a biotype keeps the type its source card gave it (Pharos: SO:0001217 for "
                  f"the gene of a Pharos protein); a gene with neither gets {g['unknown']}.", ""]
        if got:
            pc = n["gene_types"].get("SO:0001217", 0)
            lines += [f"Measured on {product_words(got[0])}: {n['genes']:,} Gene nodes, all typed; {pc:,} of them SO:0001217 (protein-coding). "
                      "Type set from: " + ", ".join(f"{k} {v:,}" for k, v in n.get("gene_type_basis", {}).items()) + ".", ""]
        lines += ["| SO term | SO name | biotypes written as this term | Gene nodes |", "|---|---|---|---|"]
        for t in g["terms"]:
            what = ", ".join(t["biotypes"]) or "-"
            if t["so_id"] == g["unknown"]:
                what = (what + "; " if t["biotypes"] else "") + "no biotype, or a biotype without an SO gene term"
            cnt = f"{n['gene_types'].get(t['so_id'], 0):,}" if got else none
            lines.append(f"| `{t['so_id']}` | {t['so_name']} | {what} | {cnt} |")
        if got and n.get("gene_type_changed"):
            lines += ["", "Types replaced because the biotype says otherwise: "
                      + ", ".join(f"{k} ({v:,})" for k, v in n["gene_type_changed"].items()) + "."]
        if got and n.get("biotypes_without_term"):
            lines += ["", "Biotypes without a Sequence Ontology gene term: "
                      + ", ".join(f"{k} ({v:,})" for k, v in n["biotypes_without_term"].items()) + "."]
        lines.append("")
    count_head = " | ".join(f"edge rows, {words[x]}" for x in names) or "edge rows"
    if ex["relabel_edges"]:
        lines += ["### Edge labels corrected (the benchmark graphs used the original labels)", "",
                  f"| layer | rows relabelled | label written | why | {count_head} |", "|---|---|---|---|" + "---|" * max(len(names), 1)]
        for r in ex["relabel_edges"]:
            cells = [f"{(reports[x].get('edges') or {}).get(r['layer'], {}).get(r['name'], 0):,}" for x in names] or [none]
            lines.append(f"| {r['layer']} | {r['name']}: {r['when']} | {r['set']} | {r['why']} | " + " | ".join(cells) + " |")
        lines.append("")
    if ex["node_rules"]:
        count_head = " | ".join(f"nodes, {words[x]}" for x in names) or "nodes"
        lines += ["### Classes and identifiers corrected", "",
                  f"| layer | rule | what is written | why | {count_head} |", "|---|---|---|---|" + "---|" * max(len(names), 1)]
        for r in ex["node_rules"]:
            def cnt(rep, name=r["name"]):
                v = (rep.get("nodes") or {}).get("retyped", {}).get(name, (rep.get("ids_renamed") or {}).get(name))
                return "-" if v is None else f"{v:,}"
            cells = [cnt(reports[x]) for x in names] or [none]
            lines.append(f"| {r['layer']} | {r['name']} | {r['what']} | {r['why']} | " + " | ".join(cells) + " |")
        lines.append("")
        first = reports[names[0]] if names else {}
        for rule, rows in ((first.get("nodes") or {}).get("retyped_nodes") or {}).items():
            lines += [f"Nodes of the rule '{rule}' ({words[names[0]]}): " + ", ".join(f"{i} ({nm})" if nm else i for i, nm in rows) + ".", ""]
    if ex["kept_as_is"]:
        lines += ["### Kept as they are, and why", ""]
        lines += [f"- {k['layer']}: {k['what']}. {k['why'][:1].upper() + k['why'][1:].rstrip('.')}." for k in ex["kept_as_is"]]
        lines.append("")
    lines += ["### Knowledge sources that are not in the infores registry", ""]
    lines += [f"- `{u['id']}`: {u['status']}." for u in ex["unregistered_infores"]] or \
             ["- none: every knowledge-source id the source cards write is a released registry entry."]
    lines += ["", "### Identifier prefixes BioLink does not list for the class", "",
              "Prefix rules (one class per prefix, above) whose prefix is not in the BioLink model's id_prefixes of the class:", "",
              "| prefix | class | note |", "|---|---|---|"]
    lines += [f"| {p['prefix']} | {p['class']} | {p['note'] or 'not listed'} |" for p in ex["prefix_rules"]] or ["| - | - | none |"]
    measured = next(((k, r["prefixes_not_listed_by_biolink"]) for k, r in reports.items()
                     if r.get("prefixes_not_listed_by_biolink") is not None), None)
    if measured:
        lines += ["", f"Measured on the node table of {product_words(measured[0])}: prefixes the BioLink model does not list for the "
                  "node's class, with node counts.", "", "| class | prefix | nodes |", "|---|---|---|"]
        lines += [f"| {c.split(':')[-1]} | {pre} | {k:,} |" for c, d in measured[1].items() for pre, k in d.items()] or ["| - | - | 0 |"]
    else:
        lines += ["", "The same list measured on the exported node table (with node counts): not measured yet."]
    return lines


def write_export_section(root=ROOT):
    """Takes the repo root. Replaces the export section of docs/SCHEMA_CARD.md (from EXPORT_HEADING to the end; appended
    when the card has none) with export_markdown(export_section(root)); every line above it is left as it is. Returns the path."""
    path = Path(root) / "docs" / "SCHEMA_CARD.md"
    text = path.read_text(encoding="utf-8")
    head = text[:text.index(EXPORT_HEADING)] if EXPORT_HEADING in text else text.rstrip("\n") + "\n\n"
    path.write_text(head + "\n".join(export_markdown(export_section(root))) + "\n", encoding="utf-8")
    return path


def check(root=ROOT, log=print):
    """`schema_card.py --check` (make check): builds the card in memory and writes nothing. Takes the repository root.
    Returns 1 when a class or predicate is not a BioLink term (CLAUDE.md rule 6), else 0. A docs/SCHEMA_CARD.md that
    differs from the rebuilt card (layer counts move with every rebuild) is reported as a WARNING, not a failure."""
    card = build_card(root)
    for p in card["problems"]:
        log(f"FAIL schema: {p}")
    committed = Path(root) / "docs" / "SCHEMA_CARD.md"
    if committed.exists() and committed.read_text() != card_markdown(card):
        log("WARNING schema: docs/SCHEMA_CARD.md differs from the layer summaries in outputs/ "
            "(regenerate with: python pipelines/schema_card.py)")
    log(f"schema check: {len(card['classes'])} classes, {len(card['predicates'])} predicates, "
        f"{len(card['problems'])} non-BioLink terms")
    return 1 if card["problems"] else 0


def main(argv=None):
    root = ROOT
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        return check(root)
    if "--export-section" in args:                       # only the corrections-at-export section; layer counts untouched
        print(f"export section rewritten in {write_export_section(root).relative_to(root)}")
        return 0
    card = build_card(root)
    (root / "docs" / "SCHEMA_CARD.md").write_text(card_markdown(card))
    (root / "outputs").mkdir(exist_ok=True)
    (root / "outputs" / "schema_card.json").write_text(json.dumps(card, indent=1) + "\n")
    print(f"schema card: {len(card['classes'])} classes, {len(card['predicates'])} predicates, "
          f"{len(card['layers'])} layer summaries, {len(card['problems'])} problems -> docs/SCHEMA_CARD.md")
    return 1 if card["problems"] else 0


# ------------------------------------------------------------------ the brief's three IRIs (CC-34, review m4; PK5 ES-01)
OBO = "http://purl.obolibrary.org/obo/"
# (CURIE used in the source card, the IRI exactly as the organisers' brief prints it, meaning)
BRIEF_IRIS = [("SO:0001217", OBO + "SO_0001217", "protein-coding gene"),
              ("PR:000000001", OBO + "PR_000000001", "protein"),
              ("SO:translates_to", OBO + "so#translates_to", "translates to (protein-coding gene -> protein)")]
# The term the brief calls the less good choice for a protein-coding gene; BioLink maps its Gene class to it.
BRIEF_GENERAL_GENE = ("SO:0000704", OBO + "SO_0000704")


def gene_type_sentence(root):
    """Takes the repo root. Returns the words for 'where SO:0001217 is': the MEASURED number of Gene nodes that carry it
    out of all Gene nodes (from the export reports, the card index first: it is the graph the page shows), or, before any
    export has been run with the rules, the rule in words without a number. Never 'every Gene node'."""
    reports = export_reports(root)
    ordered = [reports[k] for k in ("card_index",) if k in reports] + [r for k, r in reports.items() if k != "card_index"]
    n = next(((r.get("nodes") or {}) for r in ordered if (r.get("nodes") or {}).get("gene_types")), None)
    rule = ("protein-coding by Open Targets biotype, or the gene of a Pharos protein where there is no biotype; every other "
            "Gene node carries the Sequence Ontology term of its own biotype, SO:0000704 when the biotype is unknown")
    if n:
        return f"type of {n['gene_types'].get('SO:0001217', 0):,} of {n['genes']:,} Gene nodes ({rule})"
    return f"type of the Gene nodes that are {rule} (set when the graph is exported; the node count follows the next export)"


def brief_iris(card_path, labels=None, summary=None):
    """Takes the Pharos source card (sources/pharos.yaml), optionally the loaded graph's {class: [properties]} and
    optionally the parsed Pharos build summary (parse_summary). Returns one row per IRI the organisers' brief names:
    {iri (the full IRI, as the brief prints it), url (the same, for a link), curie (as stored in the graph), meaning,
    kind ('node' / 'edge'), class (node rows: the BioLink class name), predicate (edge rows: the BioLink predicate used),
    where (read from the card), in_card, in_graph, count, count_what}.
    count: how many nodes or edges of the Pharos layer carry the IRI: for the Gene row the number measured when the graph
    was exported (export_reports; None before an export with the rules), otherwise read from the build summary (the node
    count of the class whose every node has that `type`; the edge count of the predicate whose every edge has that
    original_predicate); without a summary given, outputs/pharos/summary.md next to the card is read; None without one or
    when the card gives the class / predicate to more than one table.
    in_graph: True / False when the loaded graph's class carries the property (not checkable for edges: None), None
    without a loaded graph."""
    import yaml
    card = yaml.safe_load(Path(card_path).read_text(encoding="utf-8"))
    root = Path(card_path).resolve().parents[1]
    if summary is None:                  # FIX2 F8 (G2-ES-06): /api/results passes none; read the layer's own summary
        sp = root / "outputs" / "pharos" / "summary.md"
        summary = parse_summary(sp.read_text(encoding="utf-8")) if sp.is_file() else None
    nodes, edges = card.get("nodes") or [], card.get("edges") or []
    out = []
    for curie, iri, meaning in BRIEF_IRIS:
        where, in_graph, count, what, kind, cls, pred = [], None, None, "", "", "", ""
        for n in nodes:
            if (n.get("constants") or {}).get("type") == curie:
                kind, cls = "node", n["category"].split(":", 1)[1]
                where.append(gene_type_sentence(root) if n["category"] == "biolink:Gene" else
                             f"type of the {cls} nodes Pharos lists (table {n['table']})")
                if labels is not None:
                    in_graph = "type" in (labels.get(cls) or [])
                if n["category"] == "biolink:Gene":            # PK13: genes carry their own biotype; count the measured ones
                    rep = _gene_report(export_reports(root))
                    if rep:
                        g = rep[1]["nodes"]
                        count, what = g["gene_types"].get(curie, 0), f"of {g['genes']:,} Gene nodes (counted at export)"
                elif summary and sum(1 for x in nodes if x.get("category") == n["category"]) == 1:
                    count, what = summary["nodes_by_class"].get(n["category"]), f"{cls} nodes"
        for e in edges:
            if (e.get("constants") or {}).get("original_predicate") == curie:
                kind, pred = "edge", e["predicate"]
                where.append(f"original_predicate of the {e['predicate']} edges ({e['name']}); BioLink's translates_to "
                             "needs a transcript as subject, so the gene -> protein edge uses that predicate")
                if summary and sum(1 for x in edges if x.get("predicate") == e["predicate"]) == 1:
                    count = sum(x["edges"] for x in summary["edges"] if x["predicate"] == e["predicate"]) or None
                    what = f"{_local(e['predicate'])} edges"
        out.append({"iri": iri, "url": iri, "curie": curie, "meaning": meaning, "kind": kind, "class": cls,
                    "predicate": pred, "where": "; ".join(where) or "not in the source card", "in_card": bool(where),
                    "in_graph": in_graph, "count": count, "count_what": what})
    return out


def brief_iris_markdown(rows):
    """Takes brief_iris() rows. Returns the Markdown lines of the card's first section: the three IRIs in full, as links,
    with what carries each and how many, and the sentence on gene -> protein (empty list when there are no rows)."""
    if not rows:
        return []
    lines = ["## The brief's three IRIs", "",
             "The organisers' brief names three terms a graph should make recognisable. Each is in the Pharos layer, "
             "stored as the short form shown and resolving to the full IRI:", "",
             "| meaning | full IRI | stored as | carried by | how many (Pharos layer) |", "|---|---|---|---|---|"]
    for r in rows:
        n = "-" if r["count"] is None else f"{r['count']:,} {r['count_what']}"
        lines.append(f"| {r['meaning']} | [{r['iri']}]({r['url']}) | `{r['curie']}` | "
                     f"{(r['where'] if r['kind'] == 'node' else r['where'].split(';')[0]) if r['in_card'] else 'NOT IN THE SOURCE CARD'} | {n} |")
    edge = next((r for r in rows if r["kind"] == "edge" and r["in_card"]), None)
    gene = next((r for r in rows if r["kind"] == "node" and r["class"] == "Gene" and r["in_card"]), None)
    lines.append("")
    if edge:
        lines.append(f"Why the gene -> protein edge is `{edge['predicate']}`: in the BioLink model `translates_to` joins a "
                     f"transcript to a protein, and this graph has no transcript nodes, so the edge takes the BioLink "
                     f"predicate for gene -> product and keeps the brief's [{edge['iri']}]({edge['url']}) on every such "
                     f"edge as `original_predicate`.")
    if gene:
        lines.append(f"Why genes carry `{gene['curie']}`: the brief asks for the most specific class. BioLink has one Gene "
                     f"class, mapped to the general [{BRIEF_GENERAL_GENE[1]}]({BRIEF_GENERAL_GENE[1]}) "
                     f"(`{BRIEF_GENERAL_GENE[0]}`), so the specific term is set as the `type` of each protein-coding Gene "
                     f"node and overrides that default; every other Gene node carries the Sequence Ontology term of its own "
                     f"biotype.")
    return lines + [""]


# ------------------------------------------------------------------ why these classes (PK5, finding ES-11)
def design_numbers(root=ROOT):
    """Takes the repo root. Returns {mixin_nodes, retyped_rows}: the DiseaseOrPhenotypicFeature node count of the enriched
    graph and the number of node rows re-typed to one class per identifier prefix, both read from outputs/g2/summary.md
    (None for a number that is not there)."""
    out = dict(mixin_nodes=None, retyped_rows=None)
    p = Path(root) / "outputs" / "g2" / "summary.md"
    if p.is_file():
        t = p.read_text(encoding="utf-8")
        m = re.search(r"^- DiseaseOrPhenotypicFeature: ([\d,]+) nodes", t, re.M)
        if m:
            out["mixin_nodes"] = _int(m.group(1))
        m = re.search(r"([\d,]+) node rows re-typed to one class per id prefix", t)
        if m:
            out["retyped_rows"] = _int(m.group(1))
    return out


def design_markdown(card):
    """Takes the card dict. Returns the Markdown lines of 'Why these classes': as few classes as the identifiers allow,
    each the most specific BioLink class for its identifier prefix; counts come from the card, never typed here."""
    d = card.get("design") or {}
    names = {c["name"] for c in card["classes"]}
    by = {c["name"]: c for c in card["classes"]}
    go = [n for n in ("BiologicalProcess", "MolecularActivity", "CellularComponent") if n in names]
    lines = ["", "## Why these classes", "",
             f"- **As few as the identifiers allow.** {len(card['classes'])} node classes and {len(card['predicates'])} "
             f"predicates cover the Pharos core and every enrichment layer. A class exists only where an identifier "
             f"prefix needs it: one BioLink class per prefix, the most specific one BioLink has for that prefix."]
    if d.get("retyped_rows") is not None:
        lines.append(f"- **What was merged.** Layers disagree (one source lists some phenotype and GO terms as diseases); "
                     f"when the graphs are assembled {d['retyped_rows']:,} node rows are re-typed so that one identifier "
                     f"has one class everywhere. A disease term with an exact MONDO class is mapped to MONDO when its "
                     f"layer is built, so the same disease is one node.")
    if len(go) > 1:
        roots = ", ".join(f"{n} = `{next((m for m in by[n]['exact_mappings'] if m.startswith('GO:')), '-')}`" for n in go)
        lines.append(f"- **Why {len(go)} classes for the Gene Ontology.** GO has three separate branches and BioLink has one "
                     f"class for each ({roots}); folding them into one class would lose which kind of annotation an edge is "
                     f"(takes part in, is able to do, is located in).")
    if "DiseaseOrPhenotypicFeature" in names:
        n = f"{d['mixin_nodes']:,} " if d.get("mixin_nodes") is not None else ""
        lines.append(f"- **Why DiseaseOrPhenotypicFeature next to Disease and PhenotypicFeature.** The {n}EFO terms without "
                     f"an exact MONDO class mix diseases, traits and measurements, and the identifier does not say which. "
                     f"Calling them all diseases would be a guess, so they keep the BioLink class that says exactly "
                     f"'disease or phenotypic feature'.")
    inter = [p["name"] for p in card["predicates"] if p["name"].endswith("physically_interacts_with")]
    if len(inter) > 1:
        lines.append("- **Why two interaction predicates.** `directly_physically_interacts_with` is used where the source "
                     "reports binding of a compound to its target; `physically_interacts_with` where a protein-protein "
                     "source reports an interaction without saying the contact is direct. One predicate would overstate "
                     "the second kind.")
    return lines


# ------------------------------------------------------------------ the trial layer's own vocabulary (PK5, finding ES-11)
TRIAL_NODES, TRIAL_EDGES = Path("outputs") / "kg_nodes.csv", Path("outputs") / "kg_edges.csv"


def _count_column(path, column):
    """Takes a CSV path and a column name. Returns {value: number of rows} ({} when the file is missing)."""
    import csv
    if not Path(path).is_file():
        return {}
    csv.field_size_limit(sys.maxsize)
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row[column]] = out.get(row[column], 0) + 1
    return out


def trial_layer(root=ROOT, biolink=None):
    """Takes the repo root and the loaded BioLink model. Returns the trial layer's vocabulary as built:
    {files, n_nodes, n_edges, classes: [{curie, is_biolink, parent, parent_iri, parent_ok, n}], predicates: [the same],
    n_own_classes, n_own_predicates, parents: {BioLink parent: number of tmkg predicates}}; None when the committed
    trial graph (outputs/kg_nodes.csv, outputs/kg_edges.csv) is not there. A tmkg: term's parent comes from
    kg_schema.BIOLINK_PARENT; parent_ok says the parent is a term of the BioLink model file."""
    import kg_schema
    root = Path(root)
    nodes, edges = _count_column(root / TRIAL_NODES, "category"), _count_column(root / TRIAL_EDGES, "predicate")
    if not nodes or not edges:
        return None
    biolink = biolink or load_biolink(biolink_yaml(root))

    def rows(counts, table):
        out = []
        for curie, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            own = not curie.startswith("biolink:")
            parent = kg_schema.BIOLINK_PARENT.get(curie, "") if own else curie
            hit = table.get(_local(parent)) if parent else None
            out.append(dict(curie=curie, is_biolink=not own, parent=parent, parent_iri=hit["iri"] if hit else "",
                            parent_ok=hit is not None, n=n))
        return out
    classes, preds = rows(nodes, biolink["classes"]), rows(edges, biolink["slots"])
    parents = {}
    for p in preds:
        if not p["is_biolink"]:
            parents[p["parent"]] = parents.get(p["parent"], 0) + 1
    return dict(files=[str(TRIAL_NODES), str(TRIAL_EDGES)], n_nodes=sum(nodes.values()), n_edges=sum(edges.values()),
                classes=classes, predicates=preds, n_own_classes=sum(not c["is_biolink"] for c in classes),
                n_own_predicates=sum(not p["is_biolink"] for p in preds),
                parents=dict(sorted(parents.items(), key=lambda kv: (-kv[1], kv[0]))))


def trial_layer_markdown(tl):
    """Takes trial_layer(). Returns the Markdown lines of the card's last section (empty list for None): why the layer
    has its own terms, then every class and predicate in the built layer with its BioLink parent IRI and count."""
    if not tl:
        return []
    par = ", ".join(f"{_local(k)} {v}" for k, v in tl["parents"].items())
    lines = ["", "## The trial layer: its own terms and their BioLink parents", "",
             f"The layer of failed drug programs ({tl['n_nodes']:,} nodes, {tl['n_edges']:,} edges; counted from "
             f"`{tl['files'][0]}` and `{tl['files'][1]}`) describes things BioLink has no specific term for: a trial arm, a "
             f"registered endpoint, an outcome result, a regulatory application. It uses BioLink terms where they exist "
             f"and {tl['n_own_classes']} classes and {tl['n_own_predicates']} predicates of its own under the prefix "
             f"`tmkg:`. The `biolink:` prefix is never put on a term BioLink does not have. Every `tmkg:` term names its "
             f"BioLink parent, written on each node as `biolink_category` and on each edge as `biolink_predicate`, so a "
             f"BioLink-only reader still gets a valid, more general graph (parents of the {tl['n_own_predicates']} own "
             f"predicates: {par}).", "",
             "| class | BioLink class or parent | parent IRI | nodes |", "|---|---|---|---|"]
    for c in tl["classes"]:
        lines.append(f"| `{c['curie']}` | {'(BioLink term)' if c['is_biolink'] else '`' + c['parent'] + '`' if c['parent'] else 'NO PARENT'} | "
                     f"{c['parent_iri'] or 'NOT IN THE BIOLINK MODEL'} | {c['n']:,} |")
    lines += ["", "| predicate | BioLink predicate or parent | parent IRI | edges |", "|---|---|---|---|"]
    for p in tl["predicates"]:
        lines.append(f"| `{p['curie']}` | {'(BioLink term)' if p['is_biolink'] else '`' + p['parent'] + '`' if p['parent'] else 'NO PARENT'} | "
                     f"{p['parent_iri'] or 'NOT IN THE BIOLINK MODEL'} | {p['n']:,} |")
    return lines


if __name__ == "__main__":
    sys.exit(main())
