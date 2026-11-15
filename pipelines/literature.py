"""
Publication metadata from Europe PMC (mirror of PubMed, reachable via www.ebi.ac.uk).

- pmids_from_text(text)        -> sorted PMIDs found in pubmed.ncbi.nlm.nih.gov links
- pmcids_from_text(text)       -> sorted PMCIDs found in PMC links
- fetch(snap, pmids)           -> {pmid: dict(title, journal, year, doi, pmcid, authors, fetched_at, key)}
- pmcid_to_pmid(snap, pmcids)  -> {pmcid: pmid}  (only when Europe PMC returns exactly one record)
- fetch_core(snap, pmids)      -> like fetch() plus abstract and open-access flag (for raw/ review files)
- fulltext_url(pmcid)          -> Europe PMC open-access full-text XML address
Requests are made in sorted chunks of 50, so the same PMID set always gives the same requests.
"""
import re

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def pmids_from_text(text):
    return sorted(set(re.findall(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", text or "")), key=int)


def pmcids_from_text(text):
    return sorted(set(re.findall(r"/(PMC\d+)", text or "")))


def _search(snap, query):
    r = snap.get("europepmc", EPMC, {"query": query, "format": "json", "resultType": "lite", "pageSize": 1000})
    return r, ((r.body or {}).get("resultList") or {}).get("result", [])


def fetch(snap, pmids):
    pmids = sorted({str(p) for p in pmids}, key=int)
    out = {}
    for i in range(0, len(pmids), 50):
        chunk = pmids[i:i + 50]
        r, rows = _search(snap, f"SRC:MED AND EXT_ID:({' OR '.join(chunk)})")
        for row in rows:
            if row.get("pmid") in chunk:
                out[row["pmid"]] = dict(title=row.get("title"), journal=row.get("journalTitle"),
                                        year=row.get("pubYear"), doi=row.get("doi"), pmcid=row.get("pmcid"),
                                        authors=row.get("authorString"), fetched_at=r.fetched_at, key=r.key)
    return out


def pmcid_to_pmid(snap, pmcids):
    out = {}
    for pmc in sorted(set(pmcids)):
        _, rows = _search(snap, f"PMCID:{pmc}")
        hits = [x for x in rows if x.get("pmid")]
        if len(hits) == 1:
            out[pmc] = hits[0]["pmid"]
    return out


def fetch_core(snap, pmids):
    """Like fetch(), but Europe PMC 'core' records: adds abstract, open-access flag and keywords.
    -> {pmid: dict(title, abstract, journal, year, doi, pmcid, authors, open_access, pub_types, fetched_at, key)}"""
    pmids = sorted({str(p) for p in pmids}, key=int)
    out = {}
    for i in range(0, len(pmids), 25):
        chunk = pmids[i:i + 25]
        r = snap.get("europepmc", EPMC, {"query": f"SRC:MED AND EXT_ID:({' OR '.join(chunk)})", "format": "json",
                                         "resultType": "core", "pageSize": 1000})
        for row in ((r.body or {}).get("resultList") or {}).get("result", []):
            if row.get("pmid") in chunk:
                ji = row.get("journalInfo") or {}
                out[row["pmid"]] = dict(
                    title=row.get("title"), abstract=row.get("abstractText"), doi=row.get("doi"),
                    journal=(ji.get("journal") or {}).get("title"), year=row.get("pubYear"),
                    pmcid=row.get("pmcid"), authors=row.get("authorString"),
                    open_access=row.get("isOpenAccess") == "Y",
                    pub_types=((row.get("pubTypeList") or {}).get("pubType") or []),
                    fetched_at=r.fetched_at, key=r.key)
    return out


def fulltext_url(pmcid):
    return f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
