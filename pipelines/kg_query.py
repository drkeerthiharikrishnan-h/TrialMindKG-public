"""
Answer harness: the ONLY way agents read the graph. Deterministic, citable, checkable.

Every query function returns an Answer:
    {"question": name, "params": {...}, "rows": [...sorted...], "evidence": [edge ids],
     "graph_version": {"edges_sha256": ..., "snapshot": ...}}
Each row carries `evidence` = the ids of the edges it is built on. An agent writing prose must cite
those ids as [e0123456789abcdef]; verify_answer() then rejects text that cites unknown edges or states
numbers that do not appear in the cited evidence (CLAUDE.md rule 1: no invented numbers).

Queries (same names/params work as CLI:  python pipelines/kg_query.py program_trials pid=P19)
  program_trials(pid)            trials of a failed program, with status / results / IPD statement
  drug_targets(drug)             targets (and genes) of a drug, with mechanism and source
  program_target_indication(pid) investigational targets x trial indications, Open Targets score
  trial_outcomes(nct, kind)      outcome measures (default PRIMARY) with posted statistical analyses
  trial_publications(nct)        registry-linked publications with metadata
  trial_drugs(nct)               investigational, comparator and background drugs
  trial_safety(nct)              posted serious adverse events and deaths per arm
  trial_adverse_events(nct, term, kind)  posted adverse-event terms (MedDRA text) with per-arm counts;
                                 term = case-insensitive substring filter, kind = serious | other | None (both)
  trial_design(nct)              arms (with written doses and the drugs they receive), population (eligibility,
                                 sex, ages, enrolment) and registered endpoints linked to posted results
  program_history(pid)           curated post-failure analyses, repurposing pivots and data-access routes
  shared_targets()               targets hit by investigational drugs of 2+ programs
  provenance(edge_id)            full provenance of one edge, incl. path to the raw API response

Reads outputs/kg_nodes.csv + kg_edges.csv - the same files loaded into Neptune - so a local answer
and a Neptune answer come from identical data (graph_version proves which).
"""
import csv, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CITE = re.compile(r"\[(e[0-9a-f]{16})\]")
# A number: not part of a name (TULIP-1, NCT02446912, IL-5). Atomic group: '0.75' is never cut back to '0'.
NUMBER = re.compile(r"(?<![\w.-])(?>-?\d+(?:\.\d+)?)(?![\w-])")
RANGE = re.compile(r"(?<=\d)\s?[-\u2013]\s?(?=\d)")             # '0.75-1.03' / '6.7 - 26.2' -> two numbers


def numbers(text):
    """All numbers in text as strings; a range 'a-b' gives a and b (a hyphen between digits is a range, never a
    minus sign). '95% CI 0.75-1.03, p=0.12' -> ['95', '0.75', '1.03', '0.12']."""
    return NUMBER.findall(RANGE.sub(" to ", text))


class KGIndex:
    def __init__(self, out_dir=ROOT / "outputs"):
        csv.field_size_limit(sys.maxsize)             # adverse-event tables are large JSON cells
        out = Path(out_dir)
        with open(out / "kg_nodes.csv", encoding="utf-8") as f:
            self.nodes = {r["id"]: {k: v for k, v in r.items() if v != ""} for r in csv.DictReader(f)}
        with open(out / "kg_edges.csv", encoding="utf-8") as f:
            self.edges = {r["id"]: {k: v for k, v in r.items() if v != ""} for r in csv.DictReader(f)}
        self.by_subject, self.by_object = {}, {}
        for e in self.edges.values():
            self.by_subject.setdefault(e["subject"], []).append(e)
            self.by_object.setdefault(e["object"], []).append(e)
        m = out / "run_manifest.json"
        man = json.loads(m.read_text()) if m.exists() else {}
        self.version = {"edges_sha256": man.get("outputs_sha256", {}).get("kg_edges.csv"),
                        "snapshot": man.get("snapshot", {}).get("fingerprint"), "sources": man.get("sources")}

    def out(self, nid, predicate):
        return sorted((e for e in self.by_subject.get(nid, []) if e["predicate"] == predicate),
                      key=lambda e: (e["object"], e["id"]))

    def inn(self, nid, predicate):
        return sorted((e for e in self.by_object.get(nid, []) if e["predicate"] == predicate),
                      key=lambda e: (e["subject"], e["id"]))

    def name(self, nid):
        return self.nodes.get(nid, {}).get("name")

    def answer(self, question, params, rows):
        ev = sorted({i for r in rows for i in r.get("evidence", [])})
        return {"question": question, "params": params, "rows": rows, "evidence": ev,
                "graph_version": self.version}

    # ------------------------------------------------------------------ queries
    def program_trials(self, pid):
        rows = []
        for e in self.out(pid, "tmkg:has_trial"):
            t = self.nodes[e["object"]]
            rows.append(dict(trial=t["id"], name=t.get("name"), acronym=t.get("acronym"), status=t.get("status"),
                             why_stopped=t.get("why_stopped"), phase=t.get("phase"), has_results=t.get("has_results"),
                             ipd_sharing=t.get("ipd_sharing"), match_rule=e.get("match_rule"), evidence=[e["id"]]))
        return self.answer("program_trials", {"pid": pid}, rows)

    def _genes(self, target):
        return [e for p in ("biolink:gene_product_of", "tmkg:has_component_gene", "tmkg:has_member_gene")
                for e in self.out(target, p)]

    def drug_targets(self, drug):
        rows = []
        for e in self.out(drug, "biolink:interacts_with"):
            t = e["object"]
            genes = [t] if t.startswith("ENSEMBL:") else []
            gene_edges = [] if genes else self._genes(t)
            rows.append(dict(target=t, target_name=self.name(t), category=self.nodes[t]["category"],
                             genes=sorted(genes + [g["object"] for g in gene_edges]),
                             gene_symbols=sorted(self.name(g) or g for g in genes + [g["object"] for g in gene_edges]),
                             moa=e.get("moa"), action=e.get("chembl_action_type"), source=e["source"],
                             evidence=[e["id"]] + [g["id"] for g in gene_edges]))
        return self.answer("drug_targets", {"drug": drug}, rows)

    def _trial_diseases(self, trial):
        out = []
        for e in self.out(trial, "tmkg:studies_condition"):
            if e["object"].startswith("MONDO:"):
                out.append((e["object"], [e["id"]]))
            for x in self.out(e["object"], "biolink:exact_match"):
                out.append((x["object"], [e["id"], x["id"]]))
        return out

    def program_target_indication(self, pid):
        rows = []
        diseases = {}
        for te in self.out(pid, "tmkg:has_trial"):
            for d, ev in self._trial_diseases(te["object"]):
                diseases.setdefault(d, [te["id"]] + ev)
        for de in self.out(pid, "tmkg:has_intervention"):
            for t in self.drug_targets(de["object"])["rows"]:
                for g in t["genes"]:
                    for d in sorted(diseases):
                        a = [x for x in self.out(g, "biolink:associated_with") if x["object"] == d]
                        rows.append(dict(drug=de["object"], drug_name=self.name(de["object"]), gene=g,
                                         gene_symbol=self.name(g), disease=d, disease_name=self.name(d),
                                         ot_score=a[0].get("ot_score") if a else None,
                                         ot_datatype_scores=a[0].get("ot_datatype_scores") if a else None,
                                         note=None if a else "no association edge in graph",
                                         evidence=sorted(set([de["id"]] + t["evidence"] + diseases[d] +
                                                             [x["id"] for x in a]))))
        rows.sort(key=lambda r: (r["drug"], r["gene"], r["disease"]))
        return self.answer("program_target_indication", {"pid": pid}, rows)

    def trial_outcomes(self, nct, kind="PRIMARY"):
        tid = nct if nct.startswith("NCT:") else f"NCT:{nct}"
        rows = []
        for e in self.out(tid, "tmkg:has_outcome_measure"):
            o = self.nodes[e["object"]]
            if kind and o.get("kind") != kind:
                continue
            rows.append(dict(outcome=o["id"], title=o.get("name"), kind=o.get("kind"), time_frame=o.get("time_frame"),
                             data=o.get("source"), groups=json.loads(o.get("groups", "{}")),
                             analyses=json.loads(o.get("analyses", "[]")), evidence=[e["id"]]))
        return self.answer("trial_outcomes", {"nct": nct, "kind": kind}, rows)

    def trial_publications(self, nct):
        tid = nct if nct.startswith("NCT:") else f"NCT:{nct}"
        rows = [dict(pmid=e["object"], reference_type=e.get("reference_type"), **{
                    k: self.nodes[e["object"]].get(k) for k in ("year", "journal", "title", "doi")},
                     evidence=[e["id"]]) for e in self.out(tid, "tmkg:has_publication")]
        return self.answer("trial_publications", {"nct": nct}, rows)

    def trial_drugs(self, nct):
        tid = nct if nct.startswith("NCT:") else f"NCT:{nct}"
        rows = []
        for p, role in (("tmkg:tests_intervention", "investigational"), ("tmkg:has_comparator_drug", "comparator"),
                        ("tmkg:has_background_drug", "background")):
            rows += [dict(drug=e["object"], name=self.name(e["object"]), role=role, arm_types=e.get("arm_types"),
                          evidence=[e["id"]]) for e in self.out(tid, p)]
        return self.answer("trial_drugs", {"nct": nct}, rows)

    def trial_safety(self, nct):
        tid = nct if nct.startswith("NCT:") else f"NCT:{nct}"
        t = self.nodes.get(tid, {})
        ev = [e["id"] for e in self.inn(tid, "tmkg:has_trial")][:1]     # registry record of the trial
        rows = [dict(**g, time_frame=t.get("ae_time_frame"), evidence=ev) for g in json.loads(t.get("ae_groups", "[]"))]
        return self.answer("trial_safety", {"nct": nct}, rows)

    def trial_adverse_events(self, nct, term=None, kind=None):
        tid = nct if nct.startswith("NCT:") else f"NCT:{nct}"
        rows = []
        for e in self.out(tid, "tmkg:has_adverse_event_report"):
            rep = self.nodes[e["object"]]
            if kind and rep.get("event_type") != kind:
                continue
            for t in json.loads(rep.get("terms", "[]")):
                if term and term.lower() not in (t["term"] or "").lower():
                    continue
                rows.append(dict(event_type=rep.get("event_type"), term=t["term"], organ_system=t["organ_system"],
                                 vocabulary=t["vocabulary"], arms=t["arms"], time_frame=rep.get("time_frame"),
                                 evidence=[e["id"]]))
        rows.sort(key=lambda r: (r["event_type"], r["organ_system"] or "", r["term"] or ""))
        return self.answer("trial_adverse_events", {"nct": nct, "term": term, "kind": kind}, rows)

    def trial_design(self, nct):
        tid = nct if nct.startswith("NCT:") else f"NCT:{nct}"
        rows = []
        for e in self.out(tid, "tmkg:has_study_population"):
            p = self.nodes[e["object"]]
            rows.append(dict(part="population", id=e["object"], **{k: p.get(k) for k in (
                "enrollment", "sex", "minimum_age", "maximum_age", "healthy_volunteers", "eligibility_criteria")},
                evidence=[e["id"]]))
        for e in self.out(tid, "tmkg:has_arm"):
            a = self.nodes[e["object"]]
            gets = self.out(e["object"], "tmkg:arm_receives")
            rows.append(dict(part="arm", id=e["object"], name=a.get("name"), arm_type=a.get("arm_type"),
                             description=a.get("description"), dose_text=a.get("dose_text"),
                             drugs=[self.name(g["object"]) for g in gets], evidence=[e["id"]] + [g["id"] for g in gets]))
        for e in self.out(tid, "tmkg:has_registered_endpoint"):
            n = self.nodes[e["object"]]
            res = self.out(e["object"], "tmkg:endpoint_result")
            rows.append(dict(part="endpoint", id=e["object"], name=n.get("name"), kind=n.get("kind"),
                             time_frame=n.get("time_frame"), posted_result=res[0]["object"] if res else None,
                             evidence=[e["id"]] + [r["id"] for r in res]))
        return self.answer("trial_design", {"nct": nct}, rows)

    def program_history(self, pid):
        rows = []
        for p in ("tmkg:has_post_failure_analysis", "tmkg:has_repurposing_pivot"):
            for e in self.out(pid, p):
                rows.append(dict(event=e["object"], **{k: v for k, v in self.nodes[e["object"]].items()
                                                      if k not in ("id", "category")}, evidence=[e["id"]]))
        for te in self.out(pid, "tmkg:has_trial"):
            for e in self.out(te["object"], "tmkg:has_data_access_route"):
                rows.append(dict(event=e["object"], trial=te["object"], **{
                    k: v for k, v in self.nodes[e["object"]].items() if k not in ("id", "category")},
                    evidence=[te["id"], e["id"]]))
        rows.sort(key=lambda r: (r["event"], r.get("trial", "")))
        return self.answer("program_history", {"pid": pid}, rows)

    def shared_targets(self):
        hits = {}
        for pid in sorted(n for n, v in self.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram"):
            for de in self.out(pid, "tmkg:has_intervention"):
                for te in self.out(de["object"], "biolink:interacts_with"):
                    hits.setdefault(te["object"], {}).setdefault(pid, [de["id"], te["id"]])
        rows = [dict(target=t, target_name=self.name(t), programs=sorted(p),
                     evidence=sorted({i for ev in p.values() for i in ev}))
                for t, p in sorted(hits.items()) if len(p) > 1]
        return self.answer("shared_targets", {}, rows)

    def provenance(self, edge_id):
        e = self.edges[edge_id]
        key = e.get("snapshot_key")
        raw = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "data" / "snapshots").glob(f"*/{key}.json.gz")) if key else []
        return self.answer("provenance", {"edge_id": edge_id},
                           [dict(**e, raw_response=raw[0] if raw else None, evidence=[edge_id])])


# reference values a claim may state without them being data: the 5% significance threshold, the null value 1 of a
# ratio ("the CI crosses 1"), 0 of a difference. verify_answer(..., conventions=True) ignores exactly these.
CONVENTIONS = {0.05, 1.0, 0.0}


def verify_answer(text, kg, allowed=None, conventions=False):
    """Checks prose written from Answers. Returns dict(ok, citations, unknown_citations, not_allowed,
    unsupported_numbers). A number is supported if it appears in a cited edge or its two end nodes.
    `allowed` (optional) = evidence ids the agent was given; citing anything else is flagged."""
    cites = CITE.findall(text)
    unknown = sorted({c for c in cites if c not in kg.edges})
    not_allowed = sorted({c for c in cites if allowed is not None and c not in allowed})
    support = " ".join(json.dumps(x, ensure_ascii=False) for c in cites if c in kg.edges
                       for x in (kg.edges[c], kg.nodes.get(kg.edges[c]["subject"], {}),
                                 kg.nodes.get(kg.edges[c]["object"], {})))
    # compared by VALUE: '95' in a claim is supported by '95.0' in the data (AACT stores numbers as numbers)
    support_nums = {float(n) for n in numbers(support)}
    unsupported = sorted({n for n in numbers(CITE.sub(" ", text)) if float(n) not in support_nums
                          and not (conventions and float(n) in CONVENTIONS)})
    return dict(ok=bool(cites) and not unknown and not not_allowed and not unsupported, citations=sorted(set(cites)),
                unknown_citations=unknown, not_allowed=not_allowed, unsupported_numbers=unsupported)


def run_golden(kg, path=ROOT / "data" / "curated" / "golden_questions.csv"):
    """Regression set: each row names a query, its params, a field and the value that must appear
    (exact value, or 'contains:<text>' for text that must appear inside the field)."""
    results = []
    with open(path, encoding="utf-8") as f:
        for q in csv.DictReader(f):
            ans = getattr(kg, q["function"])(**json.loads(q["params"]))
            values = [str(r.get(q["field"])) for r in ans["rows"]]
            values += [str(a.get(q["field"])) for r in ans["rows"] for a in r.get("analyses", [])]
            exp = q["expected"]
            ok = any(exp[9:] in v for v in values) if exp.startswith("contains:") else exp in values
            results.append(dict(qid=q["qid"], passed=ok, expected=exp,
                                got=sorted(set(values))[:10], evidence=ans["evidence"]))
    return results


if __name__ == "__main__":
    kg = KGIndex()
    fn, kwargs = sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:])
    print(json.dumps(getattr(kg, fn)(**kwargs), indent=2, ensure_ascii=False))
