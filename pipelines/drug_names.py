"""
External drug-name dictionaries (exact lookups, every call frozen through snapshot.Snapshot).

A registry drug name / research code / trade name -> other names of the SAME substance, from:
  pubchem  PubChem PUG-REST compound/name/<name>/synonyms   (exact name index; many company codes)
  gsrs     FDA Global Substance Registration System search   (only substances that list the name exactly)
  nameres  RENCI Name Resolver (Translator / Babel synonyms)  (fuzzy search - a hit counts ONLY if the exact
                                                               name is among its synonyms)
These names are never identifiers in the graph by themselves: the resolver accepts a molecule only when one of them
is EXACTLY a ChEMBL preferred name or synonym (CLAUDE.md rule 1), and every proposal still goes to the curator.

Functions
- pubchem(snap, name)  -> [names]          - gsrs(snap, name) -> [names]          - nameres(snap, name) -> [names]
- other_names(snap, name, sources) -> {source: [names]}   (the query itself removed; [] when nothing found)
- snapshot()           -> Snapshot over data/snapshots_names/ (kept apart so pruning the graph build's unused
                          responses never deletes them)
"""
import re
from pathlib import Path
from urllib.parse import quote

SNAP_DIR = Path(__file__).resolve().parents[1] / "data" / "snapshots_names"   # own folder: not part of the graph build
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/synonyms/JSON"
GSRS = "https://gsrs.ncats.nih.gov/api/v1/substances/search"
NAMERES = "https://name-resolution-sri.renci.org/lookup"
SOURCES = ("pubchem", "gsrs", "nameres")
MAX_NAMES = 60                       # PubChem lists hundreds of names (IUPAC, vendors); the first ones are the useful


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _safe(snap, source, url, params=None):
    try:
        return snap.get(source, url, params).body
    except Exception:                # outage / timeout: channel unused for this name (never stored, never guessed)
        return None


def pubchem(snap, name):
    body = _safe(snap, "pubchem", PUBCHEM.format(quote(name, safe="")))
    info = ((body or {}).get("InformationList") or {}).get("Information") or []
    return [s for i in info[:3] for s in (i.get("Synonym") or [])[:MAX_NAMES]]


def gsrs(snap, name):
    """Substances whose own name list contains the name exactly (search by name field, full view)."""
    body = _safe(snap, "gsrs", GSRS, {"q": f'root_names_name:"{name}"', "top": 5, "view": "full"})
    out = []
    for c in (body or {}).get("content") or []:
        names = [c.get("_name")] + [n.get("name") for n in c.get("names") or [] if isinstance(n, dict)]
        if norm(name) in {norm(n) for n in names}:
            out += names[:MAX_NAMES]
    return [n for n in out if n]


def nameres(snap, name):
    body = _safe(snap, "nameres", NAMERES, {"string": name, "limit": 5, "autocomplete": "false"})
    out = []
    for c in body if isinstance(body, list) else []:
        syns = [c.get("label")] + list(c.get("synonyms") or [])
        if norm(name) in {norm(s) for s in syns}:          # fuzzy hit -> only an exact synonym counts
            out += syns[:MAX_NAMES]
    return [n for n in out if n]


def other_names(snap, name, sources=SOURCES):
    """{source: [other names of the same substance]} - the query itself is removed."""
    fn = {"pubchem": pubchem, "gsrs": gsrs, "nameres": nameres}
    q = norm(name)
    return {s: sorted({n for n in fn[s](snap, name) if norm(n) and norm(n) != q}) for s in sources}


def snapshot(mode=None):
    from snapshot import Snapshot
    return Snapshot(directory=SNAP_DIR, mode=mode)
