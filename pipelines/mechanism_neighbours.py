"""
Mechanism neighbours (CC-23): multihop context on gene, protein and disease cards, built once into the card index.

No edge is written to the graph. The card only walks stored edges and shows each hop's edge id, so "why" resolves
every hop:
  gene card     gene -has_gene_product-> protein; the protein's family (Drug Target Ontology class at the most
                specific level that has at least one other member); up to MAX_MEMBERS other family members that are
                mechanism targets (a SmallMolecule -> protein/gene edge with the `mechanism` property set: ChEMBL 37
                mechanisms, Open Targets drug mechanisms; activities alone do not count); per member up to MAX_DRUGS
                drugs (name, action type, max phase) and per drug up to MAX_INDICATIONS indications (MONDO diseases).
  disease card  drugs indicated for it (treats_or_applied_or_studied_to_treat, max phase), up to MAX_DISEASE_DRUGS;
                per drug up to MAX_TARGETS mechanism targets and each target's family.
The family membership comes from member_of edges to DTO classes when a layer holds them (edge id shown), else from
data/pharos/dto.tsv.gz (the Pharos DTO table; the hop then names the table, edge id None). No HGNC gene-group table
is committed, so there is no gene-group fallback yet.

Functions (plain pandas / dict in and out):
  collect(chunk)                       -> dict of small frames (gene products, mechanisms, indications, memberships)
  read_dto(path) / read_dto_frame(df)  -> DataFrame protein, family, family_name (empty when the file is missing)
  families(mem, dto, labels)           -> ({protein: dict(id, name, size, edge, source)}, {family: members})
  build_contexts(parts, labels, dto)   -> {node id: payload dict} for every gene / protein / disease with something to show
  edge_ids(contexts)                   -> set of every edge id the payloads cite (the card index stores those edges)
  empty_context(node_id, category)     -> the empty-state payload, or None for categories without this section
"""
from pathlib import Path

import pandas as pd

MAX_MEMBERS, MAX_DRUGS, MAX_INDICATIONS = 10, 3, 3
MAX_DISEASE_DRUGS, MAX_TARGETS = 10, 3
GENE_PRODUCT = "biolink:has_gene_product"
INDICATION = "biolink:treats_or_applied_or_studied_to_treat"
MEMBER_OF = "biolink:member_of"
DRUG_PREFIX = "CHEMBL.COMPOUND:"
DTO_TABLE = Path("data") / "pharos" / "dto.tsv.gz"
NOTE_GENE = "Context, not evidence that this gene causes the disease."
NOTE_DISEASE = "Context, not evidence that these proteins cause the disease."
EMPTY_GENE = ("No drug in the loaded layers has a recorded mechanism of action on another member of this gene's "
              "protein family.")
EMPTY_DISEASE = "No drug in the loaded layers is indicated for this disease with a recorded mechanism of action."
GENE_CATS = {"biolink:Gene", "biolink:Protein"}
DISEASE_CATS = {"biolink:Disease", "biolink:DiseaseOrPhenotypicFeature"}
_COLS = {"gp": ["id", "subject", "object"], "mech": ["id", "subject", "object", "action_type", "max_phase"],
         "ind": ["id", "subject", "object", "phase"], "mem": ["id", "subject", "object"]}


def _col(c, name):
    return c[name].astype("string").fillna("") if name in c else pd.Series("", index=c.index, dtype="string")


def collect(chunk):
    """Takes one edge chunk (id, subject, predicate, object, and the property columns mechanism, action_type, max_phase,
    max_phase_for_ind when the layer has them). Returns {gp, mech, ind, mem}: the rows this module needs, small frames."""
    pred, subj, obj = _col(chunk, "predicate"), _col(chunk, "subject"), _col(chunk, "object")
    is_drug = subj.str.startswith(DRUG_PREFIX)
    phase = _col(chunk, "max_phase_for_ind").where(_col(chunk, "max_phase_for_ind") != "", _col(chunk, "max_phase"))
    c = chunk.assign(action_type=_col(chunk, "action_type"), max_phase=_col(chunk, "max_phase"), phase=phase)
    return dict(gp=c.loc[pred == GENE_PRODUCT, _COLS["gp"]],
                mech=c.loc[is_drug & (_col(chunk, "mechanism") != "") & (pred != INDICATION), _COLS["mech"]],
                ind=c.loc[is_drug & (pred == INDICATION) & obj.str.startswith("MONDO:"), _COLS["ind"]],
                mem=c.loc[(pred == MEMBER_OF) & obj.str.startswith("DTO:"), _COLS["mem"]])


def concat_parts(parts):
    """Takes a list of collect() results. Returns one dict of frames (first edge id kept per id)."""
    return {k: (pd.concat([p[k] for p in parts], ignore_index=True).drop_duplicates("id") if parts
                else pd.DataFrame(columns=v)) for k, v in _COLS.items()}


def read_dto(path):
    """Takes the Pharos DTO table path (data/pharos/dto.tsv.gz: UniProt, DTO ID, DTO Class). Returns a DataFrame
    protein (UniProtKB:...), family (DTO:...), family_name; empty when the file does not exist."""
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=["protein", "family", "family_name"])
    return read_dto_frame(pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False,
                                      usecols=["UniProt", "DTO ID", "DTO Class"]))


def read_dto_frame(t):
    """Takes the DTO table as a DataFrame (UniProt, DTO ID, DTO Class). Returns protein, family, family_name rows
    (rows without a UniProt accession or a DTO ID dropped)."""
    t = t.fillna("")
    t = t[(t["UniProt"] != "") & (t["DTO ID"] != "")]
    return pd.DataFrame({"protein": "UniProtKB:" + t["UniProt"], "family": t["DTO ID"],
                         "family_name": t["DTO Class"]}).drop_duplicates(["protein", "family"]).reset_index(drop=True)


def families(mem, dto, labels):
    """Takes member_of edges (id, subject = protein, object = DTO class), the DTO table (read_dto) and node labels.
    Returns {protein: dict(id, name, size, edge, source)}: the protein's DTO class with the FEWEST member proteins
    among those with at least 2 members (a class that holds only the protein itself says nothing about neighbours);
    ties by class id. size = member proteins of the class; edge = the member_of edge id when the graph holds one.
    Also returns {family id: sorted list of member proteins}."""
    rows = pd.concat([pd.DataFrame({"protein": mem["subject"], "family": mem["object"], "edge": mem["id"]}),
                      dto.assign(edge=None)[["protein", "family", "edge"]]], ignore_index=True)
    rows = rows.drop_duplicates(["protein", "family"], keep="first")
    names = dict(zip(dto["family"], dto["family_name"]))
    members = {f: sorted(g["protein"]) for f, g in rows.groupby("family")}
    size = {f: len(m) for f, m in members.items()}
    rows = rows.assign(size=rows["family"].map(size))
    rows = rows[rows["size"] >= 2].sort_values(["protein", "size", "family"], kind="mergesort")
    out = {}
    for r in rows.drop_duplicates("protein").itertuples(index=False):
        out[r.protein] = dict(id=r.family, name=labels.get(r.family) or names.get(r.family) or r.family, size=int(r.size),
                              edge=None if pd.isna(r.edge) else r.edge,
                              source="graph edge" if not pd.isna(r.edge) else "Drug Target Ontology table (Pharos)")
    return out, members


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None      # NaN / inf -> None (JSON-safe)


def _phase_key(p, label, nid):
    v = _num(p)
    return (-(v if v is not None else -1.0), label.lower(), nid)


def build_contexts(parts, labels, dto):
    """Takes concat_parts(...) frames, node labels ({id: label}) and the DTO table. Returns {node id: payload} for every
    gene / protein whose family has a mechanism-target member and every disease with an indicated mechanism drug."""
    gp, mech, ind, mem = parts["gp"], parts["mech"], parts["ind"], parts["mem"]
    L = lambda x: labels.get(x) or x
    fam, members = families(mem, dto, labels)
    medge = {}                                                 # (protein, family) -> first member_of edge id
    for r in mem.sort_values("id").itertuples(index=False):
        medge.setdefault((r.subject, r.object), r.id)
    product = {}                                               # gene -> [(protein, edge id)]
    for r in gp.sort_values(["subject", "object", "id"]).itertuples(index=False):
        product.setdefault(r.subject, []).append((r.object, r.id))
    # mechanism target -> protein: a protein target is itself; a gene target maps through its gene products
    by_protein = {}                                            # protein -> drug -> [hop dicts]
    for r in mech.sort_values(["object", "subject", "id"]).itertuples(index=False):
        hops = [(r.object, [dict(edge=r.id, statement=f"{L(r.subject)} acts on {L(r.object)}")])]
        if r.object in product:
            hops = [(p, [dict(edge=r.id, statement=f"{L(r.subject)} acts on {L(r.object)}"),
                         dict(edge=e, statement=f"{L(r.object)} encodes {L(p)}")]) for p, e in product[r.object]]
        for p, h in hops:
            d = by_protein.setdefault(p, {}).setdefault(r.subject, dict(action_type=set(), max_phase=None, hops=[]))
            if r.action_type:
                d["action_type"].add(r.action_type)
            v = _num(r.max_phase)
            if v is not None and (d["max_phase"] is None or v > d["max_phase"]):
                d["max_phase"] = v
            d["hops"] += [x for x in h if x["edge"] not in {y["edge"] for y in d["hops"]}]
    indications = {}                                           # drug -> [dict(id, label, max_phase, edge)]
    for r in ind.itertuples(index=False):
        indications.setdefault(r.subject, []).append(dict(id=r.object, label=L(r.object), max_phase=_num(r.phase),
                                                          edge=r.id))
    for k, v in indications.items():
        v.sort(key=lambda x: _phase_key(x["max_phase"], x["label"], x["id"]))

    def drug_entry(drug, d):
        best = max([x["max_phase"] for x in indications.get(drug, []) if x["max_phase"] is not None] or [None],
                   key=lambda x: -1 if x is None else x)
        mp = d["max_phase"] if d["max_phase"] is not None else best
        return dict(id=drug, label=L(drug), action_type=", ".join(sorted(d["action_type"])) or None, max_phase=mp,
                    hops=d["hops"], indications=indications.get(drug, [])[:MAX_INDICATIONS],
                    n_indications=len(indications.get(drug, [])))

    member_cache = {}

    def member_row(m, fid):
        if (m, fid) not in member_cache:
            drugs = sorted((drug_entry(d, x) for d, x in by_protein[m].items()),
                           key=lambda x: _phase_key(x["max_phase"], x["label"], x["id"]))
            member_cache[(m, fid)] = dict(id=m, label=L(m), family_edge=medge.get((m, fid)), n_drugs=len(drugs),
                                          drugs=drugs[:MAX_DRUGS])
        return member_cache[(m, fid)]

    out = {}
    # gene / protein cards
    starts = {p: [] for p in fam}                              # node -> [(protein, hops to reach it)]
    for g, ps in product.items():
        for p, e in ps:
            if p in fam:
                starts.setdefault(g, []).append((p, [dict(edge=e, statement=f"{L(g)} encodes {L(p)}")]))
    for p in fam:
        starts[p] = [(p, [])]
    for node, plist in starts.items():
        if not plist:
            continue
        p, lead = plist[0]
        f = fam[p]
        cand = [m for m in members[f["id"]] if m != p and m in by_protein]
        cand.sort(key=lambda m: (-len(by_protein[m]), L(m).lower(), m))
        if not cand:
            continue
        rows = [member_row(m, f["id"]) for m in cand[:MAX_MEMBERS]]
        out[node] = dict(kind="gene", header=(f"Drugs that act on other members of this gene's family ({f['name']}) "
                                               f"and what they treat"),
                         note=NOTE_GENE, family=dict(f, edge=medge.get((p, f["id"]))), protein=p,
                         protein_label=L(p), lead=lead, members=rows, n_members=len(cand), empty=None)
    # disease cards
    drugs_for = {}
    for drug, inds in indications.items():
        for x in inds:
            drugs_for.setdefault(x["id"], []).append((drug, x))
    targets_of = {}
    for p, ds in by_protein.items():
        for drug, d in ds.items():
            targets_of.setdefault(drug, []).append((p, d))
    for dis, lst in drugs_for.items():
        lst = [(drug, x) for drug, x in lst if drug in targets_of]
        if not lst:
            continue
        lst.sort(key=lambda t: _phase_key(t[1]["max_phase"], L(t[0]), t[0]))
        rows = []
        for drug, x in lst[:MAX_DISEASE_DRUGS]:
            tg = sorted(targets_of[drug], key=lambda t: (L(t[0]).lower(), t[0]))
            rows.append(dict(id=drug, label=L(drug), max_phase=x["max_phase"], edge=x["edge"], n_targets=len(tg),
                             targets=[dict(id=p, label=L(p), action_type=", ".join(sorted(d["action_type"])) or None,
                                           hops=d["hops"], family=_fam_short(fam.get(p)))
                                      for p, d in tg[:MAX_TARGETS]]))
        out[dis] = dict(kind="disease", header=(f"Drugs indicated for {L(dis)}, the proteins they act on, and those "
                                                 f"proteins' families"),
                        note=NOTE_DISEASE, drugs=rows, n_drugs=len(lst), empty=None)
    return out


def _fam_short(f):
    return None if not f else dict(id=f["id"], name=f["name"], size=f["size"])


def edge_ids(contexts):
    """Takes build_contexts(...). Returns the set of every edge id the payloads cite."""
    ids = set()
    for c in contexts.values():
        ids.update(h["edge"] for h in c.get("lead", []))
        for e in ((c.get("family") or {}).get("edge"),):
            ids.add(e)
        for m in c.get("members", []):
            ids.add(m.get("family_edge"))
            for d in m["drugs"]:
                ids.update(h["edge"] for h in d["hops"])
                ids.update(x["edge"] for x in d["indications"])
        for d in c.get("drugs", []):
            ids.add(d["edge"])
            for t in d["targets"]:
                ids.update(h["edge"] for h in t["hops"])
    return {i for i in ids if i}


def empty_context(category):
    """Takes a node category. Returns the empty-state payload for genes / proteins / diseases, None otherwise."""
    if category in GENE_CATS:
        return dict(kind="gene", header="Drugs that act on other members of this gene's family", note=NOTE_GENE,
                    members=[], n_members=0, empty=EMPTY_GENE)
    if category in DISEASE_CATS:
        return dict(kind="disease", header="Drugs indicated for this disease and the proteins they act on",
                    note=NOTE_DISEASE, drugs=[], n_drugs=0, empty=EMPTY_DISEASE)
    return None


def pack(contexts):
    """Takes build_contexts(...). Returns (nodes, parts) for storage: nodes = {node: payload with each family member
    replaced by its key 'protein|family'}, parts = {key: member row}. A member row is shared by every gene / protein of
    the family, so it is stored once (the card index stays small)."""
    nodes, parts = {}, {}
    for nid, c in contexts.items():
        if c.get("kind") == "gene":
            fid = c["family"]["id"]
            keys = []
            for m in c["members"]:
                k = f"{m['id']}|{fid}"
                parts.setdefault(k, m)
                keys.append(k)
            c = dict(c, members=keys)
        nodes[nid] = c
    return nodes, parts


def unpack(payload, part):
    """Takes a stored node payload and a function key -> member row. Returns the payload as build_contexts gave it."""
    if payload.get("kind") == "gene" and payload.get("members") and isinstance(payload["members"][0], str):
        payload = dict(payload, members=[part(k) for k in payload["members"]])
    return payload
