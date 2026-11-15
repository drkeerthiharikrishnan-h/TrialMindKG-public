"""
Query orchestrator (LangGraph): question -> Intelligence Report with an evidence card for every claim.

    python pipelines/orchestrator.py "Why did TULIP-1 miss its primary endpoint, and what changed later?"

Flow (a directed acyclic LangGraph):
  seeds -> retrieve (PCST subgraph per specialist) -> failure_diagnostician -> population_stratifier
        -> indication_explorer -> redesign_advisor -> verify (deterministic checks + verifier model) -> report
The controller (verify + report) drops every claim without traceable support; dropped claims are listed in the
report so nothing disappears silently. The run log records backend, model ids, frozen request keys and the graph
version, so a report can be replayed (TMKG_SNAPSHOT_MODE=offline).

A specialist whose reply is unusable leaves a note in its own section (specialists.run_specialist_safe); the other
sections are still written. run.note says which backends actually answered (run_note), so an export never claims the
hosted models when the stand-in answered; to_markdown prints the answer label (usability.answer_label) under it.

Functions: find_seeds(kg, question) -> [node ids];  build_app(kg, nim) -> compiled LangGraph;
           ask(question, kg=None, nim=None) -> report dict;  to_markdown(report) -> str;
           run_note(backend, calls) -> str
"""
import json, re, sys, time
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from langgraph.graph import StateGraph, START, END
import claims as claims_rules, nim as nimmod, retrieval, specialists
from kg_query import KGIndex

ROOT = Path(__file__).resolve().parents[1]
ORDER = ["failure_diagnostician", "population_stratifier", "indication_explorer", "redesign_advisor"]


class State(TypedDict, total=False):
    question: str
    seeds: list
    subgraphs: dict
    claims: list
    notes: dict
    verified: list
    rejected: list
    report: dict


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_seeds(kg, question):
    """Graph entities named in the question: NCT ids, trial acronyms, program trial names, drug names.
    Exact (normalised) matches only - unmatched names are reported, not guessed."""
    q = question.lower()
    qn = _norm(question)
    seeds = {f"NCT:{n}" for n in re.findall(r"NCT\d{8}", question.upper()) if f"NCT:{n}" in kg.nodes}
    words = set(re.findall(r"[a-z0-9+\-]+", q))
    for nid, n in kg.nodes.items():
        cat = n["category"]
        if cat == "biolink:ClinicalTrial" and n.get("acronym") and len(_norm(n["acronym"])) >= 3 \
                and re.search(r"(?<![a-z0-9])" + re.escape(n["acronym"].lower()) + r"(?![a-z0-9])", q):
            seeds.add(nid)
        elif cat == "tmkg:ClinicalTrialProgram":
            label = n.get("name", "")
            trials = label.split(" — ", 1)[-1].split("/")
            if any(len(_norm(t)) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(t.strip().lower()) + r"(?![a-z0-9])", q)
                   for t in trials):
                seeds.add(nid)
        elif cat == "biolink:Drug" and n.get("name") and _norm(n["name"]) in {_norm(w) for w in words}:
            seeds.add(nid)
    # a program's trials are seeds too, and a trial's program
    for s in list(seeds):
        if s.startswith("P"):
            seeds |= {e["object"] for e in kg.out(s, "tmkg:has_trial")}
        if s.startswith("NCT:"):
            seeds |= {e["subject"] for e in kg.inn(s, "tmkg:has_trial")}
    return sorted(seeds)


def graph_json(kg, nodes, edges):
    """Nodes and edges of a subgraph for drawing: ids, short labels, categories, predicates."""
    return dict(nodes=[dict(id=n, label=retrieval.label(kg.nodes[n], kg), category=kg.nodes[n]["category"])
                       for n in nodes if n in kg.nodes],
                edges=[dict(id=e, source=kg.edges[e]["subject"], target=kg.edges[e]["object"],
                            predicate=kg.edges[e]["predicate"], evidence_type=kg.edges[e].get("evidence_type"))
                       for e in edges if e in kg.edges])


def flow_links(sections):
    """How one finding informs the next: pairs of claims in DIFFERENT sections that cite at least one same edge.
    -> [dict(source=dict(role, index), target=dict(role, index), shared=[edge ids])] in section order."""
    items = [(s["role"], i, {e["id"] for e in c["evidence"]}) for s in sections for i, c in enumerate(s["claims"])]
    out = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            (ra, ia, ea), (rb, ib, eb) = items[a], items[b]
            if ra != rb and ea & eb:
                out.append(dict(source=dict(role=ra, index=ia), target=dict(role=rb, index=ib), shared=sorted(ea & eb)))
    return out


def run_note(backend, calls):
    """The one-line note of a report's run, computed from who actually answered (review finding SEC-11: the exports said
    'Generated with NVIDIA NIM models' even when the stand-in had answered after a failed hosted call). Takes the client's
    backend ('nim' | 'offline') and its call log [(role, model, backend, snapshot key)]. Returns the sentence."""
    n = len(calls)
    n_off = sum(1 for c in calls if c[2] == nimmod.OFFLINE)
    if backend == "offline":
        return "Generated by the offline stand-in (rule-based, no language model)."
    if n and n_off == n:
        return ("Generated by the offline stand-in (rule-based, no language model): the hosted model could not be reached "
                f"for any of the {n} steps of this report.")
    if n_off:
        return (f"Generated with the NVIDIA NIM models listed above, except that {n_off} of the {n} steps of this report were answered "
                "by the offline stand-in (rule-based, no language model) because the hosted model could not be reached.")
    return "Generated with NVIDIA NIM models listed above."


def build_app(kg, nim):
    G = retrieval.build_graph(kg)

    def seeds(state):
        return {"seeds": find_seeds(kg, state["question"])}

    def retrieve(state):
        return {"subgraphs": {r: retrieval.subgraph_for(kg, G, state["seeds"], r) for r in ORDER}, "claims": [], "notes": {}}

    def make_specialist(role):
        def run(state):
            sg = state["subgraphs"][role]
            claims, note = specialists.run_specialist_safe(nim, role, state["question"], sg)
            notes = dict(state.get("notes") or {}, **({role: note} if note else {}))
            return {"claims": state["claims"] + claims, "notes": notes}
        return run

    def verify(state):
        """Every claim is checked; a rejected claim gets one repair attempt, checked the same way."""
        ok, bad = [], []
        for c in [c for c in state["claims"] if not c.get("absence")]:
            sg = state["subgraphs"][c["role"]]
            v = specialists.verify_claim(nim, kg, c, sg)
            c = dict(c, verification=v, first_draft_ok=v["ok"])
            if not v["ok"]:
                fixed = specialists.repair_claim(nim, c, v["reasons"], sg)
                if fixed:
                    v2 = specialists.verify_claim(nim, kg, fixed, sg)
                    if v2["ok"]:
                        ok.append(dict(fixed, verification=v2, first_draft_ok=False))
                        continue
                    c["repair"] = dict(claim=fixed["text"], reasons=v2["reasons"])
                elif v["reasons"]:
                    c["repair"] = dict(claim=None, reasons=["withdrawn or not repairable"])
            (ok if v["ok"] else bad).append(c)
        return {"verified": ok, "rejected": bad}

    def report(state):
        sections = []
        shown = claims_rules.dedupe(state["verified"], ORDER)            # same claim in two sections: shown once
        all_facts = sorted({f for sg in state["subgraphs"].values() for f in sg.facts})
        for role in ORDER:
            title = specialists.ROLES[role][0]
            items = []
            for c in [c for c in shown if c["role"] == role]:
                cards = [kg.provenance(e)["rows"][0] for e in c["evidence_edges"]]
                verdict = claims_rules.contradiction_verdict(nim, c["text"], all_facts)
                items.append(dict(claim=c["text"], evidence_status=c["evidence_status"], uncertainty=c["uncertainty"],
                                  repaired_from=c.get("repaired_from"), also_in=c.get("also_in"),
                                  support_status=claims_rules.support_status(c, verdict), contradiction_check=verdict,
                                  indicators=claims_rules.indicators(c, kg),
                                  evidence=[{k: card.get(k) for k in ("id", "subject", "predicate", "object", "source",
                                                                       "source_record", "evidence_type", "retrieved",
                                                                       "raw_response")} for card in cards]))
            sg = state["subgraphs"][role]
            sections.append(dict(role=role, title=specialists.section_title(role, len(items)), claims=items,
                                 subgraph=dict(nodes=len(sg.nodes), edges=len(sg.edges), edge_ids=list(sg.edges)),
                                 graph=graph_json(kg, sg.nodes, sg.edges),
                                 note=None if items else (state.get("notes") or {}).get(role)
                                 or "no verified claim: the subgraph lacks the needed evidence"))
        return {"report": dict(
            question=state["question"], seeds=state["seeds"], sections=sections, flow_links=flow_links(sections),
            rejected=[dict(role=c["role"], claim=c["text"], reasons=c["verification"]["reasons"], repair=c.get("repair"))
                      for c in state["rejected"]],
            run=dict(backend=nim.backend, models=sorted({(k, m) for k, m, *_ in nim.calls}),
                     absence_statements=sum(bool(c.get("absence")) for c in state["claims"]),
                     model_calls=[dict(role=k, model=m, backend=b, snapshot_key=key) for k, m, b, key in nim.calls],
                     graph_version=kg.version,
                     note=run_note(nim.backend, nim.calls)))}

    g = StateGraph(State)
    g.add_node("seeds", seeds)
    g.add_node("retrieve", retrieve)
    for r in ORDER:
        g.add_node(r, make_specialist(r))
    g.add_node("verify", verify)
    g.add_node("report_node", report)
    chain = ["seeds", "retrieve"] + ORDER + ["verify", "report_node"]
    g.add_edge(START, chain[0])
    for a, b in zip(chain, chain[1:]):
        g.add_edge(a, b)
    g.add_edge(chain[-1], END)
    return g.compile()


def ask(question, kg=None, nim=None):
    kg, nim = kg or KGIndex(), nim or nimmod.NIM()
    t = time.time()
    out = build_app(kg, nim).invoke({"question": question})["report"]
    out["run"]["seconds"] = round(time.time() - t, 2)
    return out


def to_markdown(r):
    """Takes a report (ask). Returns it as Markdown; the answer label (stand-in, stored answer, backup model: the report's
    'label' key, else usability.answer_label of its run) is printed under the run note when it has a text."""
    lines = [f"# TrialMindKG Intelligence Report", "", f"**Question:** {r['question']}", "",
             f"*{r['run']['note']}* Graph version {str(r['run']['graph_version'].get('snapshot'))[:12]}.", ""]
    label = r.get("label")
    if label is None:
        import usability
        label = usability.answer_label(r.get("run", {}))
    if label.get("text"):
        lines += [f"**{label['text']}**", ""]
    for s in r["sections"]:
        lines.append(f"## {s['title']}")
        if not s["claims"]:
            lines.append(f"_{s['note']}_")
        for c in s["claims"]:
            refs = ", ".join(f"`{e['id']}`" for e in c["evidence"])
            fix = " (rewritten once after a failed check)" if c.get("repaired_from") else ""
            ind = c.get("indicators") or {}
            status = (c.get("support_status") or "").replace("_", " ")
            lines.append(f"- {c['claim']}  \n  **{status}** - *{c['evidence_status'].replace('_', ' ')}; source strength "
                         f"{ind.get('evidence_strength')}, id matching {ind.get('mapping_confidence')}, model uncertainty "
                         f"{c['uncertainty']}*{fix} - evidence {refs}")
        lines.append("")
    if r["rejected"]:
        lines.append("## Claims removed by the verifier")
        lines += [f"- ({c['role']}) {c['claim']} - {'; '.join(c['reasons'])}" for c in r["rejected"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Why did TULIP-1 miss its primary endpoint, and what changed later?"
    rep = ask(q)
    slug = re.sub(r"[^a-z0-9]+", "_", q.lower())[:60].strip("_")
    out = ROOT / "outputs" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{slug}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str))
    (out / f"{slug}.md").write_text(to_markdown(rep))
    print(to_markdown(rep))
