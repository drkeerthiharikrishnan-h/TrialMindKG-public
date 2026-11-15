"""
Finds the public documents and data behind each failed program (used by raw_docs.py).
All requests go through a Snapshot, so a discovery run can be repeated offline.

Literature (Europe PMC)
- trial_literature(snap, ncts)            -> publications that cite one of the NCT numbers, reviews excluded
                                             ("trial publications": papers, conference abstracts, preprints)
- mentions(snap, ncts, acronyms, drugs)   -> every record citing an NCT or naming acronym+drug (incl. reviews),
                                             metadata only
- epmc_key(rec)                           -> stable file key: 'PMID_<id>' for PubMed records, else '<SRC>_<id>'
- category_of(rec)                        -> 'conference abstract' | 'preprint' | 'publication'
- parse_fulltext(xml)                     -> dict(supplements, figures, data_availability) from JATS XML
- accessions(snap, pmcids)                -> data accession numbers text-mined from articles (GEO, EGA, ...)
Regulatory / safety / registries
- fda_applications(snap, ingredient)      -> Drugs@FDA applications with their posted documents
- faers(snap, name)                       -> FAERS report total + top reactions (all indications, not trial-specific)
- other_registries(study)                 -> EU CTR / CTIS / other secondary registry ids with search links
"""
import re
import xml.etree.ElementTree as ET

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ANNOT = "https://www.ebi.ac.uk/europepmc/annotations_api/annotationsByArticleIds"
FDA = "https://api.fda.gov/drug/"
XL = "{http://www.w3.org/1999/xlink}href"
# identifiers.org prefixes that point at data (omics, imaging, code, archives); others (genes, PDB, NCT) are ignored
DATA_PREFIXES = {"geo", "arrayexpress", "ega.study", "ega.dataset", "dbgap", "pride", "bioproject", "biosample",
                 "sra", "ena.embl", "biostudies", "metabolights", "massive", "zenodo", "figshare", "dryad",
                 "synapse", "openneuro"}       # 'doi' excluded: text-mined DOIs are mostly citations, not data


def _search_all(snap, query, result_type):
    """All Europe PMC records for a query (cursor paging, 1000 per page)."""
    out, cursor = [], "*"
    while True:
        r = snap.get("europepmc", EPMC, {"query": query, "format": "json", "resultType": result_type,
                                         "pageSize": 1000, "cursorMark": cursor})
        body = r.body or {}
        rows = (body.get("resultList") or {}).get("result", [])
        out += [dict(x, _fetched_at=r.fetched_at, _key=r.key) for x in rows]
        nxt = body.get("nextCursorMark")
        if not rows or not nxt or nxt == cursor:
            return out
        cursor = nxt


def _nct_clause(ncts):
    return "(" + " OR ".join(f'"{n}"' for n in sorted(ncts)) + ")"


def trial_literature(snap, ncts):
    return _search_all(snap, _nct_clause(ncts) + ' NOT PUB_TYPE:"review"', "core") if ncts else []


def mentions(snap, ncts, acronyms, drugs):
    parts = [_nct_clause(ncts)] if ncts else []
    if acronyms and drugs:
        parts.append("((" + " OR ".join(f'"{a}"' for a in acronyms) + ") AND (" +
                     " OR ".join(f'"{d}"' for d in drugs) + "))")
    return _search_all(snap, " OR ".join(parts), "lite") if parts else []


def epmc_key(rec):
    return f"PMID_{rec['pmid']}" if rec.get("pmid") else f"{rec.get('source')}_{rec.get('id')}"


def category_of(rec):
    pt = (rec.get("pubType") or "").lower() if isinstance(rec.get("pubType"), str) else \
        " ".join((rec.get("pubTypeList") or {}).get("pubType") or []).lower()
    if rec.get("source") == "PPR" or "preprint" in pt:
        return "preprint"
    if "abstract" in pt and "journal article" not in pt or "congress" in pt or "meeting" in pt:
        return "conference abstract"
    return "publication"


def parse_fulltext(xml_bytes):
    """-> dict(supplements=[dict(file, mimetype, label, caption)], figures=[dict(file, label, caption)],
    data_availability=text or '')."""
    root = ET.fromstring(xml_bytes)
    txt = lambda e: " ".join("".join(e.itertext()).split()) if e is not None else ""
    supps = []
    for s in root.iter("supplementary-material"):
        for m in (s.findall(".//media") or [s]):
            f = m.get(XL)
            if f:
                mt = "/".join(x for x in (m.get("mimetype"), m.get("mime-subtype")) if x)
                supps.append(dict(file=f, mimetype=mt, label=txt(s.find("label")), caption=txt(s.find("caption"))[:300]))
    figs = []
    for fig in root.iter("fig"):
        g = fig.find(".//graphic")
        if g is not None and g.get(XL):
            figs.append(dict(file=g.get(XL), label=txt(fig.find("label")), caption=txt(fig.find("caption"))[:300]))
    da = []
    for sec in root.iter("sec"):
        title = txt(sec.find("title")).lower()
        if sec.get("sec-type") in ("data-availability", "data-sharing") or any(
                k in title for k in ("data availab", "data sharing", "availability of data")):
            da.append(txt(sec))
    for n in root.iter("notes"):
        if n.get("notes-type") in ("data-availability", "data-sharing"):
            da.append(txt(n))
    return dict(supplements=supps, figures=figs, data_availability=" | ".join(dict.fromkeys(da))[:2000])


def supplement_kind(name):
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("xlsx", "xls", "csv", "tsv", "txt", "sav", "sas7bdat", "dta", "rds", "json"):
        return "data table"
    if ext in ("pdf", "doc", "docx"):
        return "document (protocol / appendix / tables)"
    if ext in ("ppt", "pptx"):
        return "slides"
    if ext in ("jpg", "jpeg", "png", "tif", "tiff", "gif", "eps"):
        return "image"
    if ext in ("mp4", "mov", "avi"):
        return "video"
    if ext in ("zip", "gz", "tar", "7z"):
        return "archive"
    return "other"


def accessions(snap, pmcids):
    """-> {pmcid: [dict(db, accession, uri)]} for data-type accession numbers (DATA_PREFIXES)."""
    out = {}
    ids = sorted(set(pmcids))
    for i in range(0, len(ids), 8):
        chunk = ids[i:i + 8]
        r = snap.get("europepmc", ANNOT, {"articleIds": ",".join(f"PMC:{p}" for p in chunk),
                                          "type": "Accession Numbers", "format": "JSON"})
        for art in r.body or []:
            pmc = art.get("pmcid")
            for a in art.get("annotations", []):
                for t in a.get("tags", []):
                    m = re.match(r"https?://identifiers\.org/([^:/]+)[:/](.+)", t.get("uri") or "")
                    if m and m.group(1).lower() in DATA_PREFIXES:
                        rec = dict(db=m.group(1).lower(), accession=m.group(2), uri=t["uri"])
                        if rec not in out.setdefault(pmc, []):
                            out[pmc].append(rec)
    return out


def fda_applications(snap, ingredient):
    """ingredient: ChEMBL preferred name. Single word -> prefix match (handles '-avwa' suffixes);
    several words -> exact. -> (Response, [dict(application, sponsor, brand, docs=[dict(type, date, url)])]); NDA/BLA only."""
    name = ingredient.upper()
    q = f"products.active_ingredients.name:{name}*" if " " not in name else f'products.active_ingredients.name:"{name}"'
    r = snap.get("openfda", FDA + "drugsfda.json", {"search": q, "limit": 100})
    apps = []
    for a in (r.body or {}).get("results", []):
        if not str(a.get("application_number", "")).startswith(("NDA", "BLA")):
            continue                                  # skip generic copies (ANDA)
        docs = sorted(({"type": d.get("type"), "date": d.get("date"),
                        "url": d.get("url").replace("http://", "https://", 1)}
                       for s in a.get("submissions", []) for d in s.get("application_docs", []) if d.get("url")),
                      key=lambda d: (d["date"] or "", d["url"]))
        brands = sorted({p.get("brand_name") for p in a.get("products", []) if p.get("brand_name")})
        apps.append(dict(application=a.get("application_number"), sponsor=a.get("sponsor_name"),
                         brand="; ".join(brands), docs=docs))
    return r, sorted(apps, key=lambda a: a["application"])


def faers(snap, name, top=25):
    """FAERS reports naming the drug. Tries the harmonised generic name, then the free-text product name
    (catches investigational products). -> dict(query_field, total, top_reactions[(term, count)], fetched_at, key)"""
    for field in ("patient.drug.openfda.generic_name", "patient.drug.medicinalproduct"):
        q = f'{field}:"{name.upper()}"'
        r = snap.get("openfda", FDA + "event.json", {"search": q, "limit": 1})
        total = ((r.body or {}).get("meta") or {}).get("results", {}).get("total")
        if total:
            c = snap.get("openfda", FDA + "event.json",
                         {"search": q, "count": "patient.reaction.reactionmeddrapt.exact", "limit": top})
            return dict(query_field=field, total=total,
                        top_reactions=[(x["term"], x["count"]) for x in (c.body or {}).get("results", [])],
                        fetched_at=c.fetched_at, key=c.key)
    return dict(query_field=None, total=0, top_reactions=[], fetched_at=r.fetched_at, key=r.key)


REGISTRY_LINKS = {
    "EUDRACT_NUMBER": "https://www.clinicaltrialsregister.eu/ctr-search/search?query={}",
    "CTIS": "https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT={}",
}


def other_registries(study):
    """Secondary ids from the ClinicalTrials.gov record -> [dict(type, id, link)] (link only for EU registries)."""
    idm = (study or {}).get("protocolSection", {}).get("identificationModule", {})
    out = []
    for s in idm.get("secondaryIdInfos", []):
        t = s.get("type") or "OTHER"
        tmpl = REGISTRY_LINKS.get(t)
        out.append(dict(type=t, id=s.get("id"), domain=s.get("domain"), link=tmpl.format(s.get("id")) if tmpl else ""))
    return out
