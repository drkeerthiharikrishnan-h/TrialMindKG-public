"""
Reactome connector (ContentService): protein -> human pathways.

- version(snap)              -> Reactome database version (e.g. 97), from the API
- pathways(snap, id, db)     -> (Response, [dict(id 'R-HSA-..', name)]) sorted by id; [] if none
"""
RC = "https://reactome.org/ContentService"


def version(snap):
    return snap.get("reactome", f"{RC}/data/database/version").body


def pathways(snap, identifier, db="UniProt"):
    """db: 'UniProt' (protein accession) or 'ENSEMBL' (gene id, used for RNA targets)."""
    r = snap.get("reactome", f"{RC}/data/mapping/{db}/{identifier}/pathways", {"species": "9606"})
    rows = r.body if isinstance(r.body, list) else []
    return r, sorted(({"id": p["stId"], "name": (p.get("displayName") or "").strip()} for p in rows
                      if str(p.get("stId", "")).startswith("R-HSA-")), key=lambda p: p["id"])
