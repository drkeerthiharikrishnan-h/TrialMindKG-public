#!/usr/bin/env python3
"""
The stored "no evidence" example (PK7A, review finding U13): one honest answer that says the graph holds no link between a target
gene and a disease, shows the nearest related evidence, says why the gap exists and what data would close it.

    python3 scripts/make_gap_example.py [--out outputs/examples] [--recorded 2026-11-13T00:00:00Z] [--check]

Nothing is typed in by hand. The pair is the one the fixed query program_target_indication reports with the note "no association
edge in graph" (selonsertib's target MAP3K5 and the disease of its trials). Every sentence is composed from:
  - outputs/kg_nodes.csv, outputs/kg_edges.csv   the trial graph (the same files the app reads, through kg_query.KGIndex)
  - outputs/resolution_report.csv                the recorded reason each pair was not linked (the build's own flags)
  - the frozen Open Targets response for the gene's top diseases (data/snapshots, offline) and its cut-off (seed_kg.MIN_SCORE, TOP_DISEASES)
  - data/reference/mondo_terms.tsv               the labels and synonyms of the diseases
The file is the JSON a /api/query reply has (question, cypher, columns, rows, evidence, problems) plus a `gap` block the page draws.
The bottom line is composed again by /api/summarize when the page opens it, like any stored query answer.

  find_pair(kg)                          -> (drug id, gene id, disease id) of the first drug-target-disease triple without an association edge
  build(kg, report, ot_rows, terms, ...) -> the stored dict (see stored_answers.record)
  main()                                 -> writes outputs/examples/<question id>.json; --check compares with the committed file (exit 1 if it differs)
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
# FIX1 F2: the frozen response is read offline (unless TMKG_SNAPSHOT_MODE says otherwise) per call, in top_diseases; the module
# no longer sets the variable for the whole process (a test that imported it turned every later Snapshot of the suite offline).
SNAPSHOT_MODE = lambda: os.environ.get("TMKG_SNAPSHOT_MODE") or "offline"   # noqa: E731

import kg_query                       # noqa: E402
import stored_answers                 # noqa: E402

RECORDED = "2026-11-13T00:00:00Z"


def question_for(gene_symbol, disease_name):
    """Takes a gene symbol and a disease name. Returns the question text the page offers (and the stored file answers)."""
    return f"Is there any evidence linking {gene_symbol} and {disease_name}?"


def find_pair(kg):
    """Takes the trial graph (kg_query.KGIndex). Returns (program id, drug id, gene id, disease id) of the first program whose
    drug-target gene has no association edge to a disease of its trials (the fixed query's own note), or None."""
    for p in sorted(n["id"] for n in kg.nodes.values() if n.get("category") == "tmkg:ClinicalTrialProgram"):
        for row in kg.program_target_indication(p)["rows"]:
            if row.get("note"):
                return p, row["drug"], row["gene"], row["disease"]
    return None


def load_terms(path=ROOT / "data" / "reference" / "mondo_terms.tsv"):
    """Returns {MONDO id: (label, [synonyms])} from the MONDO table."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or line.startswith("mondo_id"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                out[parts[0]] = (parts[1], [s for s in (parts[2].split("|") if len(parts) > 2 else []) if s])
    return out


def load_report(path=ROOT / "outputs" / "resolution_report.csv"):
    """Returns the rows of the build's flag table as dicts."""
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def top_diseases(ensembl):
    """Takes an Ensembl gene id (ENSG...). Returns (fetched_at, [dict(disease 'MONDO_..', name, score)]) from the frozen Open Targets
    response the build used (offline: a response that was not frozen raises)."""
    import opentargets
    from snapshot import Snapshot
    seed = (ROOT / "pipelines" / "seed_kg.py").read_text(encoding="utf-8")
    top = int(re.search(r"^TOP_DISEASES, MIN_SCORE = (\d+), ([\d.]+)", seed, re.M).group(1))
    r, rows = opentargets.target_diseases(Snapshot(mode=SNAPSHOT_MODE()), ensembl, top)
    return r.fetched_at, rows


def cutoff():
    """Returns (TOP_DISEASES, MIN_SCORE) as seed_kg.py defines them (read from the source, not imported: seed_kg builds a graph on import)."""
    m = re.search(r"^TOP_DISEASES, MIN_SCORE = (\d+), ([\d.]+)", (ROOT / "pipelines" / "seed_kg.py").read_text(encoding="utf-8"), re.M)
    return int(m.group(1)), float(m.group(2))


def build(kg, report, fetched_at, ot_rows, terms, pair, recorded=RECORDED):
    """Takes the trial graph, the flag table, the Open Targets top-disease rows of the gene (and when they were fetched), the MONDO
    terms, the (program, drug, gene, disease) pair and the recording time. Returns the stored dict."""
    pid, drug, gene, disease = pair
    sym, dname = kg.name(gene), kg.name(disease)
    prog_name, drug_name = kg.name(pid), (kg.name(drug) or drug).title()
    top_n, min_score = cutoff()
    question = question_for(sym, dname)
    # the question as a query, run on the graph: there is no association edge between the two
    links = [e for e in kg.out(gene, "biolink:associated_with") if e["object"] == disease] + \
            [e for e in kg.out(disease, "biolink:associated_with") if e["object"] == gene]
    if links:
        raise ValueError(f"{gene} and {disease} are linked after all: {[e['id'] for e in links]}")
    cypher = (f"MATCH (g:Gene {{id:'{gene}'}})-[r:associated_with]-(d:Disease {{id:'{disease}'}}) RETURN r.id AS evidence ORDER BY evidence LIMIT 50")
    # nearest related evidence
    nearest = []
    t = kg.drug_targets(drug)["rows"][0]
    nearest.append(dict(text=f"{drug_name} ({t.get('action', 'acts on').lower()}) is recorded in ChEMBL as acting on {t['target_name']}, whose gene is {sym}: the graph holds "
                             f"{sym} as the target of {prog_name.split(' — ')[0]}.", edges=t["evidence"]))
    trials = kg.out(pid, "tmkg:has_trial")
    cond = []
    for te in trials:
        for d, ev in kg._trial_diseases(te["object"]):
            if d == disease:
                cond.append((te["object"], ev))
    conds = sorted({ev[0] for _, ev in cond})
    cname = {kg.name(kg.edges[e]["object"]) for e in conds}
    nearest.append(dict(text=f"The same program tests {drug_name} in {len(trials)} registered trial{'s' if len(trials) != 1 else ''}; {len(cond)} of them list "
                             f"{' / '.join(sorted(cname))} as the condition, which the graph maps to {dname} ({disease}).",
                        edges=sorted({e for _, ev in cond for e in ev})))
    titles = " ".join(kg.name(te["object"]) or "" for te in trials)
    near = None
    for rank, x in enumerate(ot_rows, 1):
        did = x["disease"].replace("_", ":", 1)
        syn = terms.get(did, ("", []))[1]
        if did != disease and any(re.search(r"\b" + re.escape(s) + r"\b", titles) for s in syn if s.isupper() and len(s) >= 3):
            near = (rank, x, did, next(s for s in syn if s.isupper() and len(s) >= 3 and re.search(r"\b" + re.escape(s) + r"\b", titles)))
            break
    if near:
        rank, x, did, abbr = near
        nearest.append(dict(text=f"Open Targets {kg.version['sources']['Open Targets Platform']} ranks {x['name']} ({did}) number {rank} of the {len(ot_rows)} diseases it lists for {sym}, "
                                 f"with an overall association score of {x['score']:g}. The trial titles of this program say {abbr}, which MONDO lists as a synonym of "
                                 f"{x['name']}, not of {dname}.", edges=[]))
    # why the gap exists
    rep = {(r["entity_type"], r["query"]): r for r in report}            # the build's own flags: (kind of check, item) -> row
    why = []
    ti = rep.get(("target_indication", f"{gene} | {disease}"))
    if ti and ti["rule"] == "disease_not_in_open_targets":
        why.append(f"Open Targets, the graph's source of gene-disease associations, has no disease record under the identifier the graph uses for {dname} ({disease}), "
                   "so the association lookup for this pair returned nothing.")
    elif ti and ti["rule"] == "no_association_in_open_targets":
        why.append(f"Open Targets was asked for the association of {sym} with {dname} and returned none.")
    ls = rep.get(("target_landscape", gene))
    if ls:
        best = ot_rows[0] if ot_rows else None
        n_written = ls["identifier"] or 0  # a local: nested double quotes inside an f-string need Python 3.12 (gate 1, G1-SEC-02)
        why.append(f"The graph also adds a gene-disease link from the top {top_n} diseases Open Targets lists for a target, but only at an overall score of {min_score:g} or higher; "
                   f"for {sym} {'the highest score in that list is ' + format(best['score'], 'g') + ' (' + best['name'] + ')' if best else 'the list is empty'}, so {n_written} link{'' if n_written == '1' else 's'} {'was' if n_written == '1' else 'were'} written.")
    ge = rep.get(("genetic_evidence", f"{gene} | {disease}"))
    if ge and ge["rule"] == "no_genetic_evidence_in_open_targets":
        why.append(f"The genetic-evidence lookup (germline and somatic sources in Open Targets) for {sym} and {dname} found no record either.")
    n_genes_with = len({e["subject"] for e in kg.by_object.get(disease, []) if e["predicate"] == "biolink:associated_with"})
    n_genes = sum(1 for n in kg.nodes.values() if n.get("category") == "biolink:Gene")
    who = f"None of the {n_genes} genes in the trial graph has" if n_genes_with == 0 else f"{n_genes_with} of the {n_genes} genes in the trial graph have"
    why.append(f"{who} an association link to {dname}: the gap is about this pair and this identifier, and says nothing about whether the two are related in biology.")
    data_needed = [
        f"A gene-disease statement for {sym} and {dname} from a source the graph reads (an Open Targets association at or above the cut-off, or a curated statement), under an identifier the graph can match to {disease}.",
        f"If the disease should be read as {near[1]['name']} ({near[2]}) instead, a decision by a person to map the program's trials to that identifier (it is a different disease node, so the graph does not do it on its own)." if near else
        f"A decision by a person on which disease identifier the program's trials should use, if {dname} is not the intended one.",
        f"Measurements from the program itself that name {sym} together with the disease (for example a biomarker or pathway analysis from the {prog_name.split(' — ')[0]} trials), added through the curated tables.",
    ]
    statement = f"No evidence in this graph for {sym} and {dname}."
    gap = dict(statement=statement, subject=dict(id=gene, label=sym), object=dict(id=disease, label=dname), nearest=nearest, why=why, data_needed=data_needed,
               retrieved=fetched_at[:10])
    answer = dict(question=question, entities=[gene, disease], cypher=cypher, problems=[], columns=["evidence"], rows=[], evidence=[],
                  attempts=[], backend="offline", model="fixed rules", dropped_constraints=[], cached=False, gap=gap,
                  explanation=f"Look for an association link between {sym} ({gene}) and {dname} ({disease}) in the trial graph.")
    return stored_answers.record(dict(id=stored_answers.question_id(question), kind="query", question=question, endpoint="/api/query"),
                                 answer, "offline", "fixed rules", recorded)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "outputs" / "examples"))
    ap.add_argument("--recorded", default=RECORDED)
    ap.add_argument("--check", action="store_true", help="compare with the committed file instead of writing")
    a = ap.parse_args(argv)
    kg = kg_query.KGIndex()
    pair = find_pair(kg)
    if pair is None:
        print("no drug-target-disease pair without an association edge in this graph", file=sys.stderr)
        return 1
    fetched, rows = top_diseases(pair[2].split(":")[1])
    stored = build(kg, load_report(), fetched, rows, load_terms(), pair, a.recorded)
    path = Path(a.out) / f"{stored['id']}.json"
    text = json.dumps(stored, ensure_ascii=False, indent=1) + "\n"
    if a.check:
        same = path.exists() and path.read_text(encoding="utf-8") == text
        print("same" if same else "DIFFERENT", path)
        return 0 if same else 1
    path.write_text(text, encoding="utf-8")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
