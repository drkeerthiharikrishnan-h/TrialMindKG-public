"""
BioLink provenance on every edge (application commitment): who asserted the statement and how.

Adds to each edge, from the reviewable table data/curated/provenance_map.csv (first matching row wins):
  primary_knowledge_source     infores id of the resource that originally asserted it (BioLink slot)
  aggregator_knowledge_source  resources that passed it on, ending with TrialMindKG (list)
  supporting_data_source       resources whose data the assertion relies on (list)
  knowledge_level              BioLink KnowledgeLevelEnum (knowledge_assertion, statistical_association, ...)
  agent_type                   BioLink AgentTypeEnum (manual_agent, automated_agent, ...)

infores ids are checked against the official Information Resource Registry (biolink/information-resource-registry,
frozen through Snapshot); an id that is missing or deprecated stops the build. TrialMindKG itself is not yet a
registered resource, so its own matching / curation is labelled 'tmkg:trialmindkg' (never an invented infores id).

Functions: load_catalog(snap) -> {infores id: status};  load_map(path) -> rows;  rule_for(edge, rows) -> row;
           annotate(kg, snap, path) -> problems (empty list = every edge annotated with registered ids)
"""
import csv, json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "data" / "curated" / "provenance_map.csv"
CATALOG_URL = "https://raw.githubusercontent.com/biolink/information-resource-registry/main/infores_catalog.yaml"
LIST_FIELDS = ("aggregator_knowledge_source", "supporting_data_source")


def load_catalog(snap):
    r = snap.get_text("infores", CATALOG_URL)
    return {x["id"]: x.get("status") for x in yaml.safe_load(r.body["text"])["information_resources"]}


def load_map(path=MAP):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _match(pattern, value):
    return pattern == "*" or value == pattern or (pattern.endswith("*") and value.startswith(pattern[:-1]))


def rule_for(edge, rows):
    return next((r for r in rows if _match(r["source"], edge["source"]) and _match(r["evidence_type"], edge["evidence_type"])
                 and _match(r["predicate"], edge["predicate"])), None)


def annotate(kg, snap, path=MAP):
    """Sets the five fields on every edge of kg (in place). Returns a list of problems."""
    rows, catalog = load_map(path), load_catalog(snap)
    problems = []
    for r in rows + [dict(primary_knowledge_source="infores:aact", aggregator_knowledge_source="",
                          supporting_data_source="")]:
        for v in [r["primary_knowledge_source"]] + [x for f in LIST_FIELDS for x in r[f].split("|") if x]:
            if v.startswith("infores:") and catalog.get(v) != "released":
                problems.append(f"provenance_map: {v} is {'deprecated' if v in catalog else 'not in the infores registry'}")
            elif not v.startswith(("infores:", "tmkg:")):
                problems.append(f"provenance_map: {v} is neither an infores id nor a tmkg: label")
    for eid in sorted(kg.edges):
        e = kg.edges[eid]
        r = rule_for(e, rows)
        if r is None:
            problems.append(f"edge {eid}: no provenance_map row for {e['source']!r} / {e['evidence_type']} / {e['predicate']}")
            continue
        e["primary_knowledge_source"] = r["primary_knowledge_source"]
        for f in LIST_FIELDS:
            vals = [x for x in r[f].split("|") if x]
            if f == "aggregator_knowledge_source" and e.get("via") == "AACT":
                vals = ["infores:aact"] + vals          # registry record read from CTTI's AACT copy
            if vals:
                e[f] = json.dumps(vals)         # lists are stored as JSON text, like other list properties
            else:
                e.pop(f, None)
        e["knowledge_level"], e["agent_type"] = r["knowledge_level"], r["agent_type"]
    return problems
