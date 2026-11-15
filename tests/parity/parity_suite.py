"""
Offline-vs-hosted parity for the answer path (CC-8): the same pinned question must be answered by the SAME kg_query function
and return the SAME evidence edge-id set whichever backend writes the query - the offline stand-in or hosted Nemotron.

  load_questions()                         -> the 25 pinned questions (questions.yaml)
  classify(cypher)                         -> the kg_query function a query implements ('other' if none)
  reference(kg, q)                         -> the function's own result {rows, evidence, ...}
  answer(querier, q)                       -> {id, function, cypher, evidence [sorted], backend, model, cached, problems}
  judge(q, ans, ref)                       -> {function_ok, evidence_ok, passed, reason}   (against the function, not text)
  run_suite(kg, nim, questions=None)       -> one row per question: the answer + its judgement
  agreement(rows_a, rows_b)                -> {n, same_function, same_evidence, both_pass, disagreements: [...]}
  report_markdown(...)                     -> the table written to outputs/eval/parity_*.md (numbers computed from the rows)

The expected ids are never written down: they are computed by the kg_query function (rule 10: agents answer only through
kg_query functions and cite edge ids). tests/parity/run_parity.py runs it against a real backend; the unit tests stub the HTTP
layer. Text of the query is not compared: two queries that return the same edges through the same relationships agree.
"""
import re
from pathlib import Path

import yaml

QUESTIONS = Path(__file__).with_name("questions.yaml")
EVIDENCE_NOTES = ("equal", "subset", "covers")


def load_questions(path=QUESTIONS):
    """Takes the yaml path. Returns the list of question dicts (id, question, function, params, relation)."""
    qs = yaml.safe_load(Path(path).read_text())["questions"]
    for q in qs:
        q["params"] = {k: str(v) for k, v in (q.get("params") or {}).items()}
    return qs


def rel_types(cypher):
    """Relationship type names used in the MATCH patterns of a query, in order (A|B alternatives flattened)."""
    out = []
    for m in re.finditer(r"\[\w*:([A-Za-z_|]+)", cypher or ""):
        out += m.group(1).split("|")
    return out


def classify(cypher):
    """Takes a query. Returns the kg_query function it implements, from the relationship types it walks (and, for the two
    functions that both walk has_trial, the safety columns): program_trials, trial_safety, trial_outcomes, program_history,
    drug_targets, shared_targets, trial_drugs, trial_publications, or 'other'."""
    rels = rel_types(cypher)
    s = set(rels)
    if "has_trial" in s:
        return "trial_safety" if "ae_groups" in cypher else "program_trials"
    if "has_outcome_measure" in s:
        return "trial_outcomes"
    if s & {"has_post_failure_analysis", "has_repurposing_pivot"}:
        return "program_history"
    if "has_intervention" in s and "interacts_with" in s:
        return "shared_targets"
    if "interacts_with" in s:
        return "drug_targets"
    if s & {"tests_intervention", "has_comparator_drug", "has_background_drug"}:
        return "trial_drugs"
    if "has_publication" in s:
        return "trial_publications"
    return "other"


def reference(kg, q):
    """Takes the graph and a question. Returns the result of the pinned kg_query function with the pinned parameters."""
    return getattr(kg, q["function"])(**q["params"])


def answer(querier, q):
    """Takes a Querier and a question dict. Returns the backend's answer reduced to what parity compares. A backend that
    raises is an answer too (function 'error')."""
    try:
        res = querier.ask(q["question"])
    except Exception as err:                                   # API down, 404 for this account, no stored snapshot ...
        return dict(id=q["id"], function="error", cypher="", evidence=[], backend=getattr(querier.nim, "backend", "?"),
                    model="", cached=False, problems=[f"{type(err).__name__}: {str(err)[:200]}"], attempts=0)
    return dict(id=q["id"], function=classify(res.get("cypher", "")), cypher=res.get("cypher", ""),
                evidence=sorted(res.get("evidence") or []), backend=res.get("backend"), model=res.get("model"),
                cached=bool(res.get("cached")), problems=res.get("problems") or [], attempts=len(res.get("attempts") or []))


def judge(q, ans, ref):
    """Takes the question, an answer() and the reference result. Returns {function_ok, evidence_ok, passed, reason}:
    the function must be the pinned one; evidence must equal the reference ids (relation 'equal'), be a non-empty subset
    ('subset'), or a non-empty subset that touches every reference row ('covers')."""
    got, want = set(ans["evidence"]), set(ref["evidence"])
    fn_ok = ans["function"] == q["function"]
    rel = q["relation"]
    if rel == "equal":
        ev_ok = got == want and bool(want)
    elif rel == "subset":
        ev_ok = bool(got) and got <= want
    elif rel == "covers":
        ev_ok = bool(got) and got <= want and all(set(r.get("evidence", [])) & got for r in ref.get("rows", []))
    else:
        raise ValueError(f"unknown relation {rel!r} in {q['id']}")
    reason = ""
    if not fn_ok:
        reason = f"function {ans['function']!r}, expected {q['function']!r}"
    elif not ev_ok:
        reason = f"evidence differs: got {len(got)} ids, reference {len(want)}; missing {len(want - got)}, extra {len(got - want)}"
    if ans["problems"] and not ans["evidence"]:
        reason = reason or f"no valid query: {ans['problems'][0][:120]}"
    return dict(function_ok=fn_ok, evidence_ok=ev_ok, passed=fn_ok and ev_ok, reason=reason)


def run_suite(kg, nim, questions=None):
    """Takes the graph, a NIM and optionally the questions. Returns one row per question: the answer() fields plus
    'question', 'expected_function', 'relation', 'n_reference' and the judge() fields."""
    import querier as Q
    qr = Q.Querier(nim, kg)
    rows = []
    for q in questions or load_questions():
        ref = reference(kg, q)
        ans = answer(qr, q)
        rows.append(dict(ans, question=q["question"], expected_function=q["function"], relation=q["relation"],
                         n_reference=len(set(ref["evidence"])), **judge(q, ans, ref)))
    return rows


def agreement(rows_a, rows_b):
    """Takes the rows of two runs (e.g. offline and hosted) over the same questions. Returns {n, same_function,
    same_evidence, both_pass, disagreements}: per question whether both used the same function and returned the same
    edge-id set; disagreements lists id, the two functions and the evidence-size difference."""
    b = {r["id"]: r for r in rows_b}
    out = dict(n=0, same_function=0, same_evidence=0, both_pass=0, disagreements=[])
    for r in rows_a:
        o = b.get(r["id"])
        if o is None:
            continue
        out["n"] += 1
        same_fn, same_ev = r["function"] == o["function"], r["evidence"] == o["evidence"]
        out["same_function"] += same_fn
        out["same_evidence"] += same_ev
        out["both_pass"] += bool(r["passed"] and o["passed"])
        if not (same_fn and same_ev):
            out["disagreements"].append(dict(id=r["id"], function_a=r["function"], function_b=o["function"],
                                             n_a=len(r["evidence"]), n_b=len(o["evidence"]), only_a=len(set(r["evidence"]) - set(o["evidence"])),
                                             only_b=len(set(o["evidence"]) - set(r["evidence"]))))
    return out


def report_markdown(runs, agree=None):
    """Takes {label: rows} and optionally the agreement() of the first two. Returns the Markdown report; every count is
    computed from the rows."""
    lines = ["# Parity of the answer path: offline stand-in vs hosted model", ""]
    for label, rows in runs.items():
        n = len(rows)
        lines.append(f"- {label}: {sum(r['passed'] for r in rows)} of {n} questions answered by the pinned function with "
                     f"the reference evidence ({sum(r['function_ok'] for r in rows)} right function, "
                     f"{sum(r['cached'] for r in rows)} answered from the snapshot cache; backend "
                     f"{', '.join(sorted({str(r['backend']) for r in rows}))}).")
    if agree:
        lines.append(f"- agreement between the first two runs: {agree['same_function']} of {agree['n']} same function, "
                     f"{agree['same_evidence']} of {agree['n']} same evidence-id set, {agree['both_pass']} both correct.")
    labels = list(runs)
    lines += ["", "| id | question | function | " + " | ".join(f"{l}" for l in labels) + " |",
              "|---|---|---|" + "---|" * len(labels)]
    by = {l: {r["id"]: r for r in rows} for l, rows in runs.items()}
    for r in runs[labels[0]]:
        cells = []
        for l in labels:
            x = by[l].get(r["id"])
            cells.append("-" if x is None else ("ok" if x["passed"] else "FAIL: " + (x["reason"] or "?")[:70]))
        lines.append(f"| {r['id']} | {r['question'][:60]} | {r['expected_function']} | " + " | ".join(cells) + " |")
    if agree and agree["disagreements"]:
        lines += ["", "## Disagreements", ""] + [f"- {d['id']}: {d['function_a']} ({d['n_a']} ids) vs {d['function_b']} ({d['n_b']} ids); "
                                                  f"{d['only_a']} only in the first, {d['only_b']} only in the second" for d in agree["disagreements"]]
    return "\n".join(lines) + "\n"
