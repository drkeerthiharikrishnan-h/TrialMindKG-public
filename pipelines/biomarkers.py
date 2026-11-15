"""
Biomarker subgroups (P2): which outcome measures / endpoints were analysed in a biomarker-defined subgroup.

    python pipelines/biomarkers.py          # Nemotron proposals -> data/curated/biomarker_candidates.csv (PROPOSED)

Step 1 (agent, this module): every outcome / endpoint title that mentions a subgroup-like cue is given to the
        extractor model, which names each biomarker and copies the exact text that defines it (e.g. 'PD-L1 (TC
        >=25%)'). A proposal whose quote is not word for word in the title is REJECTED (CLAUDE.md rules 1 and 3).
Step 2 (owner): copy accepted rows into data/curated/biomarkers.csv with a canonical biomarker name, optional
        Ensembl gene id (must already be a gene node in the graph) and a justification
        'Confirmed by owner YYYY-MM-DD: ...'. Only confirmed rows enter the graph (seed_kg.add_biomarkers).

Functions
- candidate_titles(kg)            -> [(node id, title)] outcome / endpoint titles with a subgroup cue
- extract(nim, title)             -> [dict(biomarker, definition_quote, status)]  status ok | REJECTED_quote
- propose(nim, kg, path)          -> rows written to biomarker_candidates.csv
- load_confirmed(path)            -> [dict(node_id, biomarker, definition, ensembl, justification)]
- slug(name)                      -> 'PD-L1 tumour cells' -> 'PD-L1_TUMOUR_CELLS' (node id part)
"""
import csv, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nim import register_offline
from stop_reasons import quote_ok

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "data" / "curated" / "biomarker_candidates.csv"
CONFIRMED = ROOT / "data" / "curated" / "biomarkers.csv"
CUE = re.compile(r"sub-?groups?|sub-?populations?|analysis set population|genotype|carriers?|variant|status|"
                 r"\b(positive|negative)\b|test-high|test-low|expression|\bby baseline\b|in (participants|patients) with",
                 re.I)
SCHEMA = {"type": "object", "required": ["biomarkers"], "properties": {"biomarkers": {"type": "array", "items": {
    "type": "object", "required": ["biomarker", "definition_quote"], "properties": {
        "biomarker": {"type": "string"}, "definition_quote": {"type": "string"}}}}}}
SYSTEM = ("From ONE clinical-trial outcome title, list the biomarkers that define a subgroup of participants "
          "(a gene, genotype, protein, cell count, signature or lab value with a threshold or status). "
          "Not biomarkers: endpoints being measured (e.g. 'change in LDL cholesterol'), adverse-event counts, "
          "analysis parts, doses, visit weeks. biomarker = short name; definition_quote = the shortest span copied "
          "word for word from the title that defines the subgroup (e.g. 'PD-L1 (TC >=25%)'). "
          "Return an empty list when the title has none.")


def candidate_titles(kg):
    out = []
    for nid in sorted(kg.nodes):
        n = kg.nodes[nid]
        if n.get("category") in ("tmkg:OutcomeMeasure", "tmkg:Endpoint") and CUE.search(n.get("name") or ""):
            out.append((nid, n["name"]))
    return out


def extract(nim, title):
    r = nim.chat("biomarker", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "TITLE: " + title}],
                 schema=SCHEMA)
    return [dict(biomarker=b.get("biomarker", "").strip(), definition_quote=b.get("definition_quote", "").strip(),
                 status="ok" if quote_ok(b.get("definition_quote"), title) else "REJECTED_quote",
                 backend=r.backend, snapshot_key=r.key)
            for b in (r.data or {}).get("biomarkers", []) if b.get("biomarker")]


def propose(nim, kg, path=CANDIDATES):
    rows = []
    seen = {}
    for nid, title in candidate_titles(kg):
        if title not in seen:                       # identical titles (endpoint + its result) asked once
            seen[title] = extract(nim, title)
        for b in seen[title]:
            rows.append(dict(node_id=nid, title=title, **b,
                             note="PROPOSED - confirm by copying to data/curated/biomarkers.csv"))
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["node_id", "title", "biomarker", "definition_quote", "status", "backend", "snapshot_key", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


def load_confirmed(path=CONFIRMED):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("node_id") and r.get("biomarker")
                and str(r.get("justification", "")).startswith("Confirmed by owner")]


def slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").upper()


# ---- offline stand-in: transparent patterns (NOT a model; labelled offline-stand-in)
PATTERNS = [r"PD-L1 \([^)]*\)", r"Interferon \(IFN\) Test-High", r"[A-Z0-9-]+ (?:gene )?variant subgroup \([^)]*\)",
            r"(?:blood )?eosinophils? [≥>=<]+ ?\d+[^ ,;)]*", r"ADCY9 [A-Z/]+ genotype", r"baseline PD-L1 status"]


@register_offline("biomarker")
def _offline(messages, schema):
    title = messages[-1]["content"].split("TITLE: ", 1)[-1]
    out = []
    for p in PATTERNS:
        for m in re.finditer(p, title, re.I):
            out.append({"biomarker": m.group(0).split(" (")[0].split(" ")[0], "definition_quote": m.group(0)})
    return json.dumps({"biomarkers": out})


if __name__ == "__main__":
    import nim as nimmod
    from kg_query import KGIndex
    rows = propose(nimmod.NIM(), KGIndex())
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"{len(rows)} proposals ({ok} with a verified quote) -> {CANDIDATES}")
