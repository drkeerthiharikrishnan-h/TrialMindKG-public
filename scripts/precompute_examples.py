#!/usr/bin/env python3
"""
Pre-compute the stored answers of the worked examples and the role questions (CC-31h; Claude Science packet CS-7), and
(--summaries, packet PK4) the model-written summaries the app shows with them.

    python3 scripts/precompute_examples.py [--only x01,q_ab12cd34ef] [--out outputs/examples] [--dry-run]
    python3 scripts/precompute_examples.py --summaries [--only x01,pharos_demo_1] [--out outputs/summaries] [--force] [--dry-run]

Runs each question through the app IN THIS PROCESS (FastAPI TestClient: the same code path as the hosted page, no network
call of its own), so run it where the hosted model is reachable: the owner's Terminal with NVIDIA_API_KEY in .env, or the
hosted container. Writes outputs/examples/<id>.json (pipelines/stored_answers.py). A question the offline stand-in answered
is NOT stored (the page would label a stand-in as a stored hosted answer); the script says which and why.
Exit code 0 = every target stored, 1 = some were skipped.

  plan(app_module)       -> the targets (stored_answers.targets over the page's examples and roles)
  run(client, target)    -> (answer JSON, backend, model) for one target
  hosted(answer, kind)   -> True when a hosted model produced the answer (not the stand-in, not a fallback)
  batch_waits()          -> context: the hosted calls of this ahead-of-time job wait as a batch job does (FIX2 F7)
  for_storage(ans, t)    -> the answer as it is stored: no attached summary, failed programs by their recorded ids

FIX2 F7 (S08 item 1). Since PK1 a web request may spend at most 75 s on hosted calls and never waits behind an HTTP 429
(nim.web_budget); a trial report makes about 65 calls, so its later calls got the stand-in and the script (rightly) refused
to store it. This job is not a web request: inside batch_waits() every hosted call has no request deadline, BATCH_TIMEOUT s
per attempt, BATCH_ATTEMPTS tries and up to BATCH_RATE_LIMIT_WAITS waits behind a 429 (what S08 did by hand).

--summaries (PK4; pipelines/evidence_summary.py): one summary per stored answer (outputs/examples), per tested question and
per entity card of the role examples, each written by the hosted model from the answer's numbered lines and checked sentence
by sentence. Writes outputs/summaries/<key>.json (commit them with the answers) and outputs/summaries/PROOFREAD.md (the
question, the bottom line, the summary, any removed sentence and the numbered lines, for the owner's one proofreading pass).
Run it on the same card index the hosted site serves (TMKG_PHAROS_INDEX), so the tested questions and cards cite the same
links: the key is made of the question, the cited link ids and the model. A summary already there is kept (--force writes
it again). Exit code 0 = every target has a summary, 1 = some have none (no hosted model, a refused reply, a failed check).

  summary_targets(app_module, stored_folder) -> [{id, kind ('stored' | 'tested' | 'card'), question, ...}]
  run_summaries(app_module, targets, out, force=False) -> (ids with a summary, ids without)
"""
import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))


BATCH_TIMEOUT, BATCH_ATTEMPTS, BATCH_RATE_LIMIT_WAITS, BATCH_BACKOFF = 120.0, 3, 12, 2.0


@contextlib.contextmanager
def batch_waits():
    """While inside, nim.web_budget (the waiting rules every web model client is made with) gives batch waits: no request
    deadline, BATCH_TIMEOUT s per attempt, BATCH_ATTEMPTS tries, BATCH_RATE_LIMIT_WAITS waits behind an HTTP 429. Restored on
    leaving, so nothing else in the process keeps them."""
    import nim
    from snapshot import HttpBudget
    before = nim.web_budget

    def budget(seconds=None, now=None):
        return HttpBudget(timeout=BATCH_TIMEOUT, attempts=BATCH_ATTEMPTS, rate_limit_waits=BATCH_RATE_LIMIT_WAITS,
                          backoff=BATCH_BACKOFF, deadline=None)
    nim.web_budget = budget
    try:
        yield budget
    finally:
        nim.web_budget = before


ATTACHED = ("summary_text", "summary_meta")      # the page attaches the model-written summary when it serves an answer


def program_ids(rows, programs):
    """Takes the rows of a trial-graph answer and {program id: facts} (answer_summary.program_facts). Returns new rows in
    which a cell holding a failed program's name ('Elafibranor — RESOLVE-IT') holds its internal id ('P22') again: the
    inverse of stored_answers.program_names, which /api/query now applies, so the record keeps the ids it always kept and the
    page renames them when it serves the answer."""
    by_name = {f.get("name"): pid for pid, f in (programs or {}).items() if f.get("name")}

    def cell(v):
        if isinstance(v, list):
            return [cell(x) for x in v]
        return by_name.get(v, v) if isinstance(v, str) else v
    return [{c: cell(v) for c, v in r.items()} if isinstance(r, dict) else r for r in rows]


def for_storage(answer, target, programs=None):
    """Takes an answer, its target and optionally the program facts (default answer_summary.program_facts()). Returns the
    answer as it is stored: without the attached summary (ATTACHED) and, for a trial-graph query, with the failed programs
    under their recorded ids (program_ids)."""
    a = {k: v for k, v in (answer or {}).items() if k not in ATTACHED}
    if target.get("kind") == "query" and isinstance(a.get("rows"), list):
        if programs is None:
            import answer_summary
            programs = answer_summary.program_facts()
        a["rows"] = program_ids(a["rows"], programs)
    return a


def plan(appmod):
    import persona_examples
    import personas
    import stored_answers
    kg = appmod.state()[0]
    roles = personas.load_personas()
    return stored_answers.targets(persona_examples.build(kg, roles), roles)


def run(client, target):
    """Posts the question to the target's endpoint. Returns (answer, backend, model)."""
    r = client.post(target["endpoint"], json={"question": target["question"]})
    r.raise_for_status()
    ans = r.json()
    if target["kind"] == "trials":
        run_ = ans.get("run") or {}
        models = sorted({m for _, m in run_.get("models") or []}) if run_.get("models") else []
        return ans, run_.get("backend"), ", ".join(models)
    return ans, ans.get("backend"), ans.get("model")


def hosted(answer, kind):
    """True when the answer came from the hosted model: backend 'nim', no stand-in fallback anywhere in it."""
    if kind == "trials":
        run_ = answer.get("run") or {}
        return run_.get("backend") == "nim" and not any(c.get("backend") == "offline-stand-in" for c in run_.get("model_calls") or [])
    return answer.get("backend") == "nim" and not answer.get("fallback_from")


def summary_targets(appmod, stored_folder=None):
    """Takes the app module and the stored-answer folder (default stored_answers.FOLDER). Returns what --summaries fills:
    every stored answer (kind 'stored', by its id), the five tested questions (kind 'tested', by demo id) and the entity
    cards of the role examples (kind 'card', by the example's entity name; the card is found by the page's own search)."""
    import persona_examples
    import pharos_app
    import stored_answers
    folder = Path(stored_folder or stored_answers.FOLDER)
    out = []
    for f in sorted(folder.glob("*.json")):
        if stored_answers.ID_RE.match(f.stem):
            out.append(dict(id=f.stem, kind="stored", folder=str(folder),
                            question=json.loads(f.read_text(encoding="utf-8")).get("question", "")))
    out += [dict(id=d["id"], kind="tested", question=d["question"]) for d in pharos_app.DEMO_QUESTIONS]
    seen = set()
    for x in persona_examples.SPEC:
        if x["kind"] == "card" and x.get("entity") and x["entity"] not in seen:
            seen.add(x["entity"])
            out.append(dict(id="card:" + x["entity"], kind="card", question=x["question"], entity=x["entity"]))
    return out


def _summary_input(appmod, client, t):
    """Takes the app module, a TestClient and one target. Returns (question, bottom line, lines) or None
    when the answer / card is not there. Lines are built exactly as the app's endpoints build them (routes_summary)."""
    import evidence_summary as ES
    import routes_summary as RS
    import stored_answers
    if t["kind"] == "stored":
        d = stored_answers.load(t.get("folder") or stored_answers.FOLDER, t["id"])
        if d is None or not isinstance(d.get("answer"), dict):
            return None
        kg = None
        if d.get("kind") == "query":
            try:
                kg = appmod.state()[0]
            except Exception:
                kg = None
        return d.get("question") or "", ES.bottom_line(d["answer"]), RS.stored_lines(d, kg)
    if t["kind"] == "tested":
        r = client.get(f"/api/pharos/demo/{t['id']}")
        if r.status_code != 200 or not r.json().get("rows"):
            return None
        one = r.json()
        return one.get("question") or "", ES.bottom_line(one), RS.graph_answer_lines(one)
    hits = client.get("/api/pharos/search", params={"q": t["entity"]})
    if hits.status_code != 200 or not hits.json():
        return None
    r = client.get(f"/api/pharos/card/{hits.json()[0]['id']}")
    if r.status_code != 200:
        return None
    card = r.json()
    return ES.card_question(card), ES.card_bottom_line(card), ES.card_lines(card)


def run_summaries(appmod, targets, out, force=False):
    """Takes the app module, the targets (summary_targets), the folder to write and whether to write summaries that are
    already there again. Writes one <key>.json per summary and PROOFREAD.md. Returns (ids with a summary, ids without)."""
    from fastapi.testclient import TestClient
    import evidence_summary as ES
    import nim
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    client, done, skipped, proof = TestClient(appmod.app), [], [], []
    base = appmod.state()[1]
    for t in targets:
        t0 = time.time()
        got = _summary_input(appmod, client, t)
        if got is None or not got[2]:
            skipped.append(t["id"])
            print(f"SKIP {t['id']}: no answer rows to summarise")
            continue
        question, bottom, lines = got
        writer = nim.web_client(ES.DEADLINE, base=base)
        if force:
            (out / f"{ES.cache_key(question, lines, ES.writer_model(writer))}.json").unlink(missing_ok=True)
        rec = ES.summary_for(question, bottom, lines, [out], write_folder=out, client=writer,
                             check_client=nim.web_client(ES.DEADLINE, base=base), call=True)
        if rec is None:
            skipped.append(t["id"])
            print(f"SKIP {t['id']}: no summary (no hosted model, a refused reply or a failed sentence check)")
            continue
        done.append(t["id"])
        removed = rec.get("dropped") or []
        proof += [f"## {t['id']} ({t['kind']})", "", f"Question: {question}", "", f"Bottom line: {bottom or '(none)'}", "",
                  "Summary (as the page shows it):", "", "> " + ES.response_keys(rec, lines)["summary_text"], ""]
        if removed:
            proof += ["Removed by the sentence check:", ""] + [f"- {d['sentence']} ({d.get('reason') or 'no reason given'})"
                                                                for d in removed] + [""]
        proof += [f"Written by {rec.get('model')}, checked by {rec.get('checked_by')}, {rec.get('created')}; file {rec['key']}.json",
                  "", "Numbered lines:", "", "```"] + [f"[{ln['n']}] {ln['text']}" for ln in lines] + ["```", ""]
        print(f"OK   {t['id']} {'stored' if rec.get('from_cache') else 'written'} {round(time.time() - t0, 1)} s")
    head = ["# Model-written summaries to proofread", "",
            f"{len(done)} of {len(targets)} targets have a summary. Each was written by a language model from the numbered "
            "lines below it and checked sentence by sentence against the lines it cites. Read each one against its lines; "
            "delete the .json file of any you reject and run the script again for that id.", ""]
    (out / "PROOFREAD.md").write_text("\n".join(head + proof), encoding="utf-8")
    print(f"{len(done)} of {len(targets)} summaries in {out}")
    return done, skipped


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--only", default="", help="comma list of ids to (re)compute")
    p.add_argument("--out", default=None, help="default outputs/examples (answers) or outputs/summaries (--summaries)")
    p.add_argument("--dry-run", action="store_true", help="list the targets and exit")
    p.add_argument("--summaries", action="store_true", help="write the model-written summaries instead of the answers (PK4)")
    p.add_argument("--force", action="store_true", help="with --summaries: write summaries that already exist again")
    a = p.parse_args(argv)
    from fastapi.testclient import TestClient
    import app as appmod
    import stored_answers
    if a.summaries:
        import evidence_summary as ES
        targets = summary_targets(appmod)
        if a.only:
            want = {x.strip() for x in a.only.split(",") if x.strip()}
            targets = [t for t in targets if t["id"] in want]
        if a.dry_run:
            for t in targets:
                print(f"{t['id']:40} {t['kind']:7} {t['question']}")
            return 0
        with batch_waits():
            done, skipped = run_summaries(appmod, targets, Path(a.out or ES.FOLDER), force=a.force)
        return 1 if skipped else 0
    a.out = a.out or str(ROOT / "outputs" / "examples")
    targets = plan(appmod)
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        targets = [t for t in targets if t["id"] in want]
    if a.dry_run:
        for t in targets:
            print(f"{t['id']:14} {t['kind']:7} {t['question']}")
        return 0
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client, skipped = TestClient(appmod.app), []
    with batch_waits():
        for t in targets:
            t0 = time.time()
            ans, backend, model = run(client, t)
            if not hosted(ans, t["kind"]):
                skipped.append(t["id"])
                print(f"SKIP {t['id']}: answered by {backend!r} (not the hosted model); nothing stored")
                continue
            rec = stored_answers.record(t, for_storage(ans, t), backend, model)
            (out / f"{t['id']}.json").write_text(json.dumps(rec, ensure_ascii=False))
            print(f"OK   {t['id']} {t['kind']} {round(time.time() - t0, 1)} s")
    print(f"{len(targets) - len(skipped)} of {len(targets)} stored in {out}")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
