"""
Contradiction checks between sources (P5b). Nothing is changed in the graph: every row is for review.

    python pipelines/contradictions.py      # -> outputs/contradiction_report.csv

1. status_conflicts(kg): the curated table says a program's trial(s) STOPPED (futility, early termination) but the
   registry lists every trial as COMPLETED - or the curated text says nothing about stopping while the registry says
   TERMINATED with a reason. (Completed-after-futility is possible, so these are 'check', not errors.)
2. number_conflicts(kg, texts): statistics stated in curated / confirmed finding texts (hazard ratio, odds ratio,
   rate ratio, p value, percentage) compared with the posted registry results of the same program's trials:
   matches | close_to_registry_value (|difference| <= 10% but not equal: possible transcription mismatch) |
   not_in_registry_results (e.g. from a publication; not a contradiction by itself).
3. AACT vs ClinicalTrials.gov API record comparison is in outputs/aact_equivalence.csv (aact.py freeze).

Functions: stats_in_text(text) -> [(kind, value)];  registry_numbers(kg, trials) -> {float};
           status_conflicts(kg) -> rows;  number_conflicts(kg, texts) -> rows;  run(kg) -> rows
"""
import csv, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "contradiction_report.csv"
STAT = re.compile(r"\b(hazard ratio|HR|odds ratio|OR|rate ratio|RR|risk ratio|p\s*[=<>]|difference)\s*(?:of|was|=|:)?\s*"
                  r"(-?\d+(?:\.\d+)?)|(-?\d+(?:\.\d+)?)\s*%", re.I)
STOPPED = re.compile(r"\b(stopped|futility|terminated|halted|discontinued early|early termination)\b", re.I)


def stats_in_text(text):
    out = []
    for m in STAT.finditer(text or ""):
        if m.group(2):
            out.append((re.sub(r"\s+", " ", m.group(1).lower()), float(m.group(2))))
        elif m.group(3):
            out.append(("%", float(m.group(3))))
    return out


def registry_numbers(kg, trials):
    nums = set()
    for t in trials:
        for e in kg.out(t, "tmkg:has_outcome_measure"):
            o = kg.nodes[e["object"]]
            for a in json.loads(o.get("analyses") or "[]"):
                for k in ("param_value", "ci_lower", "ci_upper", "p_value"):
                    try:
                        nums.add(float(str(a.get(k)).lstrip("<>=")))
                    except (TypeError, ValueError):
                        pass
            for m in json.loads(o.get("measurements") or "[]"):
                try:
                    nums.add(float(m.get("value")))
                except (TypeError, ValueError):
                    pass
    return nums


def _trials(kg, pid):
    return [e["object"] for e in kg.out(pid, "tmkg:has_trial")]


def status_conflicts(kg):
    rows = []
    for pid in sorted(n for n, v in kg.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram"):
        text = kg.nodes[pid].get("what_failed") or ""
        status = {t: kg.nodes[t].get("status") for t in _trials(kg, pid)}
        if not status:
            continue
        says_stopped = bool(STOPPED.search(text))
        terminated = {t for t, s in status.items() if s in ("TERMINATED", "SUSPENDED", "WITHDRAWN")}
        if says_stopped and not terminated:
            rows.append(dict(check="status", program=pid, item="what_failed", statement=text,
                             registry=json.dumps(status), result="curated says stopped; registry lists no trial as "
                             "terminated - check (a trial can complete after a futility decision)"))
        elif terminated and not says_stopped:
            rows.append(dict(check="status", program=pid, item="what_failed", statement=text,
                             registry=json.dumps({t: [status[t], kg.nodes[t].get("why_stopped")] for t in terminated}),
                             result="registry says terminated; curated text does not mention stopping - check"))
    return rows


def number_conflicts(kg, texts):
    """texts: [(program, item, text)]"""
    rows = []
    for pid, item, text in texts:
        nums = registry_numbers(kg, _trials(kg, pid))
        for kind, v in sorted(set(stats_in_text(text))):
            dec = len(str(v).split(".")[1]) if "." in str(v) and not str(v).endswith(".0") else 0
            rounded = sorted(x for x in nums if x != v and round(x, dec) == v)
            close = sorted((x for x in nums if x != v and abs(x - v) <= 0.1 * max(abs(v), 1e-9)), key=lambda x: abs(x - v))
            res = ("matches_registry" if v in nums else f"matches_registry_rounded {rounded[0]}" if rounded
                   else f"close_to_registry_value {close[0]} (possible mismatch)" if close else "not_in_registry_results")
            rows.append(dict(check="number", program=pid, item=item, statement=f"{kind} {v}", registry="",
                             result=res))
    return rows


def finding_texts(kg):
    """Curated program / event texts and owner-confirmed finding values, with their program."""
    out = []
    for pid in sorted(n for n, v in kg.nodes.items() if v["category"] == "tmkg:ClinicalTrialProgram"):
        out.append((pid, "what_failed", kg.nodes[pid].get("what_failed") or ""))
        for pred in ("tmkg:has_post_failure_analysis", "tmkg:has_repurposing_pivot", "tmkg:has_document_finding"):
            for e in kg.out(pid, pred):
                n = kg.nodes[e["object"]]
                out.append((pid, e["object"], " ".join(str(n.get(k) or "") for k in (
                    "original_problem", "analysis_showed", "outcome_evidence", "what_changed", "consequence", "value"))))
    return out


def run(kg, out=OUT):
    rows = status_conflicts(kg) + number_conflicts(kg, finding_texts(kg))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["check", "program", "item", "statement", "registry", "result"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return rows


if __name__ == "__main__":
    from kg_query import KGIndex
    rows = run(KGIndex())
    from collections import Counter
    print(Counter(r["result"].split(" (")[0].split(";")[0] for r in rows), f"-> {OUT}")
