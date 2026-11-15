"""
PharmGKB / ClinPGx clinical annotations (P4): gene variants that change a drug's efficacy, toxicity, dosage or
metabolism, with the PharmGKB level of evidence (1A, 1B, 2A, 2B, 3, 4). All calls frozen through snapshot.Snapshot.

    api.clinpgx.org (the PharmGKB API, renamed ClinPGx; api.pharmgkb.org is not reachable from the cloud session)

Functions
- annotations(snap, drug_name) -> (Response, [dict(accession, level, types, variant, genes, diseases, chemicals,
                                    score, name)])  [] when ClinPGx has none (HTTP 404 is stored as 'no record')
- LEVELS                       -> evidence levels, strongest first (1A = guideline / FDA label annotated variant)
"""
URL = "https://api.clinpgx.org/v1/data/clinicalAnnotation"
LEVELS = ["1A", "1B", "2A", "2B", "3", "4"]


def annotations(snap, drug_name):
    r = snap.get("clinpgx", URL, {"relatedChemicals.name": drug_name.lower(), "view": "base"})
    body = r.body if isinstance(r.body, dict) else {}
    rows = body.get("data") if body.get("status") == "success" else []
    out = []
    for a in rows if isinstance(rows, list) else []:
        loc = a.get("location") or {}
        out.append(dict(accession=a.get("accessionId"), level=(a.get("levelOfEvidence") or {}).get("term"),
                        types=sorted(a.get("types") or []), variant=loc.get("displayName"),
                        genes=sorted(g.get("symbol") for g in loc.get("genes") or [] if g.get("symbol")),
                        diseases=sorted(d.get("name") for d in a.get("relatedDiseases") or [] if d.get("name")),
                        chemicals=sorted(c.get("name") for c in a.get("relatedChemicals") or [] if c.get("name")),
                        score=a.get("score"), name=a.get("name")))
    return r, sorted(out, key=lambda x: (LEVELS.index(x["level"]) if x["level"] in LEVELS else 9, x["accession"] or ""))
