"""
Stored answers for the worked examples and the role questions (CC-31h, review finding M8: a trial report takes 3-4 minutes).

A stored answer is the app's own reply to one question, recorded once (scripts/precompute_examples.py, run with the hosted
model) and served with "stored answer, recorded <date>" and a re-run-live button; new questions are always answered live.
Nothing is written by hand: each file is the JSON an endpoint returned, plus when and by which model it was recorded.

Files: outputs/examples/<id>.json  {id, question, kind ('trials' | 'query' | 'pharos'), endpoint, recorded (UTC ISO time),
       backend, model, answer}

  targets(examples, personas)   -> [{id, kind, question, endpoint}]  what to pre-compute: the examples that call a model
                                   (trials, query) and the role questions of the trials / Pharos-ask tabs, one per question;
                                   a Pharos question whose text is a tested question is NOT a target (review finding U5: it is
                                   answered by the tested question itself, so one question never has two answers)
  question_id(text)             -> 'q_' + 10 hex characters of the question's SHA-1 (role questions have no id of their own)
  record(target, answer, backend, model, recorded) -> the stored dict
  index(folder)                 -> [{id, question, kind, recorded, backend, model}]  for the page (no answers); a stored
                                   Pharos answer to a tested question is left out even when its file is still there
  display(stored)               -> the stored dict as the page shows it: internal program ids in a trial-graph answer become
                                   program names, decimals are printed at 3 significant figures, and the bottom line is composed
                                   again by the current rules (resummarise) from what the file holds. The file is not changed.
  resummarise(stored)           -> the stored dict with answer.summary recomputed (answer_summary), None when nothing to recompute
  load(folder, id)              -> display(the stored dict), or None (only ids of the form x01 / q_<hex> are read)
  rewrite(folder)               -> writes the recomputed bottom lines back into the files; returns the ids rewritten
                                   (python3 pipelines/stored_answers.py --rewrite)
"""
import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "outputs" / "examples"
ENDPOINTS = {"trials": "/api/ask", "query": "/api/query", "pharos": "/api/pharos/ask"}
ID_RE = re.compile(r"^(x\d{2}|q_[0-9a-f]{10})$")


def question_id(text):
    """Takes a question. Returns its stable id 'q_<10 hex>' (SHA-1 of the text, whitespace trimmed)."""
    return "q_" + hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:10]


def targets(examples, personas):
    """Takes the worked examples (persona_examples.build: {persona: [example]}) and the roles (personas.load_personas()).
    Returns [{id, kind, question, endpoint}] in page order: every example of kind trials / query (by its id), then every
    role question of tab trials / pharos (by question_id); a question already listed is not listed twice. Cards are not
    stored: they come from the card index in well under a second."""
    out, seen = [], set()
    for exs in examples.values():
        for x in exs:
            if x["kind"] in ("trials", "query") and x["question"] not in seen:
                seen.add(x["question"])
                out.append(dict(id=x["id"], kind=x["kind"], question=x["question"], endpoint=ENDPOINTS[x["kind"]]))
    for p in personas:
        for q in p.get("questions") or []:
            kind = q.get("tab")
            if kind == "pharos" and is_tested(q.get("text")):
                continue                                  # answered by the tested question (pharos_app.ask), never stored
            if kind in ("trials", "pharos") and q.get("text") and q["text"] not in seen:
                seen.add(q["text"])
                out.append(dict(id=question_id(q["text"]), kind=kind, question=q["text"], endpoint=ENDPOINTS[kind]))
    return out


def is_tested(question):
    """Takes a question. Returns True when its text is one of the tested Pharos questions (pharos_app.tested_question)."""
    import pharos_app
    return pharos_app.tested_question(question) is not None


def record(target, answer, backend, model, recorded=None):
    """Takes a target, the endpoint's JSON answer, the backend and model that produced it and the time (default now, UTC).
    Returns the stored dict."""
    return dict(id=target["id"], question=target["question"], kind=target["kind"], endpoint=target["endpoint"],
                recorded=recorded or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), backend=backend, model=model,
                answer=answer)


def index(folder=FOLDER):
    """Takes the folder. Returns [{id, question, kind, recorded, backend, model}] of every readable stored answer, by id."""
    out = []
    for p in sorted(Path(folder).glob("*.json")) if Path(folder).is_dir() else []:
        if not ID_RE.match(p.stem):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("kind") == "pharos" and is_tested(d.get("question")):
            continue                                      # U5: the tested question answers it; a stale file is not offered
        out.append({k: d.get(k) for k in ("id", "question", "kind", "recorded", "backend", "model")})
    return out


def _id_columns(cypher):
    """Takes the openCypher of a stored answer. Returns the columns its RETURN fills with a node's id ('p1.id AS program_1')."""
    m = re.search(r"\bRETURN\b(.*?)(?:\bORDER BY\b|\bLIMIT\b|$)", str(cypher or ""), re.I | re.S)
    return [x.group(1) for x in re.finditer(r"\b\w+\.id\s+AS\s+(\w+)", m.group(1), re.I)] if m else []


def program_names(rows, cypher, programs):
    """Takes the rows of a trial-graph answer, its openCypher and {program id: facts} (answer_summary.program_facts).
    Returns new rows in which a cell of an id column (a column the query filled with a node's id) that holds the internal
    id of a failed program ('P22') holds the program's name instead ('Elafibranor — RESOLVE-IT'); the internal id is
    not shown. Other cells (public ids such as CHEMBL:..., link ids, names) are unchanged."""
    cols = set(_id_columns(cypher))
    def cell(c, v):
        if isinstance(v, list):
            return [cell(c, x) for x in v]
        return (programs[v].get("name") or v) if c in cols and isinstance(v, str) and v in programs else v
    return [{c: cell(c, v) for c, v in r.items()} if isinstance(r, dict) else r for r in rows]


def resummarise(stored):
    """Takes a stored dict. Returns a copy whose answer.summary is composed again by the current rules, or None when the
    stored answer has no bottom line to recompute. A trial report: answer_summary.trial_summary(report). A Pharos answer:
    answer_summary.pharos_summary from the rows and the link facts the stored bottom line already holds (refs.edges:
    source, knowledge level, pruning verdict of every link that was looked up when it was recorded); the entity labels
    come from the stored plan. Nothing is looked up anew, so no graph is needed."""
    import answer_summary
    a = stored.get("answer")
    old = a.get("summary") if isinstance(a, dict) else None
    if not isinstance(old, dict) or old.get("error"):
        return None
    if stored.get("kind") == "trials":
        new = answer_summary.trial_summary(a)
    elif stored.get("kind") == "pharos":
        labels = {e.get("id"): e.get("label") for e in (old.get("plan") or {}).get("entities") or []}
        new = answer_summary.pharos_summary(a, (old.get("refs") or {}).get("edges") or {}, labels)
    else:
        return None
    return dict(stored, answer=dict(a, summary=new))


def display(stored):
    """Takes a stored dict. Returns it as the page shows it (module docstring); a display step that fails leaves that part
    as stored (a stored answer must always open)."""
    import answer_summary
    a = stored.get("answer")
    if not isinstance(a, dict):
        return stored
    try:
        rows = a.get("rows")
        if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
            if stored.get("kind") == "query":
                rows = program_names(rows, a.get("cypher"), answer_summary.program_facts())
            stored = dict(stored, answer=dict(a, rows=answer_summary.display_rows(rows)))
    except Exception:
        pass
    try:
        return resummarise(stored) or stored
    except Exception:
        return stored


def load(folder, sid):
    """Takes the folder and a stored-answer id. Returns the stored dict, or None (unknown or malformed id, no file)."""
    if not ID_RE.match(str(sid or "")):
        return None
    p = Path(folder) / f"{sid}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return display(d) if isinstance(d, dict) else d


def rewrite(folder=FOLDER):
    """Takes the folder. Writes the recomputed bottom line (resummarise) back into every stored answer that has one and
    whose bottom line changed. Returns the ids rewritten. Rows, claims and evidence are not touched."""
    done = []
    for p in sorted(Path(folder).glob("*.json")):
        if not ID_RE.match(p.stem):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        new = resummarise(d) if isinstance(d, dict) else None
        same = new is None or json.loads(json.dumps(new["answer"]["summary"])) == d["answer"]["summary"]   # as JSON holds it
        if not same:
            p.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
            done.append(p.stem)
    return done


if __name__ == "__main__":
    if "--rewrite" in sys.argv:
        ids = rewrite()
        print(f"bottom line recomputed in {len(ids)} stored answers: {', '.join(ids) or 'none'}")
    else:
        for row in index():
            print(row["id"], row["kind"], row["question"])
