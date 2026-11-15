"""
Open Targets Platform connector (GraphQL API). All calls go through a Snapshot (repeatable).

- query(snap, q, variables)            -> Response (body = GraphQL "data") ; raises on GraphQL errors
- release(snap)                        -> data release label, e.g. "26.09"
- parse_mechanisms(data)               -> list of mechanism dicts from a DRUG_MOA response
- drug_mechanisms(snap, chembl_id)     -> (Response, parse_mechanisms list)
  Used as a FALLBACK when ChEMBL has no mechanism (OT collects mechanisms across related
  ChEMBL records, e.g. salt forms).
- symbols_to_ensembl(snap, symbols)    -> (Response, {symbol: (ensembl, symbol)}) exact approved-symbol hits only
- uniprot_to_ensembl(snap, accessions) -> (Response, {accession: (ensembl gene id, symbol)}); only accessions
  with exactly ONE target hit are mapped (ambiguous ones are left out for review). Also accepts Ensembl
  gene ids (returns their symbol).
- disease_target_scores(snap, disease_id, ensembl_ids)
    -> (Response, disease name or None if OT has no such disease, [dict(ensembl, score, datatypes)])
- target_diseases(snap, ensembl_id, size)
    -> (Response, [dict(disease 'MONDO_..', name, score, datatypes)]) the target's top associated diseases
- genetic_evidence(snap, disease_id, ensembl_ids)
    -> (Response, {(ensembl, datasource): summary dict}) germline (ClinVar/EVA, ClinGen, Gene2Phenotype, Orphanet,
       UniProt, gene burden) and somatic (ClinVar somatic, COSMIC Cancer Gene Census, IntOGen) evidence, summarised
       per target and datasource: counts, clinical significances, ClinVar records, rs ids, mutated samples, PMIDs
"""
OT = "https://api.platform.opentargets.org/api/v4/graphql"

DRUG_MOA = """query($id:String!){ drug(chemblId:$id){ id name
  mechanismsOfAction{ rows{ mechanismOfAction actionType targetName
    targets{ id approvedSymbol proteinIds{ id source } }
    references{ source ids urls } } } } }"""

MAP_IDS = """query($t:[String!]!){ mapIds(queryTerms:$t, entityNames:["target"]){
  mappings{ term hits{ id name entity } } } }"""

DISEASE_TARGETS = """query($d:String!,$b:[String!]){ disease(efoId:$d){ id name
  associatedTargets(Bs:$b, page:{index:0,size:500}){ rows{ target{ id } score datatypeScores{ id score } } } } }"""


TARGET_DISEASES = """query($id:String!,$n:Int!){ target(ensemblId:$id){ id approvedSymbol
  associatedDiseases(page:{index:0,size:$n}){ count rows{ disease{ id name } score datatypeScores{ id score } } } } }"""

GENETIC_DATASOURCES = ["eva", "clingen", "gene2phenotype", "orphanet", "uniprot_variants", "uniprot_literature",
                       "gene_burden", "eva_somatic", "cancer_gene_census", "intogen"]
SOMATIC = {"eva_somatic", "cancer_gene_census", "intogen"}
EVIDENCE = """query($d:String!,$t:[String!]!,$ds:[String!]){ disease(efoId:$d){ id name
  evidences(ensemblIds:$t, datasourceIds:$ds, size:3000){ count rows{ datasourceId score target{ id }
    variantRsId studyId clinicalSignificances confidence literature directionOnTrait
    variantFunctionalConsequence{ label } mutatedSamples{ numberMutatedSamples numberSamplesTested } } } } }"""


class OpenTargetsError(RuntimeError):
    pass


def query(snap, q, variables):
    r = snap.post("opentargets", OT, {"query": q, "variables": variables})
    if not isinstance(r.body, dict) or r.body.get("errors") or "data" not in r.body:
        raise OpenTargetsError(f"Open Targets error for {variables}: {(r.body or {}).get('errors')}")
    return r


def release(snap):
    v = query(snap, "{ meta { dataVersion { year month } } }", {}).body["data"]["meta"]["dataVersion"]
    return f"{v['year']}.{v['month']}"


def parse_mechanisms(data):
    """Takes the data of a DRUG_MOA response. Returns one dict per (mechanism, target gene):
    ensembl, symbol, uniprot (Swiss-Prot accessions), moa, action_type, target_name, pmids."""
    rows = (((data or {}).get("drug") or {}).get("mechanismsOfAction") or {}).get("rows") or []
    out = []
    for m in rows:
        pmids = sorted({i for ref in m.get("references") or [] if ref.get("source") == "PubMed"
                        for i in ref.get("ids") or []})
        for t in m.get("targets") or []:
            out.append(dict(
                ensembl=t["id"], symbol=t.get("approvedSymbol"),
                uniprot=[p["id"] for p in t.get("proteinIds") or [] if p.get("source") == "uniprot_swissprot"],
                moa=m.get("mechanismOfAction"), action_type=m.get("actionType"),
                target_name=m.get("targetName"), pmids=pmids))
    return sorted(out, key=lambda m: (m["ensembl"], m["moa"] or ""))


def drug_mechanisms(snap, chembl_id):
    r = query(snap, DRUG_MOA, {"id": chembl_id})
    return r, parse_mechanisms(r.body["data"])


def uniprot_to_ensembl(snap, accessions):
    accs = sorted(set(accessions))
    if not accs:
        return None, {}
    r = query(snap, MAP_IDS, {"t": accs})
    out = {}
    for m in r.body["data"]["mapIds"]["mappings"]:
        hits = [(h["id"], h.get("name")) for h in m.get("hits") or [] if h.get("entity") == "target"]
        if len(hits) == 1:
            out[m["term"]] = hits[0]
    return r, out


GO_QUERY = """query($id:String!){ target(ensemblId:$id){ id geneOntology{ aspect evidence source term{ id label } } } }"""
GO_EXPERIMENTAL = {"EXP", "IDA", "IPI", "IMP", "IGI", "IEP", "HTP", "HDA", "HMP", "HGI", "HEP", "TAS"}


def gene_ontology(snap, ensembl_id, aspect="P"):
    """GO annotations of a target from Open Targets -> (Response, [dict(go, label, evidence codes, sources)]) for one
    aspect (P biological process, F molecular function, C cellular component), experimental / traceable-author
    evidence only (GO_EXPERIMENTAL; electronic IEA and similarity-based codes left out), one row per term."""
    r = query(snap, GO_QUERY, {"id": ensembl_id})
    rows = ((r.body["data"]["target"] or {}).get("geneOntology")) or []
    out = {}
    for x in rows:
        if x.get("aspect") == aspect and x.get("evidence") in GO_EXPERIMENTAL and (x.get("term") or {}).get("id"):
            o = out.setdefault(x["term"]["id"], dict(go=x["term"]["id"], label=x["term"].get("label"), evidence=set(),
                                                     sources=set()))
            o["evidence"].add(x["evidence"])
            if x.get("source"):
                o["sources"].add(x["source"])
    return r, [dict(o, evidence=sorted(o["evidence"]), sources=sorted(o["sources"])[:10]) for _, o in sorted(out.items())]


PHENO_QUERY = """query($d:String!){ disease(efoId:$d){ id phenotypes(page:{index:0,size:500}){ count rows{
  phenotypeHPO{ id name } evidence{ resource qualifierNot evidenceType frequencyHPO{ id name } } } } } }"""
PHENO_CURATED = {"PCS", "TAS"}          # HPO annotation evidence: published clinical study, traceable author statement


def disease_phenotypes(snap, disease_id):
    """HPO phenotypes of a disease from Open Targets (HPO annotations) -> (Response, [dict(hp 'HP:..', name,
    evidence codes, frequencies)]); curated evidence only (PCS / TAS; inferred IEA left out), negations removed."""
    r = query(snap, PHENO_QUERY, {"d": disease_id})
    rows = (((r.body["data"]["disease"] or {}).get("phenotypes")) or {}).get("rows") or []
    out = []
    for x in rows:
        ev = [e for e in x.get("evidence") or [] if e.get("resource") == "HPO" and not e.get("qualifierNot")
              and e.get("evidenceType") in PHENO_CURATED]
        hp = (x.get("phenotypeHPO") or {})
        if ev and hp.get("id", "").startswith("HP_"):
            out.append(dict(hp=hp["id"].replace("_", ":"), name=hp.get("name"),
                            evidence=sorted({e["evidenceType"] for e in ev}),
                            frequencies=sorted({(e.get("frequencyHPO") or {}).get("name") for e in ev} - {None})))
    return r, sorted(out, key=lambda o: o["hp"])


def symbols_to_ensembl(snap, symbols):
    """Approved gene symbols -> (Response, {symbol: (ensembl id, symbol)}): the one target hit whose approved symbol
    equals the query exactly (case-insensitive); symbols with zero or several such hits are left out."""
    syms = sorted(set(symbols))
    if not syms:
        return None, {}
    r = query(snap, MAP_IDS, {"t": syms})
    out = {}
    for m in r.body["data"]["mapIds"]["mappings"]:
        hits = [(h["id"], h.get("name")) for h in m.get("hits") or []
                if h.get("entity") == "target" and (h.get("name") or "").upper() == m["term"].upper()]
        if len(hits) == 1:
            out[m["term"]] = hits[0]
    return r, out


def disease_target_scores(snap, disease_id, ensembl_ids):
    """disease_id in OT form, e.g. 'MONDO_0004975'."""
    r = query(snap, DISEASE_TARGETS, {"d": disease_id, "b": sorted(set(ensembl_ids))})
    d = r.body["data"]["disease"]
    if not d:
        return r, None, []
    rows = [dict(ensembl=x["target"]["id"], score=round(x["score"], 4),
                 datatypes={t["id"]: round(t["score"], 4) for t in x["datatypeScores"]})
            for x in d["associatedTargets"]["rows"]]
    return r, d["name"], sorted(rows, key=lambda x: x["ensembl"])


def target_diseases(snap, ensembl_id, size=25):
    """Top `size` diseases associated with a target (Open Targets overall association score), best first."""
    r = query(snap, TARGET_DISEASES, {"id": ensembl_id, "n": size})
    t = r.body["data"]["target"]
    rows = ((t or {}).get("associatedDiseases") or {}).get("rows") or []
    return r, [dict(disease=x["disease"]["id"], name=x["disease"]["name"], score=round(x["score"], 4),
                    datatypes={d["id"]: round(d["score"], 4) for d in x["datatypeScores"]}) for x in rows]


def summarise_evidence(rows):
    """Evidence rows -> {(ensembl, datasource): dict(n_evidence, max_score, clinical_significance{label: n},
    clinvar_records[:10], variants[:10], mutated_samples [n, tested] (largest tested), consequences, pmids[:10],
    direction{label: n})}. Only values present in the rows; lists sorted, truncated lists say so via n_*."""
    out = {}
    for x in rows:
        k = (x["target"]["id"], x["datasourceId"])
        o = out.setdefault(k, dict(n_evidence=0, max_score=0.0, clinical_significance={}, clinvar_records=set(),
                                   variants=set(), mutated_samples=None, consequences=set(), pmids=set(), direction={}))
        o["n_evidence"] += 1
        o["max_score"] = max(o["max_score"], round(x.get("score") or 0, 4))
        for c in x.get("clinicalSignificances") or []:
            o["clinical_significance"][c] = o["clinical_significance"].get(c, 0) + 1
        if x.get("studyId") and str(x["studyId"]).startswith(("RCV", "VCV", "SCV")):
            o["clinvar_records"].add(x["studyId"])
        if x.get("variantRsId"):
            o["variants"].add(x["variantRsId"])
        if (x.get("variantFunctionalConsequence") or {}).get("label"):
            o["consequences"].add(x["variantFunctionalConsequence"]["label"])
        if x.get("directionOnTrait"):
            o["direction"][x["directionOnTrait"]] = o["direction"].get(x["directionOnTrait"], 0) + 1
        for m in x.get("mutatedSamples") or []:
            pair = [m.get("numberMutatedSamples"), m.get("numberSamplesTested")]
            if None not in pair and (o["mutated_samples"] is None or pair[1] > o["mutated_samples"][1]):
                o["mutated_samples"] = pair
        o["pmids"] |= set(x.get("literature") or [])
    for o in out.values():
        o["n_clinvar_records"], o["n_variants"] = len(o["clinvar_records"]), len(o["variants"])
        for f in ("clinvar_records", "variants", "pmids"):
            o[f] = sorted(o[f], key=lambda v: (len(v), v))[:10]
        o["consequences"] = sorted(o["consequences"])
        o["clinical_significance"] = dict(sorted(o["clinical_significance"].items()))
        o["direction"] = dict(sorted(o["direction"].items()))
    return out


def genetic_evidence(snap, disease_id, ensembl_ids):
    """Germline + somatic evidence for these targets in one disease (OT form 'MONDO_0004976')."""
    r = query(snap, EVIDENCE, {"d": disease_id, "t": sorted(set(ensembl_ids)), "ds": GENETIC_DATASOURCES})
    d = r.body["data"]["disease"]
    if not d:
        return r, {}
    ev = d["evidences"]
    out = summarise_evidence(ev["rows"])
    if ev["count"] > len(ev["rows"]):
        for o in out.values():
            o["truncated"] = f"{len(ev['rows'])} of {ev['count']} evidence rows read"
    return r, out
