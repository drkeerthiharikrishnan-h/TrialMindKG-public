"""
Reads the filled stranger-test log (docs/STRANGER_TEST_RESULTS.md, from docs/STRANGER_TEST_RESULTS_TEMPLATE.md) for the
scorecard check `stranger_test_logged` (kind: stranger_results in loop/loop.py).

  parse_results(text) -> dict(fields, tasks, answers, placeholders)    the markdown tables as data
  check_results(path) -> (ok, detail)                                  detail names the first thing missing, or counts

It checks that a real, finished log exists, not that the tool did well: failures logged honestly pass, a missing,
templated or half-filled log fails. Standard library only.
"""
import re
from pathlib import Path

OUTCOMES = ("done", "partly", "not done")
N_TASKS, N_ANSWERS = 5, 5
FIELDS = {"date": "Date", "tester": "Tester code", "background": "Tester background", "knows_kg": "Knows what a knowledge graph is",
          "facilitator": "Facilitator", "recorded": "Screen recorded", "address": "Address tested",
          "helped": "Facilitator helped only with the scripted hints"}


def _rows(text, heading):
    """The cells of the table rows under '## heading' (header and separator rows skipped)."""
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    out = []
    for line in (m.group(1) if m else "").splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s\-|]+\|$", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] not in ("#",):
                out.append(cells)
    return out


def parse_results(text):
    """Takes the log text. Returns {fields: {key: value}, tasks: [{n, task, seconds, outcome, hints, note}], answers:
    [{n, question, answer}], placeholders: how many '<fill' markers remain, changes: the 'What we will change' text}."""
    fields = {}
    for key, label in FIELDS.items():
        m = re.search(rf"^- {re.escape(label)}[^:\n]*:[ \t]*(.*)$", text, re.M)
        fields[key] = m.group(1).strip() if m else ""
    tasks = [dict(n=c[0], task=c[1], seconds=c[2], outcome=c[3].lower(), hints=c[4], note=c[5])
             for c in _rows(text, "Tasks") if len(c) >= 6]
    answers = [dict(n=c[0], question=c[1], answer=c[2]) for c in _rows(text, "Their answers (their words)") if len(c) >= 3]
    m = re.search(r"^## What we will change\s*\n(.*?)\Z", text, re.M | re.S)
    return dict(fields=fields, tasks=tasks, answers=answers, placeholders=len(re.findall(r"<fill", text)),
                changes=(m.group(1).strip() if m else ""))


def check_results(path):
    """Takes the path of the filled log. Returns (ok, detail): ok only when the log is finished (no '<fill' left), the tester
    is not a knowledge-graph builder, five tasks have seconds / outcome / hints, five answers have at least three words, and
    the facilitator helped only with the scripted hints. detail = the first problem, or a count of what was logged."""
    p = Path(path)
    if not p.exists():
        return False, f"{p.name} missing: run docs/STRANGER_TEST.md and fill the template"
    r = parse_results(p.read_text(errors="replace"))
    if r["placeholders"]:
        return False, f"{r['placeholders']} '<fill' marker(s) left in {p.name}"
    f = r["fields"]
    for key in ("date", "tester", "background", "facilitator", "address"):
        if not f[key]:
            return False, f"field '{FIELDS[key]}' is empty"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f["date"]):
        return False, f"date {f['date']!r} is not YYYY-MM-DD"
    if f["knows_kg"].lower() not in ("no", "a little"):
        return False, f"tester answered {f['knows_kg']!r} to knowing knowledge graphs: not a stranger"
    if f["helped"].lower() != "yes":
        return False, "facilitator helped beyond the scripted hints (or the field is not 'yes')"
    if len(r["tasks"]) != N_TASKS:
        return False, f"{len(r['tasks'])} task rows, need {N_TASKS}"
    for t in r["tasks"]:
        if not re.fullmatch(r"\d+", t["seconds"]) or int(t["seconds"]) <= 0:
            return False, f"task {t['n']}: seconds {t['seconds']!r} is not a positive whole number"
        if t["outcome"] not in OUTCOMES:
            return False, f"task {t['n']}: outcome {t['outcome']!r} is not one of {', '.join(OUTCOMES)}"
        if not re.fullmatch(r"\d+", t["hints"]):
            return False, f"task {t['n']}: hints {t['hints']!r} is not a number"
    if len(r["answers"]) != N_ANSWERS:
        return False, f"{len(r['answers'])} answers, need {N_ANSWERS}"
    for a in r["answers"]:
        if len(a["answer"].split()) < 3:
            return False, f"answer {a['n']} has fewer than three words"
    if not r["changes"]:
        return False, "'What we will change' is empty"
    done = sum(t["outcome"] == "done" for t in r["tasks"])
    partly = sum(t["outcome"] == "partly" for t in r["tasks"])
    secs = sum(int(t["seconds"]) for t in r["tasks"])
    return True, f"tester {f['tester']}: {done} of {N_TASKS} tasks done, {partly} partly, {secs // 60} min {secs % 60} s of tasks, {N_ANSWERS} answers"
