"""
The model-written summary in the app's answers (packet PK4): the glue between pipelines/evidence_summary.py and the endpoints.
Its own router module, included by pipelines/app.py with one line.

Which answer gets what (the keys summary_text / summary_meta are absent whenever there is no summary):
- POST /api/pharos/ask, POST /api/ask, GET /api/ask_stream (a typed question or a trial report): the cached summary, else ONE
  summary is written inside the same request (no second request, so the visitor limit counts the question once). It waits at
  most SLOT_WAIT seconds for a free model slot; when none comes free the answer goes out without a summary.
- GET /api/pharos/demo/{id}, GET /api/stored_answer/{id}, GET /api/pharos/card/{id}, GET /api/pharos/explain/{id}: the cached
  summary only (scripts/precompute_examples.py --summaries fills the cache); these endpoints never call a model.
- POST /api/summary {"kind": "card" | "edge", "id"} (on demand, from an entity card or a 'why this link' drawer): the cached
  summary, else one written now and kept. Counted by the visitor limit like a question (app.ASK_PATHS). Returns
  {kind, id, summary_text, summary_meta} or {kind, id, available: false} when no summary could be written.

Functions (host = the app module: _data_root, model_client, model_slot, Busy, answering_graph, state, api_pharos_card,
api_pharos_explain, pjson)
- folders(host)                            -> the folders a summary is read from (evidence_summary.folders_for)
- graph_answer_lines(answer, graph=None)   -> lines of a graph answer (facts from its bottom line, else looked up in graph)
- stored_lines(stored, kg=None)            -> lines of a stored answer by its kind (trials, query, pharos)
- with_summary(out, question, bottom_line, lines, host, call, read_as=None) -> out with the two keys when there is a summary
- writer_bottom_line(out)                  -> the bottom line a typed graph answer's writer is given (FIX3 F3: with
                                              'Read as <term> (the question named <phrase>).' when the answer was read so)
- pharos_answer(out, host, call=True), demo_answer(one, host), stored_answer(d, host), report_answer(rep, host, call=True),
  card(card, host, call=False), edge(explain, host, call=False)  -> the response with the keys added
- make_router(host)                        -> the APIRouter with POST /api/summary
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import answer_summary as AS  # noqa: E402
import evidence_summary as ES  # noqa: E402

PATH = "/api/summary"
SLOT_WAIT = 2.0                      # seconds a typed question waits for a model slot for its summary (then: no summary)
KINDS = ("card", "edge")


def folders(host):
    """The folders a cached summary is read from: TMKG_SUMMARY_DIR, the app's data root, the committed outputs/summaries."""
    try:
        root = host._data_root()
    except Exception:
        root = None
    return ES.folders_for(root)


def graph_answer_lines(answer, graph=None):
    """Takes a graph answer and optionally the graph that answered. Returns its lines (evidence_summary.graph_lines). The
    facts of the cited links come from the answer's own bottom line (summary.refs.edges); when it holds none and a graph
    is given, they are looked up there (a trial-graph query answer)."""
    refs = ((answer.get("summary") or {}).get("refs") or {}) if isinstance(answer.get("summary"), dict) else {}
    facts = refs.get("edges") or None
    if facts is None and graph is not None:
        try:
            ids = [e for r in answer.get("rows") or [] for e in AS.row_edges(r, answer.get("columns") or [])]
            facts = AS.edge_facts(graph, ids)
        except Exception:
            facts = None
    return ES.graph_lines(answer, facts)


def stored_lines(stored, kg=None):
    """Takes a stored answer ({kind, question, answer}) and optionally the trial graph (for a query answer's link facts).
    Returns its lines: a trial report's claims, a graph answer's rows."""
    a = stored.get("answer") if isinstance(stored, dict) else None
    if not isinstance(a, dict):
        return []
    if stored.get("kind") == "trials":
        return ES.claim_lines(a)
    return graph_answer_lines(a, kg if stored.get("kind") == "query" else None)


def _clients(host):
    """The writer and checker clients of one request: the app's web client, each with its own DEADLINE."""
    return host.model_client(ES.DEADLINE), host.model_client(ES.DEADLINE)


def with_summary(out, question, bottom_line, lines, host, call, in_slot=False, read_as=None):
    """Takes a response dict, the question, the bottom line, the lines, the app module, whether a model may be called,
    whether the caller already holds a model slot and the terms the answer was read as (FIX3 F3; read by the sentence
    check, not part of the key). Returns the response with summary_text / summary_meta when a cached summary exists or
    (call=True) one was written now. A failure of this step never changes the answer itself."""
    try:
        if not lines or not ES.enabled():
            return out
        record = ES.summary_for(question, bottom_line, lines, folders(host), call=False)
        if record is None and call:
            def write():
                writer, checker = _clients(host)
                return ES.summary_for(question, bottom_line, lines, folders(host), client=writer, check_client=checker,
                                      call=True, read_as=read_as)
            if in_slot:
                record = write()
            else:
                try:
                    with host.model_slot(wait=SLOT_WAIT):
                        record = write()
                except host.Busy:
                    record = None
        return ES.attach(out, record, lines) if record is not None else out
    except Exception as err:                                  # the summary is extra: it must never break an answer
        print(f"summary step failed: {type(err).__name__}: {str(err)[:200]}", file=sys.stderr)
        return out


def writer_bottom_line(out):
    """FIX3 F3 (G3-08). Takes a typed graph answer. Returns the bottom line its summary writer is given: the headline, and
    when the answer read a phrase of the question as another term (the condition that makes the headline say "Read as",
    answer['read_as']) the sentence 'Read as lung carcinoma (the question named non-small cell lung carcinoma).' after it.
    The question itself, and so the cache key, is unchanged."""
    note = ES.read_as_note(out.get("read_as"))
    return " ".join(x for x in (ES.bottom_line(out), note) if x)


def pharos_answer(out, host, call=True):
    """A typed Pharos question (POST /api/pharos/ask): summary of its rows; none for an answer without rows. The writer is
    told the terms the answer was read as, and the sentence check drops a sentence that names the user's phrase instead."""
    if not out.get("rows"):
        return out
    return with_summary(out, out.get("question") or "", writer_bottom_line(out), graph_answer_lines(out), host, call,
                        read_as=out.get("read_as") or None)


def demo_answer(one, host):
    """A tested question (GET /api/pharos/demo/{id}): the cached summary only."""
    if not one.get("rows"):
        return one
    return with_summary(one, one.get("question") or "", ES.bottom_line(one), graph_answer_lines(one), host, call=False)


def stored_answer(d, host):
    """A stored answer (GET /api/stored_answer/{id}): the cached summary only. The keys go on the stored dict and on its
    answer (the page renders the answer)."""
    a = d.get("answer") if isinstance(d, dict) else None
    if not isinstance(a, dict):
        return d
    lines = stored_lines(d)                                   # ids only decide the key: no graph lookup needed here
    question = d.get("question") or a.get("question") or ""
    a2 = with_summary(a, question, ES.bottom_line(a), lines, host, call=False)
    if "summary_text" not in a2:
        return d
    return dict(d, answer=a2, summary_text=a2["summary_text"], summary_meta=a2["summary_meta"])


def report_answer(rep, host, call=True):
    """A trial report (POST /api/ask, GET /api/ask_stream): summary of its claims."""
    return with_summary(rep, rep.get("question") or "", ES.bottom_line(rep), ES.claim_lines(rep), host, call)


def card(c, host, call=False, in_slot=False):
    """An entity card: the cached summary (call=False, GET /api/pharos/card) or one written now (POST /api/summary)."""
    return with_summary(c, ES.card_question(c), ES.card_bottom_line(c), ES.card_lines(c), host, call, in_slot)


def edge(x, host, call=False, in_slot=False):
    """A link's 'why this link' drawer: the cached summary, or one written now (POST /api/summary)."""
    return with_summary(x, ES.edge_question(x), ES.edge_bottom_line(x), ES.edge_lines(x), host, call, in_slot)


def make_router(host):
    """Takes the app module. Returns an APIRouter with POST /api/summary (module docstring)."""
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field

    router = APIRouter()

    class SummaryAsk(BaseModel):
        kind: str = Field(max_length=10)
        id: str = Field(max_length=300)

    @router.post(PATH)
    def api_summary(r: SummaryAsk):
        """The summary of an entity card or of a link's drawer, written on demand and kept (same cache as the answers)."""
        if r.kind not in KINDS:
            raise HTTPException(400, f"unknown kind {r.kind!r}: use card or edge")
        if r.kind == "card":
            base, add = json.loads(host.api_pharos_card(r.id).body), card     # 404 for an unknown node, as the card itself
        else:
            base, add = json.loads(host.api_pharos_explain(r.id).body), edge  # 404 for an unknown link, as the drawer
        done = base if "summary_text" in base else add(base, host, call=False)
        if "summary_text" not in done and ES.enabled():
            with host.model_slot():                                         # busy: 503 with the app's plain sentence
                done = add(base, host, call=True, in_slot=True)
        if "summary_text" not in done:
            return dict(kind=r.kind, id=r.id, available=False)
        return host.pjson(dict(kind=r.kind, id=r.id, summary_text=done["summary_text"], summary_meta=done["summary_meta"]))

    return router
