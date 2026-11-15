"""
Babel identifier normalisation (P3) via the RENCI Node Normalizer (Translator), frozen through snapshot.Snapshot.

    curie -> preferred Babel id, label, BioLink types and every equivalent identifier
    e.g. ENSEMBL:ENSG00000142168 -> NCBIGene:6647 (SOD1) = HGNC:11179 = UniProtKB:P00441 = ...

Graph node ids are NOT changed (repeatable builds, existing citations). annotate() adds to each node it can map:
  babel_id, babel_label, babel_types, equivalent_ids (JSON list), and for genes hgnc / ncbigene, for drugs unii /
  drugbank / pubchem. Licensed vocabularies (MedDRA, UMLS) are left out. Nodes that normalise to the same Babel id are
  written to outputs/babel_report.csv for review - never merged automatically (CLAUDE.md rule 3).

Functions
- to_babel(node_id, category) -> Babel curie or None  ('CHEMBL:CHEMBL1117' + Drug -> 'CHEMBL.COMPOUND:CHEMBL1117')
- normalize(snap, curies)     -> {curie: dict(id, label, types, equivalents) or None}
- annotate(kg, snap)          -> (n mapped, n asked, duplicate rows)
"""
URL = "https://nodenormalization-sri.renci.org/get_normalized_nodes"
LICENSED = ("MEDDRA:", "UMLS:")           # not redistributed without a licence
CHEMICAL = {"biolink:Drug", "biolink:SmallMolecule"}
DIRECT = ("ENSEMBL:", "MONDO:", "MESH:", "EFO:", "REACT:")
XREF = {"HGNC:": "hgnc", "NCBIGene:": "ncbigene", "UNII:": "unii", "DRUGBANK:": "drugbank",
        "PUBCHEM.COMPOUND:": "pubchem", "UniProtKB:": "uniprot"}


def to_babel(node_id, category):
    if node_id.startswith("CHEMBL:") and category in CHEMICAL:
        return "CHEMBL.COMPOUND:" + node_id.split(":", 1)[1]
    if node_id.startswith(DIRECT):
        return node_id
    return None


def normalize(snap, curies, batch=200):
    out = {}
    curies = sorted(set(curies))
    for i in range(0, len(curies), batch):
        chunk = curies[i:i + batch]
        body = snap.post("nodenorm", URL, {"curies": chunk, "conflate": True, "drug_chemical_conflate": False,
                                           "description": False}).body or {}
        for c in chunk:
            v = body.get(c)
            out[c] = None if not v else dict(
                id=v["id"]["identifier"], label=v["id"].get("label"), types=(v.get("type") or [])[:3],
                equivalents=sorted({e["identifier"] for e in v.get("equivalent_identifiers", [])
                                    if not e["identifier"].startswith(LICENSED)}))
    return out


def annotate(kg, snap):
    ask = {nid: to_babel(nid, n["category"]) for nid, n in kg.nodes.items()}
    ask = {k: v for k, v in ask.items() if v}
    res = normalize(snap, ask.values())
    by_babel = {}
    for nid in sorted(ask):
        r = res.get(ask[nid])
        if not r:
            continue
        n = kg.nodes[nid]
        n["babel_id"], n["babel_label"] = r["id"], r["label"]
        n["babel_types"] = "|".join(r["types"])
        n["equivalent_ids"] = "|".join(r["equivalents"])
        for pre, key in XREF.items():
            ids = [e for e in r["equivalents"] if e.startswith(pre)]
            if ids and n["category"] in (("biolink:Gene",) if key in ("hgnc", "ncbigene", "uniprot") else CHEMICAL):
                n[key] = "|".join(ids)
        by_babel.setdefault(r["id"], []).append(nid)
    linked = {frozenset((e["subject"], e["object"])) for e in kg.edges.values() if e["predicate"] == "biolink:exact_match"}
    dups = []
    for bid, nids in sorted(by_babel.items()):
        if len(nids) > 1:
            pairs = [(a, b) for i, a in enumerate(nids) for b in nids[i + 1:]]
            for a, b in pairs:
                dups.append(dict(babel_id=bid, node_a=a, node_b=b, label=res[ask[a]]["label"],
                                 already_linked=frozenset((a, b)) in linked,
                                 action="none (exact_match edge exists)" if frozenset((a, b)) in linked
                                 else "REVIEW: same concept per Babel, not linked in the graph"))
    return sum(1 for nid in ask if res.get(ask[nid])), len(ask), dups
