"""
GWAS Catalog connector (P11): genome-wide associations whose mapped gene is one of our target genes. Frozen through
snapshot.Snapshot (REST API v2).

- associations(snap, symbol, p_max) -> (Responses, [dict(p_value, traits [(id 'EFO:..'/'MONDO:..', label)], snps,
                                        study, pmid)])   genome-wide significant (p <= p_max) only
"""
GWAS = "https://www.ebi.ac.uk/gwas/rest/api/v2/associations"
GENOME_WIDE = 5e-8
PAGE = 200


def _curie(efo_id):
    pre, _, num = (efo_id or "").partition("_")
    return f"{pre}:{num}" if pre and num else None


def associations(snap, symbol, p_max=GENOME_WIDE):
    responses, out, page = [], [], 0
    while True:
        r = snap.get("gwas", GWAS, {"mapped_gene": symbol, "size": PAGE, "page": page, "sort": "p_value",
                                   "direction": "asc"})
        responses.append(r)
        body = r.body or {}
        rows = (body.get("_embedded") or {}).get("associations") or []
        for a in rows:
            p = a.get("p_value")
            if p is None or p > p_max:
                continue
            out.append(dict(p_value=p, traits=[(_curie(t.get("efo_id")), t.get("efo_trait")) for t in a.get("efo_traits") or []],
                            snps=sorted(set(a.get("snp_effect_allele") or a.get("snp_allele") or [])),
                            study=a.get("accession_id"), pmid=a.get("pubmed_id")))
        info = body.get("page") or {}
        page += 1
        if not rows or page >= (info.get("totalPages") or 0) or (rows and (rows[-1].get("p_value") or 1) > p_max):
            break
    return responses, out
