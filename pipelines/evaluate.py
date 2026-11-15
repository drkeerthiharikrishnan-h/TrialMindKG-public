"""
Evaluation harness: the metrics promised in the application, computed in one place.

    python pipelines/evaluate.py            # -> outputs/eval/summary_<backend>.json and .md

Backend: offline stand-ins here (proves the plumbing; scores are NOT model scores); with NVIDIA_API_KEY set the
same run uses the NIM models in config/nim_models.yaml. Every metric reports its definition, n and status:
  computed | pending ground truth (owner workbook not filled) | needs NIM (no offline stand-in, e.g. LLM baseline)

Graph quality      schema-valid edge rate, provenance completeness, entity-mapping coverage (+ precision vs workbook)
Answer harness     golden question pass rate
Agents             Querier execution accuracy, Resolver top-1 / MRR, stop-reason quote-verified rate (+ agreement)
Reports            evidence-supported claim rate (first draft and after one repair), unsupported (rejected) claim
                   rate, NCT-id mismatches, negative-control overclaims, latency per question; comparison with an
                   LLM-only baseline (same model, no graph): every claim of both systems judged by the same judge
                   (supported / contradicted / not addressed) against the same evidence. Rates carry 95% Wilson CIs.
                   Run on the benchmark questions and on one template question per curated program.

Functions: graph_quality(kg); mapping_precision(workbook); reports(nim, kg, questions) -> (reports, rows);
           report_metrics(rows); comparison(nim, kg, questions, reports) -> claim rows; comparison_metrics(rows);
           judge(nim, kg, text, edges, facts); nct_mismatches(text, kg); wilson(k, n); program_questions(kg);
           run(nim, kg) -> summary dict; to_markdown(summary) -> str
"""
import csv, json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kg_schema, kg_query, nim as nimmod, orchestrator, querier, rag_baseline, resolver, retrieval, specialists, stop_reasons
from kg_query import KGIndex

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "data" / "curated" / "benchmark_questions.csv"
WORKBOOK = ROOT / "data" / "curated" / "ground_truth_template.xlsx"
OUT = ROOT / "outputs" / "eval"


# review-report rows that record a lookup result, not an entity mapping (absence of evidence is not a mapping failure)
NOT_MAPPING = {"genetic_evidence", "target_landscape", "babel_same_concept", "pharmacogenomics", "atc", "raw_items", "gwas"}


def metric(value, n, definition, status="computed", **extra):
    return dict(value=value, n=n, definition=definition, status=status, **extra)


def _rate(k, n):
    return round(k / n, 3) if n else None


# ------------------------------------------------------------------ graph quality
def graph_quality(kg, report=ROOT / "outputs" / "resolution_report.csv"):
    g = kg_schema.KG()
    g.nodes, g.edges = kg.nodes, kg.edges
    problems = kg_schema.validate(g, "seed")
    bad = {p.split(":")[0].split()[1] for p in problems if p.startswith("edge ")}
    prov = [f for f in kg_schema.EDGE_FIELDS]
    complete = sum(all(e.get(f) not in (None, "") for f in prov) for e in kg.edges.values())
    rows = list(csv.DictReader(open(report, encoding="utf-8")))
    by = {}
    rows = [r for r in rows if r["entity_type"] not in NOT_MAPPING]
    for r in rows:
        ok = r["resolved"] == "True" and not r["rule"].endswith("NEEDS_REVIEW")
        by.setdefault(r["entity_type"], []).append(ok)
    return {
        "schema_valid_edge_rate": metric(_rate(len(kg.edges) - len(bad), len(kg.edges)), len(kg.edges),
                                         "edges passing kg_schema.validate (domain/range, ids, evidence type, dates)",
                                         node_problems=sum(p.startswith("node ") for p in problems)),
        "provenance_completeness": metric(_rate(complete, len(kg.edges)), len(kg.edges),
                                          "edges with every provenance field filled: " + ", ".join(prov)),
        "entity_mapping_coverage": metric(_rate(sum(sum(v) for v in by.values()), len(rows)), len(rows),
                                          "entities resolved automatically without a NEEDS_REVIEW flag",
                                          by_type={k: dict(rate=_rate(sum(v), len(v)), n=len(v)) for k, v in sorted(by.items())}),
    }


def mapping_precision(kg, workbook=WORKBOOK):
    """Pipeline id vs the reviewer's confirmed id (Trials: NCT, Drugs: ChEMBL), only rows the reviewer filled."""
    from openpyxl import load_workbook
    wb = load_workbook(workbook, data_only=True)
    pairs = []
    for sheet, pipe, conf in (("Trials", "Pipeline NCT", "Confirmed NCT"),
                              ("Drugs", "Pipeline ChEMBL id", "Confirmed ChEMBL id")):
        ws = wb[sheet]
        head = [c.value for c in ws[1]]
        pi, ci = head.index(pipe), head.index(conf)
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r[ci] not in (None, "") and r[pi] not in (None, ""):
                norm = lambda x: str(x).split(":")[-1].strip().upper()
                pairs.append((sheet, norm(r[pi]) == norm(r[ci])))
    if not pairs:
        return metric(None, 0, "pipeline ids that match the reviewer's confirmed ids (Trials, Drugs sheets)",
                      "pending ground truth")
    return metric(_rate(sum(ok for _, ok in pairs), len(pairs)), len(pairs),
                  "pipeline ids that match the reviewer's confirmed ids (Trials, Drugs sheets)")


# ------------------------------------------------------------------ agents
def agents(nim, kg):
    out = {}
    g = kg_query.run_golden(kg)
    out["golden_pass_rate"] = metric(_rate(sum(r["passed"] for r in g), len(g)), len(g),
                                     "golden questions whose expected value is returned (data/curated/golden_questions.csv)",
                                     failed=[r["qid"] for r in g if not r["passed"]])
    q = querier.evaluate(nim, kg)
    out["querier_execution_accuracy"] = metric(q["execution_accuracy"], q["n"],
                                               "generated Cypher returns exactly the gold query's evidence edges",
                                               valid_rate=q["valid_rate"], first_try_valid=q["first_try_valid"])
    import chembl_synonyms
    import drug_names
    r = resolver.evaluate(nim, resolver.build_index(), dictionary=chembl_synonyms.load()[1],
                          snap=drug_names.snapshot(None if nim.backend == "nim" else "offline"))
    out["resolver_top1"] = metric(r["top1"], r["n"], "ChEMBL research code / trade name -> correct molecule at rank 1 "
                                  "(leave-one-out)", top3=r["top3"], mrr=r["mrr"])
    out["resolver_top1_same_active_ingredient"] = metric(r["top1_same_active_ingredient"], r["n"],
                                                         "as resolver_top1, but a salt / parent form of the correct "
                                                         "molecule also counts (the graph compares drugs by parent)",
                                                         by_channel=r["by_top1_channel"])
    rows = [dict(trial=t, source=s, **stop_reasons.categorize(nim, x, s)) for _, t, s, x in stop_reasons.trial_texts()]
    out["stop_reason_quote_verified"] = metric(_rate(sum(x["status"] == "ok" for x in rows), len(rows)), len(rows),
                                               "extractions whose evidence quote appears word for word in the text")
    agree = stop_reasons.evaluate(rows)
    out["stop_reason_agreement"] = (metric(agree["agreement"], agree["n"], "category agrees with reviewer's failure category")
                                    if agree.get("n") else metric(None, 0, "category agrees with reviewer's failure "
                                                                  "category", "pending ground truth"))
    return out


# ------------------------------------------------------------------ reports + LLM-only baseline
def load_questions(path=QUESTIONS):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def wilson(k, n, z=1.96):
    """95% Wilson score interval for k successes out of n -> [low, high] (None when n = 0)."""
    if not n:
        return None
    p, d = k / n, 1 + z * z / n
    c, h = (p + z * z / (2 * n)) / d, z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


def program_questions(kg):
    """One question per curated program, from a fixed template filled with the program label (drug - trials).
    Programs that already have a benchmark question are skipped by run()."""
    out = []
    for pid in sorted(n for n, v in kg.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram"):
        drug, _, trials = kg.nodes[pid].get("name", "").partition(" \u2014 ")
        if trials:
            out.append(dict(id=f"Q{pid}", program=pid, kind="program",
                            question=f"Why did {drug.strip()} fail in {trials.strip()}, and what happened after the failure?",
                            expectation="template question (all programs)"))
    return out


TRIAL_NAME = re.compile(r"[A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9+]+)+|\b[A-Z][A-Z0-9]{2,}\b")


def nct_mismatches(text, kg):
    """NCT ids the text attaches to the wrong trial: the trial name written right before the id (e.g. 'TULIP-2
    (NCT02446912)') differs from the acronym the registry gives that NCT id. -> [(nct, acronym in graph)].
    Ids not in the graph, without a known trial name, or without a name before them, are not judged."""
    out = []
    for m in re.finditer(r"NCT\d{8}", text):
        node = kg.nodes.get(f"NCT:{m.group()}")
        own = retrieval.label(node, kg).rsplit(" (", 1)[0] if node else None       # registry acronym or curated name
        own = None if own == "trial" else own
        before = TRIAL_NAME.findall(text[max(0, m.start() - 30):m.start()])
        b = _key(before[-1]) if before else ""
        if own and before and not (_key(own).startswith(b) or b.startswith(_key(own))) \
                and _key(own) not in _key(text[max(0, m.start() - 30):m.start()]):     # 'VALOR' names 'VALOR (Part C)'
            out.append((m.group(), own))
    return sorted(set(out))


def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def reports(nim, kg, questions):
    """Runs the orchestrator on each question -> (reports, per-question rows)."""
    rows, out = [], []
    for q in questions:
        t = time.time()
        rep = orchestrator.ask(q["question"], kg=kg, nim=nim)
        secs = round(time.time() - t, 2)
        claims = [c for s in rep["sections"] for c in s["claims"]]
        over = [c["claim"] for c in claims if c["evidence_status"] == "prospectively_confirmed"] \
            if q["kind"] == "negative_control" else []
        rows.append(dict(id=q["id"], kind=q["kind"], drafted=len(claims) + len(rep["rejected"]), supported=len(claims),
                         first_draft_supported=sum(not c.get("repaired_from") for c in claims),
                         repaired=sum(bool(c.get("repaired_from")) for c in claims), rejected=len(rep["rejected"]),
                         nct_mismatches=sum(len(nct_mismatches(c["claim"], kg)) for c in claims),
                         support_status={s: sum(c.get("support_status") == s for c in claims)
                                         for s in ("supported", "hypothesis_generating", "mixed", "unchecked")},
                         causal_rejections=sum(any("causal wording" in x for x in r["reasons"]) for r in rep["rejected"]),
                         absence_statements=rep["run"].get("absence_statements", 0),
                         overclaims=over, seconds=secs,
                         empty_sections=[s["role"] for s in rep["sections"] if not s["claims"]]))
        out.append(rep)
    return out, rows


def report_metrics(rows):
    sup, n = sum(r["supported"] for r in rows), sum(r["drafted"] for r in rows)
    first, rej = sum(r["first_draft_supported"] for r in rows), sum(r["rejected"] for r in rows)
    neg = [r for r in rows if r["kind"] == "negative_control"]
    return {
        "evidence_supported_claim_rate": metric(_rate(sup, n), n,
                                                "drafted claims that end up passing every verifier check (citations in "
                                                "subgraph, numbers in cited evidence, verifier model), after at most one "
                                                "rewrite of a rejected claim", ci95=wilson(sup, n)),
        "first_draft_supported_rate": metric(_rate(first, n), n, "drafted claims passing every check as first written",
                                             ci95=wilson(first, n)),
        "unsupported_claim_rate": metric(_rate(rej, n), n, "drafted claims removed by the verifier (never shown)",
                                         ci95=wilson(rej, n)),
        "repaired_claims": metric(sum(r["repaired"] for r in rows), n, "rejected claims rewritten once and then passing"),
        "absence_statements": metric(sum(r.get("absence_statements", 0) for r in rows), len(rows),
                                     "drafted statements that cite nothing and only say the facts are insufficient "
                                     "(not claims: dropped before verification, not counted in the rates)"),
        "mixed_claims": metric(sum(r.get("support_status", {}).get("mixed", 0) for r in rows), sup,
                               "shown claims the judge finds contradicted by other facts retrieved for the question"),
        "unchecked_claims": metric(sum(r.get("support_status", {}).get("unchecked", 0) for r in rows), sup,
                                   "shown claims whose contradiction check did not run (judge failed): they passed "
                                   "the verifier but are not counted as judged"),
        "causal_wording_rejections": metric(sum(r.get("causal_rejections", 0) for r in rows), n,
                                            "claims still rejected for causal wording on post hoc / curated evidence"),
        "nct_id_mismatches": metric(sum(r["nct_mismatches"] for r in rows), sup,
                                    "shown claims giving a trial an NCT id the registry assigns to another trial"),
        "negative_control_overclaims": metric(sum(len(r["overclaims"]) for r in neg), len(neg),
                                              "claims labelled prospectively_confirmed on negative-control questions"),
        "latency_seconds": metric(round(sum(r["seconds"] for r in rows) / (len(rows) or 1), 2), len(rows),
                                  "mean seconds per report", per_question={r["id"]: r["seconds"] for r in rows}),
    }


BASELINE_SYSTEM = ("Answer the clinical drug-development question from your own knowledge. Return JSON "
                   "{\"claims\": [{\"text\": \"...\"}]} with at most 8 short factual claims; include numbers where known.")


BASELINE_SCHEMA = {"type": "object", "required": ["claims"], "properties": {"claims": {"type": "array", "items": {
    "type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}}}}
VERDICTS = ["supported", "contradicted", "not_addressed"]
JUDGE_SCHEMA = {"type": "object", "required": ["verdict", "reason"], "properties": {
    "verdict": {"type": "string", "enum": VERDICTS}, "reason": {"type": "string"}}}
JUDGE_SYSTEM = ("You judge one CLAIM against the FACTS (registry records, curated records, database statements). "
                "verdict = supported: the facts state it, numbers included; contradicted: the facts state something "
                "incompatible (a different number, identifier, outcome or direction); not_addressed: the facts neither "
                "confirm nor contradict it. Use only the FACTS.")


def judge(nim, kg, text, edges, facts):
    """Same judgement for both systems -> 'supported' | 'contradicted' | 'not_addressed' | 'unjudged'.
    A claim with a number absent from the evidence is never 'supported' (kg_query.verify_answer by value)."""
    nums_ok = not kg_query.verify_answer(text + " " + " ".join(f"[{e}]" for e in edges), kg,
                                         allowed=edges, conventions=True)["unsupported_numbers"]
    msgs = [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": "CLAIM: " + text + "\nFACTS:\n" + "\n".join(facts)}]
    for extra in ([], [{"role": "user", "content": "Reply with one short JSON object only."}]):
        try:
            v = nim.chat("judge", msgs + extra, schema=JUDGE_SCHEMA).data["verdict"]
            break
        except ValueError:
            v = "unjudged"
    return "not_addressed" if v == "supported" and not nums_ok else v


def comparison(nim, kg, questions, reps):
    """LLM-only comparison: the same model answers without the graph. Every claim of BOTH systems is judged by the
    same judge against the same evidence: the union of the facts TrialMindKG retrieved for that question.
    Returns per-claim rows [dict(id, system, claim, verdict, nct_mismatches)] or None without NIM."""
    G = retrieval.build_graph(kg)
    rows = []
    for q, rep in zip(questions, reps):
        try:
            r = nim.chat("baseline", [{"role": "system", "content": BASELINE_SYSTEM},
                                      {"role": "user", "content": q["question"]}], schema=BASELINE_SCHEMA)
        except nimmod.OfflineUnavailable:
            return None
        except ValueError:
            r = None
        edges = sorted({e for role in orchestrator.ORDER
                        for e in retrieval.subgraph_for(kg, G, rep["seeds"], role).edges})
        facts = retrieval.facts(kg, edges)
        llm = [c.get("text", "") if isinstance(c, dict) else str(c) for c in ((r.data if r else None) or {}).get("claims", [])]
        ours = [c["claim"] for s in rep["sections"] for c in s["claims"]]
        try:                                            # document-retrieval baseline over the program's raw documents
            rag = [c.get("text", "") for c in rag_baseline.answer(nim, q["question"], q.get("program") or "")["claims"]]
        except ValueError:
            rag = []
        for system, claims in (("llm_only", llm), ("rag", rag), ("trialmindkg", ours)):
            for text in claims:
                rows.append(dict(id=q["id"], kind=q["kind"], system=system, claim=text,
                                 verdict=judge(nim, kg, text, edges, facts),
                                 nct_mismatches=nct_mismatches(text, kg)))
    return rows


JUDGE_EVIDENCE_NOTE = (" [the evidence is the facts TrialMindKG itself retrieved for that question, the same for every "
                       "system: this measures agreement with that evidence, not independent truth or complete coverage]")


def comparison_metrics(rows):
    if rows is None:
        return {"llm_only_baseline_supported_rate": metric(None, 0, "LLM-only claims supported by the graph evidence",
                                                           "needs NIM")}
    out = {}
    for system in ("llm_only", "rag", "trialmindkg"):
        rs = [r for r in rows if r["system"] == system]
        n = len(rs)
        k = {v: sum(r["verdict"] == v for r in rs) for v in VERDICTS + ["unjudged"]}
        name = {"llm_only": "llm_only_baseline", "rag": "rag_baseline", "trialmindkg": "trialmindkg_judged"}[system]
        who = {"llm_only": "LLM-only (same model, no graph)",
               "rag": "Document RAG (same model, program documents, no graph)",
               "trialmindkg": "TrialMindKG report"}[system]
        out[f"{name}_supported_rate"] = metric(_rate(k["supported"], n), n,
                                               f"{who} claims the judge finds supported by the question's graph evidence"
                                               f"{JUDGE_EVIDENCE_NOTE}",
                                               ci95=wilson(k["supported"], n), verdicts=k)
        out[f"{name}_contradicted_rate"] = metric(_rate(k["contradicted"], n), n,
                                                  f"{who} claims the judge finds contradicted by that evidence",
                                                  ci95=wilson(k["contradicted"], n))
        out[f"{name}_nct_id_mismatches"] = metric(sum(len(r["nct_mismatches"]) for r in rs), n,
                                                  f"{who}: NCT ids given to the wrong trial (registry check, no model)")
    out["comparison_claims"] = rows
    return out


# ------------------------------------------------------------------ run
def run(nim=None, kg=None, questions=None, all_programs=True):
    """questions: benchmark set (default data/curated/benchmark_questions.csv). all_programs adds one template
    question per other curated program; 'reports' / 'comparison' are reported for the benchmark set and for all."""
    nim, kg = nim or nimmod.NIM(), kg or KGIndex()
    questions = questions or load_questions()
    done = {q.get("program") for q in questions}
    extra = [q for q in program_questions(kg) if q["program"] not in done] if all_programs else []
    s = dict(backend=nim.backend, graph_version=kg.version,
             note=("offline stand-ins: rule-based placeholders that prove the pipeline runs; these are NOT model "
                   "scores" if nim.backend == "offline" else "NVIDIA NIM models from config/nim_models.yaml"))
    s["graph"] = graph_quality(kg)
    s["graph"]["entity_mapping_precision"] = mapping_precision(kg)
    s["agents"] = agents(nim, kg)
    qs = questions + extra
    reps, rows = reports(nim, kg, qs)
    comp = comparison(nim, kg, qs, reps)
    bench = {q["id"] for q in questions}
    s["reports"] = report_metrics([r for r in rows if r["id"] in bench])
    s["reports"].update(comparison_metrics(None if comp is None else [r for r in comp if r["id"] in bench]))
    s["reports"]["per_question"] = [r for r in rows if r["id"] in bench]
    if extra:
        s["reports_all_programs"] = report_metrics(rows)
        s["reports_all_programs"].update(comparison_metrics(comp))
        s["reports_all_programs"]["per_question"] = rows
    return s


def to_markdown(s):
    lines = ["# TrialMindKG evaluation", "", f"Backend: **{s['backend']}** - {s['note']}", "",
             "| Group | Metric | Value | n | Status | Definition |", "|---|---|---|---|---|---|"]
    for grp in ("graph", "agents", "reports", "reports_all_programs"):
        for k, m in s.get(grp, {}).items():
            if isinstance(m, dict) and "definition" in m:
                v = "-" if m["value"] is None else m["value"]
                if m.get("ci95"):
                    v = f"{v} (95% CI {m['ci95'][0]}-{m['ci95'][1]})"
                lines.append(f"| {grp} | {k} | {v} | {m['n']} | {m['status']} | {m['definition']} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    s = run()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"summary_{s['backend']}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False, default=str))
    (OUT / f"summary_{s['backend']}.md").write_text(to_markdown(s))
    print(to_markdown(s))
