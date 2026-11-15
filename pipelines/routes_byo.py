"""
Bring-your-own-table endpoints (router; pipelines/app.py includes it with one line).

  GET  /api/byo/examples        the stored worked examples (id, title, label, source) and the limits the page shows
  GET  /api/byo/example/{id}    one stored run: table rows, proposed mapping, validator messages, resulting edges
  POST /api/byo/draft           {table, hint?, source_name?} -> the drafted source card, the validation report, the list of
                                decisions and a draft_token. Counts toward the visitor's question limit (DRAFT_PATH is added
                                to app.ASK_PATHS by the include line), because it can call the language model.
  POST /api/byo/build           {table, card_yaml, approved: true, draft_token, source_name?} -> nodes and edges with their
                                provenance and verdict fields. Nothing is built unless `approved` is exactly true (the
                                scientist pressed "Approve mapping") and the token shows that this table was drafted here
                                within the last hour. One counted draft therefore allows builds of THAT table for an
                                hour (also with a hand-corrected card); a build without any draft is not possible.

Rules kept here (the work itself is in pipelines/card_drafter.py):
  - a pasted table is at most card_drafter.MAX_ROWS rows and MAX_BYTES bytes (413 beyond; the request body is read with
    a cap, so a larger upload is refused without being held in memory);
  - a card is YAML read with yaml.safe_load only, may name only columns of the pasted table and the allowed vocabulary, and
    its name, version, table and source fields are set by the server;
  - every step has a deadline (reading the body, each model call and all of them together, the trial build, the build);
  - the graph is built in a temporary directory by the existing builder and pruner; nothing is written under data/ or
    outputs/ and nothing is kept after the request (the token is a signature, not a stored record);
  - an error answer is a plain sentence; file names and internal messages never leave the server;
  - `note` in the decisions is the model's own free text (at most 240 characters): the page must show it as text.
"""
import asyncio, hashlib, hmac, json, secrets, sys, threading, time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_drafter as cd  # noqa: E402

DRAFT_PATH = "/api/byo/draft"
BUILD_PATH = "/api/byo/build"
BODY_CAP = 2 * cd.MAX_BYTES + 64 * 1024      # JSON escaping can double a table's size; the table itself is capped exactly
BODY_SECONDS = 15                            # to receive the request body
TOKEN_SECONDS = 3600                         # how long a draft stays valid for a build
MAX_BUILDS = 2                               # builds running at the same time
NO_STORE = {"Cache-Control": "no-store"}

router = APIRouter()
_secret = secrets.token_bytes(32)            # per process: a restart invalidates open drafts (the page drafts again)
_builds = threading.BoundedSemaphore(MAX_BUILDS)
_nim_lock, _nim_box = threading.Lock(), {}


def limits():
    """The caps as the page states them: {rows, kilobytes, columns, sample_rows, rounds, draft_valid_minutes}."""
    return dict(rows=cd.MAX_ROWS, kilobytes=cd.MAX_BYTES // 1024, columns=cd.MAX_COLUMNS, sample_rows=cd.SAMPLE_ROWS,
                rounds=cd.MAX_ROUNDS, draft_valid_minutes=TOKEN_SECONDS // 60)


def model_client():
    """The model client for drafts, made once per process: a nim.NIM without the batch fallbacks (card_drafter.call_model
    adds the deadline and the throw-away snapshot folder)."""
    with _nim_lock:
        if "nim" not in _nim_box:
            import nim as nimmod
            _nim_box["nim"] = nimmod.NIM(fallback="none")
        return _nim_box["nim"]


def table_digest(header, rows):
    """Takes a parsed table. Returns the sha256 of its canonical text (the same table always gives the same digest)."""
    return hashlib.sha256(cd.table_text(header, rows).encode("utf-8")).hexdigest()


def make_token(digest, now=None):
    """Takes a table digest (and the time in seconds). Returns the draft token '<time>.<signature>': proof that this
    process drafted a card for this table at that time. Nothing is stored."""
    ts = str(int(time.time() if now is None else now))
    return ts + "." + hmac.new(_secret, f"{ts}.{digest}".encode(), hashlib.sha256).hexdigest()


def token_valid(token, digest, now=None):
    """Takes a token, the digest of the table sent with the build and the time. Returns True when the token was made by
    make_token for the same table at most TOKEN_SECONDS ago."""
    if not isinstance(token, str) or token.count(".") != 1:
        return False
    ts, sig = token.split(".")
    if not ts.isdigit():
        return False
    good = hmac.new(_secret, f"{ts}.{digest}".encode(), hashlib.sha256).hexdigest()
    age = (time.time() if now is None else now) - int(ts)
    return hmac.compare_digest(sig, good) and 0 <= age <= TOKEN_SECONDS


async def read_json(request, cap=BODY_CAP, seconds=BODY_SECONDS):
    """Takes the request. Returns its JSON body as a dict. Raises HTTPException 413 when the body is larger than `cap`
    bytes (checked on Content-Length first, then while reading, so the rest is never read), 408 when it does not arrive
    within `seconds`, 400 when it is not a JSON object."""
    too_big = HTTPException(413, f"The request is too large. A pasted table may be at most {cd.MAX_BYTES // 1024} KB "
                                 f"and {cd.MAX_ROWS} rows.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise too_big

    async def read():
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > cap:
                raise too_big
        return bytes(buf)
    try:
        raw = await asyncio.wait_for(read(), seconds)
    except asyncio.TimeoutError:
        raise HTTPException(408, "The table did not arrive in time. Please try again.") from None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "The request must be a JSON object with the pasted table in `table`.")
    return body


def _table(body):
    try:
        return cd.parse_table(body.get("table"))
    except cd.TableError as err:
        raise HTTPException(err.status, str(err)) from None


def _text_field(body, key, max_len):
    """An optional short text field of the request: None when absent or empty; 400 when not text or too long."""
    v = body.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str) or len(v) > max_len or any(ord(c) < 32 for c in v):
        raise HTTPException(400, f"`{key}` must be one line of text of at most {max_len} characters.")
    return v.strip() or None


def provenance_for(source_name):
    """Takes the scientist's name for their table (or None). Returns the provenance the server writes on every edge: the
    name becomes the edges' `source` text; the knowledge source stays the placeholder of card_drafter.PASTED."""
    return dict(source=source_name) if source_name else None


def draft(table, hint=None, source_name=None, model=None):
    """Takes the pasted table (text), an optional hint and source name (and, in tests, a model function). Returns the
    draft answer: card_drafter.draft_card's result plus {draft_token, rows, sample_rows, limits}. Raises
    card_drafter.TableError for an unusable table."""
    header, rows = cd.parse_table(table)
    out = cd.draft_card(header, rows[:cd.SAMPLE_ROWS], hint, model=model, nim=None if model else model_client(),
                        provenance=provenance_for(source_name))
    out.update(draft_token=make_token(table_digest(header, rows)), rows=len(rows),
               sample_rows=min(len(rows), cd.SAMPLE_ROWS), limits=limits())
    return out


def build(table, card_yaml, source_name=None):
    """Takes the pasted table (text), the approved card (YAML text) and the optional source name. Returns
    card_drafter.build_small_graph's result plus card_yaml (the card as built, with the server's fields). The card is
    read with safe_load, completed and checked exactly as a draft is; ok False with the problems when it is refused."""
    header, rows = cd.parse_table(table)
    card, problems = cd.load_card_text(card_yaml)
    if problems:
        return dict(ok=False, problems=problems, nodes=[], edges=[], not_mapped=[], counts={}, verdict_note="", card_yaml="")
    card = cd.finish_card(card, header, rows, provenance_for(source_name))   # a SYNTHETIC label of the draft is kept
    out = cd.build_small_graph(card, header, rows)
    out["card_yaml"] = cd.dump_card(card) if out["ok"] else ""
    return out


@router.get("/api/byo/examples")
def api_byo_examples():
    """The stored worked examples and the limits for a pasted table."""
    return dict(examples=cd.stored_runs_list(), limits=limits())


@router.get("/api/byo/example/{example_id}")
def api_byo_example(example_id: str):
    """One stored run: the table rows, the proposed mapping with its decisions, the validator messages of every round and
    the resulting nodes and edges with provenance and verdicts. 404 for an id that is not a stored example."""
    run = cd.stored_run(example_id)
    if run is None:
        raise HTTPException(404, "No stored example with this name.")
    return run


@router.post(DRAFT_PATH)
async def api_byo_draft(request: Request):
    """Drafts the source card for a pasted table (see the module docstring). 413 over the caps, 400 for an unusable
    table; otherwise 200 with ok true or false: a draft that did not pass the checks is an answer, not an error."""
    body = await read_json(request)
    hint, source_name = _text_field(body, "hint", 300), _text_field(body, "source_name", 80)
    _table(body)                                    # refuse an unusable table before any model call
    try:
        out = await run_in_threadpool(draft, body["table"], hint, source_name)
    except cd.TableError as err:
        raise HTTPException(err.status, str(err)) from None
    except Exception:
        raise HTTPException(500, "The mapping could not be drafted. Please try again.") from None
    return JSONResponse(out, headers=NO_STORE)


@router.post(BUILD_PATH)
async def api_byo_build(request: Request):
    """Builds the small graph for an approved mapping (see the module docstring). 409 when the mapping was not approved or
    the table was not drafted here within the last hour, 422 when the card is refused (with the validator's messages),
    503 when MAX_BUILDS builds are already running."""
    body = await read_json(request)
    source_name = _text_field(body, "source_name", 80)
    header, rows = _table(body)
    if body.get("approved") is not True:
        raise HTTPException(409, "Nothing was built: the mapping has not been approved. Read the proposed mapping and "
                                 "press \"Approve mapping\" first.")
    if not token_valid(body.get("draft_token"), table_digest(header, rows)):
        raise HTTPException(409, "Nothing was built: this table has no current draft. Draft the mapping again (a draft "
                                 f"is valid for {TOKEN_SECONDS // 60} minutes and only for the table it was made for).")
    if not isinstance(body.get("card_yaml"), str):
        raise HTTPException(400, "The approved mapping must be sent as text in `card_yaml`.")
    if not _builds.acquire(blocking=False):
        raise HTTPException(503, "Other tables are being built right now. Please try again in a few seconds.",
                            headers={"Retry-After": "5"})
    try:
        out = await run_in_threadpool(build, body["table"], body["card_yaml"], source_name)
    except Exception:
        raise HTTPException(500, "The graph could not be built. Please try again.") from None
    finally:
        _builds.release()
    return JSONResponse(out, status_code=200 if out["ok"] else 422, headers=NO_STORE)
