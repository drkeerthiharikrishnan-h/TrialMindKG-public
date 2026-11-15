"""
TrialMindKG web app (FastAPI): ask a question, get an Intelligence Report whose every claim opens an evidence card.

    uvicorn pipelines.app:app --port 8000        # then open http://localhost:8000
    (or: python pipelines/app.py)

Pages and endpoints (all read-only; the graph is never changed from the app)
- GET  /                      the front page (app_page_v3.html, one HTML file, no build step) with per-role worked
                              examples (persona_examples.py)
- GET  /classic               the first page (app_page.html), kept for the regression journey only; /v3 redirects to /
- POST /api/summarize         {kind, result} -> the deterministic bottom line (answer_summary.py); /api/ask, /api/ask_stream,
                              /api/pharos/ask and /api/pharos/demo/{id} also carry it as an additive 'summary' key
- POST /api/ask               {"question"} -> report JSON (4 sections, typed claims, evidence, rejected claims, run log)
- GET  /api/report.pdf?q=...  the same report as a PDF
- GET  /api/evidence/{edge}   evidence card: source record, BioLink provenance, raw frozen response, graph path
- GET  /api/trial/{nct}       trial overview from kg_query (outcomes, drugs, safety, publications)
- POST /api/query             {"question"} -> Querier: validated openCypher + rows + evidence edge ids
- GET  /api/search?q=...       find trials, programs, drugs, genes, diseases by name / acronym / id
- GET  /api/neighbourhood/{id} the node's neighbours (1 hop, at most 150 edges) for the graph explorer
- GET  /api/status            graph version, model backend, quality metrics (schema-valid edges, provenance,
                              mapping coverage) and the open review queue
Pharos graph (pipelines/pharos_app.py; needs data/kg/pharos built by build_kg.py, loaded on first use)
- GET  /api/pharos/status      layers loaded, counts, pruning verdicts, predictions available
- GET  /api/pharos/search?q=   Pharos nodes by id / name / symbol
- GET  /api/pharos/node/{id}   the node and its edges by predicate with layer, source, verdict, confidence
- GET  /api/pharos/explain/{edge}  provenance, pruning verdict and reasons, corroborating rows, timeline
- GET  /api/pharos/predict?node=&n=&task=  top-n GNN predictions for a target or disease with supporting paths
- POST /api/pharos/ask         {"question"} -> Querier over the Pharos graph (validated openCypher + rows + evidence)
- GET  /api/pharos/demo        the five fixed Pharos questions run on the graph, with their checks
- GET  /api/pharos/demo_questions   the demo questions (id, question, why) for the strip, without running them
- GET  /api/pharos/demo/{id}   one demo question run on the graph, with its check
- POST /api/pharos/subgraph.zip {"edges": [...], "title"} -> the answer's subgraph: KGX tables + PyG-ready tables
                               (pipelines/subgraph_export.py)
- GET  /api/pharos/card/{id}   the entity card: about, known links by evidence kind, predicted links, a picture
  status, search and card come from the build-time card index (pipelines/pharos_index.py,
  data/kg/pharos_index/card_index.sqlite) when it exists, so opening the page and a card never loads the edge
  table; explain, predict, node, ask and demo load the full graph on first use
First screen and honesty labels (pipelines/usability.py, pipelines/neptune_backend.py)
- GET  /api/landing            three example chips for the one search box, the model label, the graph backends, via_cloudfront
                              and the "Runs on" line (PK15)
- GET  /api/backends           graph backends (local; neptune, available only when its state is 'available') and the default
- GET  /api/neptune/status, POST /api/neptune/wake   (routes_neptune.py, PK15) the state of Amazon Neptune as the host writes it,
                              and a wake request; neither counts against the ask limit
- GET  /api/downloads         the downloads listed in downloads.json under the deploy-time base URL (empty = hidden)
- GET  /api/agent-loop        the "For judges" tab: the brief's loop mapped to modules, commands and tests (docs/AGENT_LOOP.md)
- GET  /api/generalization     the Generalization tab: trial layer counts, the bring-your-own-source example built now, the
                              ADC example (SYNTHETIC)
  POST /api/pharos/ask takes {"question", "backend": "local" | "neptune"}; its answer carries label {kind, text, offline}: where
  the answer came from (hosted model, offline stand-in, stored answer, stand-in after a failed hosted call). The card
  endpoint reports elapsed_ms. backend 'neptune' (PK15) never fails: the local answer, plus the same query on Amazon Neptune
  when it is available (graph_backend says which graph answered; neptune {asked, answered, ms, match | state, note}).
Personas and words (pipelines/personas.py, personas/*.yaml; docs/USABILITY_PLAN.md)
- GET  /api/personas           the five user roles (start tab, example questions, card section order, wording level)
- GET  /api/wording            predicate -> verb phrase, knowledge level -> plain words, evidence kinds and colours
- GET  /api/stored_answers, /api/stored_answer/{id}  stored answers of the worked examples / role questions (stored_answers.py)
- GET  /api/trials_for?ids=&names=  failed-trial programs and trials in the trial graph that mention these ids / names
Model-written summary (PK4; pipelines/evidence_summary.py, routes_summary.py)
- keys summary_text / summary_meta on /api/pharos/ask, /api/ask, /api/ask_stream (written once inside the request when not
  stored), /api/pharos/demo/{id}, /api/stored_answer/{id}, /api/pharos/card/{id}, /api/pharos/explain/{id} (stored only);
  absent when there is no summary
- POST /api/summary            {"kind": "card" | "edge", "id"} -> the summary of an entity card or a link's drawer, on demand
Numbers page (the judge's view: the brief's four criteria in one place)
- GET  /api/results            the frozen link-prediction benchmark (outputs/linkpred/*@frozen_*.json and the paired
                              comparisons against G0; numbers_page.py), pruning calibration vs Open Targets
                              (outputs/pharos/prune_calibration.json), verdict counts, the schema card with BioLink
                              IRIs (when the Pharos graph is loaded), and the downloadable files that exist
- GET  /api/file?name=         one of the files /api/results lists or cites (allow-list; nothing else is served)

Backend: NVIDIA NIM when NVIDIA_API_KEY is set, otherwise the offline stand-ins (the report says which).
Functions: report_pdf(report) -> bytes; evidence_card(kg, edge_id) -> dict; status(kg) -> dict

Staying up on the open internet (review of 11 Nov, findings SEC-01 .. SEC-18; tests/test_stay_up.py):
- model_client() is the model client of ONE request (nim.web_client): 30 s per hosted call, one retry, no waiting behind a
  rate limit, 75 s for the whole request, then the labelled stand-in. model_slot() lets at most TMKG_MODEL_CONCURRENCY (4)
  requests use a model at a time; a request that finds no free slot within a few seconds gets 503 "busy", not a long wait.
- Limits: a question is at most MAX_QUESTION_CHARS (500) characters; a request body at most 256 KB (1 MB for the two
  endpoints that take an answer back); n is clamped to 1..50. ASK_PATHS (every endpoint that can spend model credits)
  are limited per visitor per hour and, with TMKG_ASK_LIMIT_PER_DAY, for all visitors together per day.
- GET /api/report.pdf and /api/report_subgraph.zip take the report the server already wrote for that question
  (cached_report) instead of running the whole pipeline again; only a question never asked here is run (and counted).
- Every answer carries security headers (SECURITY_HEADERS) and is compressed when the browser accepts it; the pages and the
  two status routes answer HEAD; /docs and /redoc are off (they load a script from a public CDN).
- A failed step of a report gives a JSON error body (POST /api/ask) or an `event: error` (the stream), never a bare 500.
"""
import csv, hashlib, json, os, re, secrets, sys, threading, time, traceback
from collections import OrderedDict, deque
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, FileResponse
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
import nim as nimmod, orchestrator, querier, evaluate, retrieval, personas, usability, neptune_backend
import answer_summary
from kg_query import KGIndex

ROOT = Path(__file__).resolve().parents[1]
STATUS_LABEL = {"prespecified": "Prespecified", "post_hoc": "Post hoc", "prospectively_confirmed":
                "Prospectively confirmed", "hypothesis_generating": "Hypothesis-generating",
                "registry_record": "Registry record", "curated_summary": "Curated summary"}

_state = nimmod._web                 # {"kg": the trial graph, "nim": the app's model client}; shared with nim.web_client()
_state_lock = threading.RLock()      # SEC-17: the lazy singletons (state, pharos, card_index) are built once, under this lock
_preflight = {"started": False, "rep": None}     # the start-up check of the hosted models (CC-36)


def start_preflight(nim=None, background=True):
    """Takes an optional NIM (default: the app's) and whether to run in a background thread. Starts nim.preflight once per
    process (one tiny call per configured model, nothing frozen) and keeps the report in _preflight["rep"]; /api/landing
    puts its summary into model.note. Off with TMKG_NIM_PREFLIGHT=off. Returns True when it started a check now."""
    import threading
    if _preflight["started"] or os.environ.get("TMKG_NIM_PREFLIGHT", "on") == "off":
        return False
    _preflight["started"] = True
    n = nim or state()[1]

    def run():
        try:
            _preflight["rep"] = nimmod.preflight(n)
        except Exception as err:                   # the check itself must never stop the app
            _preflight["rep"] = {"backend": n.backend, "ok": False, "listed": None,
                                 "rows": [dict(kind="check", model="(pre-flight)", roles=[], status="failed",
                                               listed=None, seconds=0, detail=f"{type(err).__name__}: {str(err)[:160]}")]}
    if background and n.backend != "offline":
        threading.Thread(target=run, daemon=True, name="nim-preflight").start()
    else:
        run()
    return True


def preflight_state():
    """The start-up check as nim.preflight_summary(...): None when no check was started (the tests, TMKG_NIM_PREFLIGHT=off)."""
    return nimmod.preflight_summary(_preflight["rep"]) if _preflight["started"] else None


def warm():
    """Loads the trial graph, the model client and the card index before the first visitor arrives (SEC-17), so that no two
    first requests build them at the same time. Off with TMKG_WARM=off. A failure here never stops the app: the first
    request then loads (and reports) as before. Returns the list of what was loaded. The live Pharos graph (pharos()) is
    NOT loaded here on purpose: it is the 26-million-link graph, which the hosted image does not carry (cards, search and
    the tested questions come from the card index, loaded above) and which would take minutes on the Mac; pharos() builds
    it once under the same lock on its first use (tests/test_stay_up.py, SEC-17)."""
    done = []
    if os.environ.get("TMKG_WARM", "on") == "off":
        return done
    for name, load in (("state", state), ("card_index", card_index)):
        try:
            load()
            done.append(name)
        except Exception as err:
            print(f"warm-up of {name} failed: {type(err).__name__}: {str(err)[:200]}", file=sys.stderr)
    return done


@asynccontextmanager
async def lifespan(_app):
    warm()                                          # SEC-17: build the singletons once, before any request
    start_preflight()                               # container start: check the hosted models in the background
    yield


# SEC-16: no /docs and /redoc (their page loads a script from a public CDN, which the self-hosted-fonts decision avoids and
# the Content-Security-Policy would block); PK1 (S03): /openapi.json is off with them (app.openapi() still builds the
# description for the tests and for anyone who needs it from the code).
app = FastAPI(title="TrialMindKG", description="Clinical-trial intelligence with provenance on every claim", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(__import__("routes_answers").router)      # PK2: /api/graph_numbers, /api/pharos/tested (routes_answers.py)
if (ROOT / "static").is_dir():                       # self-hosted fonts (CC-35: no call to Google Fonts)
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

# ------------------------------------------------------------------ ask limit (owner decision 8 Nov)
# The hosted app is open to the internet and every question can spend NVIDIA credits, so the three ask endpoints accept at
# most TMKG_ASK_LIMIT_PER_HOUR questions per visitor per hour (0 or unset = no limit: the Mac and the tests). Cards, search,
# the tested demo questions and every other endpoint are never limited. The visitor (CC-46, 9 Nov) is the per-browser cookie
# VISITOR_COOKIE (a random id set on the first page load, 1 year, SameSite=Lax), so a household or office behind one public
# address no longer shares one budget; without the cookie (a script, cookies off) it is the first X-Forwarded-For address
# (Caddy sets it; only Caddy can reach the app on the instance) or the socket address. A browser that drops its cookie gets
# a new budget: the limit protects the credits from casual over-use, not from a determined script.
# SEC-04 (11 Nov): /api/query and the two report exports spend credits too, so they are limited as well (an export of a
# report the server already holds is neither run again nor counted: EXPORT_PATHS, cached_report). TMKG_ASK_LIMIT_PER_DAY
# (0 or unset = none) caps the questions of ALL visitors together per 24 hours, because a new cookie is a new visitor.
ASK_PATHS = {"/api/ask", "/api/ask_stream", "/api/pharos/ask", "/api/query", "/api/report.pdf", "/api/report_subgraph.zip"}
EXPORT_PATHS = {"/api/report.pdf", "/api/report_subgraph.zip"}
PAGE_PATHS = {"/", "/classic"}
VISITOR_COOKIE = "tmkg_vid"
_VID = re.compile(r"^[0-9a-f]{32}$")
_ask_log = {}
_day_log = deque()
_ask_swept = {"at": 0.0}


def forget_old_visitors(now, window=3600.0, log=None, every=60.0, swept=None):
    """SEC-17: the limiter kept one entry per visitor for ever. Takes the time, the window, the per-visitor log and how often
    to sweep (seconds). At most once per `every` seconds it drops every visitor whose questions have all left the window.
    Returns how many visitors were dropped."""
    log = _ask_log if log is None else log
    swept = _ask_swept if swept is None else swept
    if now - swept["at"] < every:
        return 0
    swept["at"] = now
    old = [v for v, times in list(log.items()) if not times or now - times[-1] >= window]
    for v in old:
        log.pop(v, None)
    return len(old)


def day_allowed(now, limit, window=86400.0, log=None, record=True):
    """The cap for all visitors together. Takes the time, the limit per window (<= 0 = none), the log (a deque of times)
    and whether to count this question when allowed. Returns (allowed, seconds until the oldest counted question leaves
    the window)."""
    if limit <= 0:
        return True, 0
    log = _day_log if log is None else log
    while log and now - log[0] >= window:
        log.popleft()
    if len(log) >= limit:
        return False, int(window - (now - log[0])) + 1
    if record:
        log.append(now)
    return True, 0


def ask_allowed(visitor, now, limit, window=3600.0, log=None):
    """Takes a visitor key, the time in seconds, the limit per window (<= 0 = unlimited) and the per-visitor log (dict of
    lists of times). Returns (allowed, seconds until the oldest counted question leaves the window); records the question
    when allowed."""
    if limit <= 0:
        return True, 0
    log = _ask_log if log is None else log
    times = [t for t in log.get(visitor, []) if now - t < window]
    if len(times) >= limit:
        log[visitor] = times
        return False, int(window - (now - times[0])) + 1
    times.append(now)
    log[visitor] = times
    return True, 0


def ask_remaining(visitor, now, limit, window=3600.0, log=None):
    """Takes the same arguments as ask_allowed (without recording anything). Returns how many questions the visitor has left
    in the current window (None when there is no limit)."""
    if limit <= 0:
        return None
    log = _ask_log if log is None else log
    return max(0, limit - len([t for t in log.get(visitor, []) if now - t < window]))


def visitor_key(cookies, forwarded_for, client_host):
    """Takes the request's cookies (dict), its X-Forwarded-For header and the socket address. Returns the key the ask limit
    counts under: 'c:<cookie id>' when the visitor cookie is a well-formed id, else 'ip:<first forwarded address or socket>'."""
    vid = (cookies or {}).get(VISITOR_COOKIE, "")
    if _VID.match(vid):
        return "c:" + vid
    return "ip:" + client_ip(forwarded_for, client_host)


def client_ip(forwarded_for, client_host):
    """Takes the X-Forwarded-For header and the socket address. Returns the client address the per-address ceiling counts
    under: the first X-Forwarded-For hop (on the hosted site Caddy sets it to the real client and ignores one a client sends),
    else the socket address, else 'unknown'."""
    return (forwarded_for or "").split(",")[0].strip() or client_host or "unknown"


IP_CEILING_FACTOR = 3                # gate 1 G1-SEC-01: per address, 3x the per-visitor hourly limit


def ip_limit(visitor_limit):
    """Takes the per-visitor hourly limit (<= 0 = none). Returns the hourly ceiling for ALL cookies from one address:
    TMKG_ASK_LIMIT_PER_IP_HOUR when set, else IP_CEILING_FACTOR x the visitor limit (0 = none when the visitor limit is off).
    A script that rotates the visitor cookie gets a fresh visitor budget each time; this ceiling stops it from using up the day
    cap that all visitors share."""
    own = _env_int("TMKG_ASK_LIMIT_PER_IP_HOUR", -1)
    if own >= 0:
        return own
    return IP_CEILING_FACTOR * visitor_limit if visitor_limit > 0 else 0


def ip_key(forwarded_for, client_host):
    """The key of the per-address ceiling in the hour log ('net:<address>'; never equal to a visitor key)."""
    return "net:" + client_ip(forwarded_for, client_host)


def ip_ceiling_allowed(net, now, net_limit, record=True, log=None):
    """Takes the per-address key (ip_key), the time, the ceiling (ip_limit; <= 0 = none), whether to count this question when
    allowed, and the hour log. Returns (allowed, seconds until the oldest counted question from that address leaves the hour).
    Used by the ask limiter and by the on-demand edge check (routes_audit), so both count against the same ceiling."""
    if net_limit <= 0:
        return True, 0
    log = _ask_log if log is None else log
    times = [t for t in log.get(net, []) if now - t < 3600.0]
    if len(times) >= net_limit:
        log[net] = times
        return False, int(3600.0 - (now - times[0])) + 1
    if record:
        times.append(now)
    log[net] = times
    return True, 0


def ip_ceiling_refusal(net_limit, wait):
    """Takes the ceiling and the wait in seconds. Returns the 429 body (detail, limit, retry_after, scope 'address')."""
    mins = max(1, wait // 60)
    return {"detail": f"This demo answers at most {net_limit} questions per hour from one network address, to keep it "
                      f"available for everyone. Please try again in about {mins} minute{'' if mins == 1 else 's'}. Cards, "
                      "search and the tested questions still work.",
            "limit": net_limit, "retry_after": wait, "scope": "address"}


NOT_A_QUESTION = {400, 404, 405, 409, 413, 422, 503}   # refused for its form (too long, too large) or because the demo was busy
OWN_BODY_LIMIT_PATHS = set()         # endpoints that cap their own body while reading it (PK8: routes_byo); BodyLimit skips them
COUNTED_REFUSALS = {}                # path -> statuses that still count as a question there (PK8: a refused paste, 400)


def uncount(visitor, now, log=None, day_log=None, ip=None):
    """Takes a visitor key, the time a question was counted at and (optional) the per-address key. Removes that one count
    from the visitor's hour, the address's hour and from the day (a request that was refused for its form called no model,
    so it must not use up anyone's questions)."""
    log = _ask_log if log is None else log
    day_log = _day_log if day_log is None else day_log
    for key in (visitor, ip):
        times = log.get(key) or [] if key else []
        if times and times[-1] == now:
            times.pop()
    if day_log and day_log[-1] == now:
        day_log.pop()


def _env_int(name, default=0):
    """Takes an environment variable name and a default. Returns its value as an int (the default when unset or not a number)."""
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@app.middleware("http")
async def _ask_limit(request, call_next):
    path = request.url.path
    if path in ASK_PATHS and not (path in EXPORT_PATHS and cached_report(request.query_params.get("q", "")) is not None):
        limit, per_day = _env_int("TMKG_ASK_LIMIT_PER_HOUR"), _env_int("TMKG_ASK_LIMIT_PER_DAY")
        visitor = visitor_key(request.cookies, request.headers.get("x-forwarded-for", ""), request.client.host if request.client else "")
        now = time.time()
        forget_old_visitors(now)
        open_today, day_wait = day_allowed(now, per_day, record=False)
        if not open_today:
            return JSONResponse(status_code=429, headers={"Retry-After": str(day_wait), "X-Ask-Remaining": "0"},
                                content={"detail": f"This demo answers at most {per_day} questions a day for all visitors "
                                                   "together, and today's are used up. Please try again in about "
                                                   f"{max(1, day_wait // 3600)} hour{'s' if day_wait >= 7200 else ''}. Cards, search and the tested "
                                                   "questions still work.",
                                         "remaining": 0, "limit": per_day, "retry_after": day_wait, "scope": "day"})
        net, net_limit = ip_key(request.headers.get("x-forwarded-for", ""), request.client.host if request.client else ""), ip_limit(limit)
        net_ok, net_wait = ip_ceiling_allowed(net, now, net_limit, record=False)       # G1-SEC-01: before the cookie
        if not net_ok:
            return JSONResponse(status_code=429, headers={"Retry-After": str(net_wait), "X-Ask-Remaining": "0"},
                                content=dict(ip_ceiling_refusal(net_limit, net_wait), remaining=0))
        ok, wait = ask_allowed(visitor, now, limit)
        if not ok:
            return JSONResponse(status_code=429, headers={"Retry-After": str(wait), "X-Ask-Remaining": "0"},
                                content={"detail": f"This demo answers at most {limit} questions per visitor per hour, to keep "
                                                   f"it available for everyone. Please try again in about {max(1, wait // 60)} "
                                                   "minutes. Cards, search and the tested questions still work.",
                                         "remaining": 0, "limit": limit, "retry_after": wait})
        day_allowed(now, per_day)                        # count it for the day
        ip_ceiling_allowed(net, now, net_limit)          # and for the address
        response = await call_next(request)
        if response.status_code in NOT_A_QUESTION - COUNTED_REFUSALS.get(path, set()):   # refused before any model call: give it back
            uncount(visitor, now, ip=net if net_limit > 0 else None)
        left = ask_remaining(visitor, now, limit)
        net_left = ask_remaining(net, now, net_limit) if net_limit > 0 else None
        if left is not None and net_left is not None:
            left = min(left, net_left)
        if left is not None:
            response.headers["X-Ask-Remaining"] = str(left)
        return response
    response = await call_next(request)
    if path in PAGE_PATHS and not _VID.match(request.cookies.get(VISITOR_COOKIE, "")):
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
        response.set_cookie(VISITOR_COOKIE, secrets.token_hex(16), max_age=365 * 24 * 3600, samesite="lax", httponly=True, secure=https)
    return response


@app.get("/api/ask_quota")
def api_ask_quota(request: Request):
    """How many questions this visitor has left this hour, without counting one (CC-46: the page shows "N questions left"
    when N <= 5, and explains a dropped event stream). Returns {limit, remaining, retry_after, detail}; limit 0 and
    remaining None when there is no limit."""
    limit = int(os.environ.get("TMKG_ASK_LIMIT_PER_HOUR", "0") or 0)
    visitor = visitor_key(request.cookies, request.headers.get("x-forwarded-for", ""), request.client.host if request.client else "")
    now = time.time()
    left = ask_remaining(visitor, now, limit)
    times = [t for t in _ask_log.get(visitor, []) if now - t < 3600.0]
    wait = int(3600.0 - (now - times[0])) + 1 if left == 0 and times else 0
    net, net_limit = ip_key(request.headers.get("x-forwarded-for", ""), request.client.host if request.client else ""), ip_limit(limit)
    net_ok, net_wait = ip_ceiling_allowed(net, now, net_limit, record=False)          # G1-SEC-01: the address ceiling too
    if not net_ok:
        return dict(limit=net_limit, remaining=0, retry_after=net_wait, detail=ip_ceiling_refusal(net_limit, net_wait)["detail"])
    detail = (f"This demo answers at most {limit} questions per visitor per hour, to keep it available for everyone. "
              "Cards, search and the tested questions still work.") if left == 0 else ""
    return dict(limit=max(limit, 0), remaining=left, retry_after=wait, detail=detail)


import routes_byo; app.include_router(routes_byo.router); ASK_PATHS.add(routes_byo.DRAFT_PATH)   # noqa: E401,E702  own table: a draft counts as a question
OWN_BODY_LIMIT_PATHS.update({routes_byo.DRAFT_PATH, routes_byo.BUILD_PATH})   # routes_byo reads the body with its own cap (BODY_CAP)
COUNTED_REFUSALS[routes_byo.DRAFT_PATH] = {400}                                # PK8: a refused paste counts toward the limit too


def state():
    """Graph and model client, loaded once per process (under a lock: SEC-17, eight first requests used to build the
    graph eight times). Returns (kg, nim); nim is the app's base client: endpoints that call a model use model_client()."""
    if "kg" not in _state or "nim" not in _state:
        with _state_lock:
            if "kg" not in _state:
                _state["kg"] = KGIndex()
            if "nim" not in _state:
                nimmod.web_base()                        # failing hosted call -> labelled stand-in; stored in _state["nim"]
    return _state["kg"], _state["nim"]


# ------------------------------------------------------------------ staying up: model client, slots, limits, headers
MAX_QUESTION_CHARS = 500             # SEC-01 / SEC-06: a typed question (every 1-3 word phrase of it is looked up)
MAX_BODY_BYTES = 256 * 1024          # SEC-06: any request body ...
MAX_ANSWER_BODY_BYTES = 1024 * 1024  # ... except the two endpoints that take an answer the page holds back
ANSWER_BODY_PATHS = {"/api/summarize", "/api/pharos/subgraph.zip"}
MAX_N = 50                           # SEC-06: predictions / card rows asked for in one call
BIG_FILE_BYTES = 50 * 1024 * 1024    # SEC-16: larger files are downloads, not something the one app worker should stream
BIG_FILE_FOLDERS = {"edge_verdicts.tsv.gz": "verdicts/"}   # /api/file name -> folder in downloads.json

BUSY_TEXT = ("The demo is answering as many questions as it can at the moment. Please try again in a minute. "
             "Cards, search and the tested questions still work.")
REPORT_FAILED_TEXT = ("The report could not be written this time: one of its steps failed. Please ask again. "
                      "Cards, search and the tested questions still work.")
SERVER_ERROR_TEXT = "Something went wrong on the server while answering. Please try again."


class Busy(RuntimeError):
    """No model slot came free in time (model_slot); answered as HTTP 503 with BUSY_TEXT."""


_model_health = usability.ModelHealth()     # FIX1 F4 (G1-U-01): the last answers that needed the hosted model


def note_model_outcome(label, now=None):
    """Takes the label of a fresh answer (usability.answer_label). Records whether the hosted model answered it
    (usability.label_outcome; stored, offline and too-broad answers record nothing). Returns the recorded outcome or None."""
    ok = usability.label_outcome(label)
    if ok is not None:
        _model_health.record(ok, time.time() if now is None else now)
    return ok


def model_paused(now=None):
    """True when the last answers that needed the hosted model all failed within the last 10 minutes (ModelHealth):
    /api/status and /api/landing then say typed questions are paused. No model call is made to find out."""
    return _model_health.paused(time.time() if now is None else now)


def model_client(seconds=None):
    """The model client for ONE request: nim.web_client on the app's client (own call log, 30 s per hosted call, one
    retry, no rate-limit wait, a deadline of `seconds` (default 75) for the whole request, then the labelled stand-in).
    Router modules import nim.web_client directly; it returns the same thing."""
    return nimmod.web_client(seconds, base=state()[1])


_slots = {"n": None, "sem": None}


@contextmanager
def model_slot(wait=None):
    """SEC-04: at most TMKG_MODEL_CONCURRENCY (default 4; 0 = no cap) requests use a model at the same time, so that stuck
    or slow model calls cannot take every worker thread and with them the cards, search and the health check. Takes the
    seconds to wait for a free slot (default TMKG_MODEL_WAIT_SECONDS, 5). Use as `with model_slot(): ...`; raises Busy."""
    n = _env_int("TMKG_MODEL_CONCURRENCY", 4)
    if n <= 0:
        yield
        return
    with _state_lock:
        if _slots["n"] != n:                             # first use, or the setting changed (tests)
            _slots["n"], _slots["sem"] = n, threading.BoundedSemaphore(n)
        sem = _slots["sem"]
    wait = float(_env_int("TMKG_MODEL_WAIT_SECONDS", 5)) if wait is None else wait
    if not sem.acquire(timeout=max(0.0, wait)):
        raise Busy(BUSY_TEXT)
    try:
        yield
    finally:
        sem.release()


def error_json(status_code, text, kind, retry_after=None):
    """A JSON error body the pages can show: {"detail": plain sentence, "error": kind}. Takes the HTTP status, the sentence,
    a short machine-readable kind and optionally the Retry-After seconds. Returns a JSONResponse."""
    headers = dict(SECURITY_HEADERS, **({"Retry-After": str(retry_after)} if retry_after else {}))   # also outside the header middleware
    return JSONResponse(status_code=status_code, content={"detail": text, "error": kind}, headers=headers)


@app.exception_handler(Busy)
async def _busy(_request, err):
    return error_json(503, str(err) or BUSY_TEXT, "busy", retry_after=30)


@app.exception_handler(querier.QueryBusy)
async def _query_busy(_request, err):
    return error_json(503, str(err), "busy", retry_after=15)


@app.exception_handler(RequestValidationError)
async def _invalid(_request, err):
    """A request that does not fit (a question that is too long, a missing field): HTTP 422 with ONE plain sentence in
    `detail` (the pages print detail as text) and the per-field list in `errors`."""
    errors = [dict(where=".".join(str(x) for x in e.get("loc", ()) if x not in ("body", "query")), problem=str(e.get("msg", "")))
              for e in err.errors()]
    too_long = any(e.get("type") == "string_too_long" and (e.get("loc") or [""])[-1] in ("question", "q") for e in err.errors())
    text = (f"Please keep the question under {MAX_QUESTION_CHARS} characters." if too_long else
            "The request could not be read: " + "; ".join(f"{e['where'] or 'request'}: {e['problem']}" for e in errors[:3]) + ".")
    return JSONResponse(status_code=422, content={"detail": text, "error": "invalid_request", "errors": errors})


@app.exception_handler(Exception)
async def _server_error(_request, err):
    """Anything unexpected: the traceback goes to the server log, the visitor gets a JSON body with a plain sentence."""
    traceback.print_exception(type(err), err, err.__traceback__, file=sys.stderr)
    return error_json(500, SERVER_ERROR_TEXT, "server_error")


# SEC-05: the single-file pages carry their script and style inline and load nothing from another site, so everything is
# limited to this origin; no other site may frame the app. Caddy adds the same headers to what it serves itself.
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                           "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
                           "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {"Content-Security-Policy": CONTENT_SECURITY_POLICY, "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY", "Referrer-Policy": "strict-origin-when-cross-origin",
                    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()"}
HSTS = "max-age=31536000"            # sent only when the visitor came over HTTPS


@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https":
        response.headers.setdefault("Strict-Transport-Security", HSTS)
    return response


class BodyLimit:
    """SEC-06: refuses a request body above the limit of its path (MAX_BODY_BYTES; MAX_ANSWER_BODY_BYTES for
    ANSWER_BODY_PATHS) with HTTP 413 and a plain sentence, before it is read into memory. A body sent without a length
    is cut off at the limit (the endpoint then answers that it cannot read it). Pure ASGI middleware."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS") or scope["path"] in OWN_BODY_LIMIT_PATHS:
            return await self.app(scope, receive, send)
        limit = MAX_ANSWER_BODY_BYTES if scope["path"] in ANSWER_BODY_PATHS else MAX_BODY_BYTES
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            response = error_json(413, f"This request is too large (at most {limit // 1024} KB).", "too_large")
            return await response(scope, receive, send)
        seen = {"n": 0, "cut": False}

        async def limited():
            if seen["cut"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                seen["n"] += len(message.get("body", b""))
                if seen["n"] > limit:
                    seen["cut"] = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message
        return await self.app(scope, limited, send)


app.add_middleware(BodyLimit)
app.add_middleware(GZipMiddleware, minimum_size=1024)    # SEC-05: compressed answers (the event stream is left as it is)


# ------------------------------------------------------------------ the reports the server already wrote (SEC-04)
MAX_REPORTS = 32
_reports = OrderedDict()
_reports_lock = threading.Lock()


def report_key(question):
    """Takes a question. Returns the key its report is kept under (a hash of the question without outer spaces)."""
    return hashlib.sha256((question or "").strip().encode("utf-8")).hexdigest()


def cached_report(question):
    """Takes a question. Returns the report the server wrote for it earlier in this process (the last MAX_REPORTS
    questions are kept), or None. The PDF and subgraph exports use it instead of running the pipeline again."""
    with _reports_lock:
        r = _reports.get(report_key(question))
        if r is not None:
            _reports.move_to_end(report_key(question))
        return r


def remember_report(question, report):
    """Keeps a finished report (with its label) for the exports; the oldest is dropped beyond MAX_REPORTS. Returns it."""
    note_model_outcome(report.get("label"))          # FIX1 F4: the outcome of a fresh report counts for /api/status
    with _reports_lock:
        _reports[report_key(question)] = report
        _reports.move_to_end(report_key(question))
        while len(_reports) > MAX_REPORTS:
            _reports.popitem(last=False)
    return report


def run_report(question):
    """Runs the trial-report pipeline for one question with the request's model client, inside a model slot. Returns the
    report with its 'label' (usability.answer_label) and keeps it for the exports. Raises Busy when no slot is free."""
    with model_slot():
        kg = state()[0]
        rep = orchestrator.ask(question, kg=kg, nim=model_client())
    return remember_report(question, dict(rep, label=usability.answer_label(rep.get("run", {}))))


def report_for(question):
    """The report for an export: the one already written for this question, else a fresh run."""
    return cached_report(question) or run_report(question)


class Question(BaseModel):
    question: str = Field(max_length=MAX_QUESTION_CHARS)
    backend: str = "local"               # 'local' | 'neptune' (GET /api/backends says which are available)


# ------------------------------------------------------------------ evidence + status
def graph_path(kg, edge_id, seeds, max_hops=6):
    """Shortest chain of edges (any direction) from a question seed to the cited edge: how the fact connects to the
    trial / program asked about. -> [dict(id, statement)] (empty if the edge touches a seed or no path within hops)."""
    e = kg.edges[edge_id]
    ends, start = {e["subject"], e["object"]}, [s for s in seeds if s in kg.nodes]
    if not start or ends & set(start):
        return []
    prev, frontier = {s: None for s in start}, list(start)
    for _ in range(max_hops):
        nxt = []
        for n in frontier:
            for x in sorted(kg.by_subject.get(n, []) + kg.by_object.get(n, []), key=lambda x: x["id"]):
                m = x["object"] if x["subject"] == n else x["subject"]
                if m not in prev:
                    prev[m] = (n, x["id"])
                    nxt.append(m)
                    if m in ends:
                        path, cur = [], m
                        while prev[cur]:
                            cur, eid = prev[cur][0], prev[cur][1]
                            path.append(eid)
                        return [dict(id=i, statement=_statement(kg, kg.edges[i])) for i in reversed(path)] + \
                            [dict(id=edge_id, statement=_statement(kg, e))]
        frontier = nxt
    return []


def _statement(kg, e):
    s, o = kg.nodes.get(e["subject"], {}), kg.nodes.get(e["object"], {})
    return f"{retrieval.label(s, kg) if s else e['subject']} -{e['predicate']}-> {retrieval.label(o, kg) if o else e['object']}"


def registry_url(edge, raw=None):
    """FIX1 F11 (gate 1, G1-ES-06). Takes a trial-layer edge and the provenance row's raw_response (a path of the frozen file
    in this repository). Returns the public ClinicalTrials.gov page of the first NCT id in the edge's source record, subject,
    object or that path, or None: a visitor never gets a repository path (the frozen file stays in data/snapshots)."""
    import re
    text = " ".join(str(x or "") for x in (edge.get("source_record"), edge.get("subject"), edge.get("object"), raw))
    m = re.search(r"NCT\d{8}", text)
    return f"https://clinicaltrials.gov/study/{m.group(0)}" if m else None


def evidence_card(kg, edge_id, seeds=()):
    """Everything a reviewer needs to judge one edge: the statement, where it came from, how it was made, and the
    graph path from the question's seeds to it."""
    if edge_id not in kg.edges:
        return None
    e = kg.edges[edge_id]
    row = kg.provenance(edge_id)["rows"][0]
    s, o = kg.nodes.get(e["subject"], {}), kg.nodes.get(e["object"], {})
    return dict(
        id=edge_id, statement=f"{s.get('name') or e['subject']} -{e['predicate']}-> {o.get('name') or e['object']}",
        subject=dict(id=e["subject"], name=s.get("name"), category=s.get("category")),
        predicate=e["predicate"],
        object=dict(id=e["object"], name=o.get("name"), category=o.get("category")),
        source=e.get("source"), source_record=e.get("source_record"), evidence_type=e.get("evidence_type"),
        retrieved=e.get("retrieved"), via=e.get("via"),
        biolink=dict(primary_knowledge_source=e.get("primary_knowledge_source"),
                     aggregator_knowledge_source=json.loads(e.get("aggregator_knowledge_source") or "[]"),
                     supporting_data_source=json.loads(e.get("supporting_data_source") or "[]"),
                     knowledge_level=e.get("knowledge_level"), agent_type=e.get("agent_type")),
        raw_response=registry_url(e, row.get("raw_response")), mapping_confidence=e.get("mapping_confidence"),
        biolink_predicate=e.get("biolink_predicate"), graph_path=graph_path(kg, edge_id, seeds),
        details={k: v for k, v in e.items() if k not in ("id", "subject", "predicate", "object", "source",
                                                       "source_record", "evidence_type", "retrieved", "via")
                 and not k.endswith("knowledge_source") and k not in ("supporting_data_source", "knowledge_level",
                                                                     "agent_type")})


def status(kg):
    q = evaluate.graph_quality(kg)
    queue = [r for r in csv.DictReader(open(ROOT / "outputs" / "resolution_report.csv", encoding="utf-8"))
             if r["rule"].endswith("NEEDS_REVIEW")]
    man = json.loads((ROOT / "outputs" / "run_manifest.json").read_text())
    from kg_schema import KG, validate
    g = KG()
    g.nodes, g.edges = kg.nodes, kg.edges
    return dict(graph_version=kg.version, counts=man.get("counts"), sources=man.get("sources"),
                validation_failures=validate(g)[:50],
                contradictions=list(csv.DictReader(open(ROOT / "outputs" / "contradiction_report.csv", encoding="utf-8")))
                if (ROOT / "outputs" / "contradiction_report.csv").exists() else [],
                coverage_by_type=q["entity_mapping_coverage"].get("by_type") if isinstance(q.get("entity_mapping_coverage"), dict) else None,
                backend=state()[1].backend, models=state()[1].cfg.get("roles"),
                model=usability.model_label(state()[1], preflight_state(), paused=model_paused()),   # FIX1 F4 (G1-U-01)
                quality={k: dict(value=v["value"], n=v["n"], definition=v["definition"]) for k, v in q.items()},
                review_queue=[{k: r[k] for k in ("program", "entity_type", "query", "rule")} for r in queue])


# ------------------------------------------------------------------ PDF export
def _latin(text):
    """Core PDF fonts cover Latin-1 only; the JSON export keeps the exact text."""
    for a, b in (("≥", ">="), ("≤", "<="), ("—", "-"), ("–", "-"), ("´", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
                 ("•", "-"), ("→", "->")):
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def report_pdf(r):
    """Takes a report (orchestrator.ask, optionally with its 'label'). Returns the PDF as bytes: question, the run note,
    the answer label when it has a text (stand-in, stored answer, backup model), the sections with their claims and
    evidence, and the claims the verifier removed."""
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    w = pdf.w - 2 * pdf.l_margin
    def para(text, size=10, style="", gap=1.5):
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(w, size * 0.5, _latin(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)
    para("TrialMindKG Intelligence Report", 16, "B", 3)
    para(f"Question: {r['question']}", 11, "B")
    label = r.get("label") or usability.answer_label(r.get("run", {}))     # SEC-11: the on-screen label, in the export too
    para(f"{r['run']['note']} Graph version {str(r['run']['graph_version'].get('snapshot'))[:12]}.", 8, "I", 1.5 if label.get("text") else 4)
    if label.get("text"):
        para(label["text"], 9, "B", 4)
    for s in r["sections"]:
        para(s["title"], 13, "B")
        if not s["claims"]:
            para(s["note"], 9, "I")
        for c in s["claims"]:
            para(f"- {c['claim']}", 10)
            para(f"   {STATUS_LABEL.get(c['evidence_status'], c['evidence_status'])}, uncertainty {c['uncertainty']}; "
                 "evidence: " + "; ".join(f"{e['id']} ({e['source']}, {e['source_record']})" for e in c["evidence"]),
                 8, "I", 2)
        pdf.ln(2)
    if r["rejected"]:
        para("Claims removed by the verifier", 12, "B")
        for c in r["rejected"]:
            para(f"- ({c['role']}) {c['claim']} - {'; '.join(c['reasons'])}", 8)
    return bytes(pdf.output())


# ------------------------------------------------------------------ endpoints
# CC-43: the pages are re-read on every request and must be revalidated by the browser after a redeploy (no stale page
# with handlers that no longer match the server); the API answers are unaffected.
PAGE_HEADERS = {"Cache-Control": "no-cache"}


def page_response(html):
    """An HTML page with PAGE_HEADERS. Takes the page text; returns an HTMLResponse."""
    return HTMLResponse(html, headers=PAGE_HEADERS)


# SEC-09: the pages and the two status routes answer HEAD as well as GET (uptime monitors and link checkers send HEAD).
@app.head("/", include_in_schema=False)
@app.get("/", response_class=HTMLResponse)
def home():
    """The front page (docs/ux/V3_DESIGN_2026-11-08.md; owner decision 11 Nov: the only front page): a bottom line on every
    answer, stored answers, inline evidence, graph hover cards, prediction paths and per-role worked examples from the trial
    layer (pipelines/persona_examples.py, read from the trial graph here). Returns the page with the examples injected."""
    import persona_examples
    html = (Path(__file__).parent / "app_page_v3.html").read_text(encoding="utf-8")
    try:
        examples = persona_examples.build(state()[0], personas.load_personas())
    except Exception:                                    # the page still works without its worked examples
        examples = {}
    return page_response(persona_examples.inject(html, examples))


@app.head("/classic", include_in_schema=False)
@app.get("/classic", response_class=HTMLResponse)
def home_classic():
    """The first page (app_page.html), kept for the regression journey only; nothing links to it. Returns the file as is."""
    return page_response((Path(__file__).parent / "app_page.html").read_text(encoding="utf-8"))


@app.get("/v3", include_in_schema=False)
def home_v3_moved(request: Request):
    """The front page used to live here: links shared before 11 Nov (?node=..., ?persona=...) still open it. Takes the
    request; returns a redirect to "/" with the same query (the browser carries the #... part over itself)."""
    query = request.url.query
    return RedirectResponse("/" + ("?" + query if query else ""), status_code=307)


def trial_summary_safe(report):
    """The additive 'summary' key of a trial report (answer_summary.trial_summary); a summary failure never breaks the answer."""
    try:
        return answer_summary.trial_summary(report)
    except Exception as err:
        return dict(error=f"summary not available: {err}")


def graph_summary_safe(res, graph):
    """The additive 'summary' key of a graph answer: answer_summary.pharos_summary with the facts of the cited edges looked up in
    `graph` (source, knowledge level, pruning verdict, confidence, reasons). A summary failure never breaks the answer."""
    try:
        eids = [e for row in res.get("rows") or [] for e in answer_summary.row_edges(row, res.get("columns") or [])]
        facts = answer_summary.edge_facts(graph, eids + list(res.get("evidence") or []))
        lab = graph.label if hasattr(graph, "label") else (lambda n: (graph.nodes.get(n) or {}).get("name") or n)
        if not res.get("entities") and res.get("rows"):        # FIX1 F8: a tested question is read from the query it ran
            res = dict(res, entities=answer_summary.query_entities(res.get("cypher"), graph))
        return answer_summary.pharos_summary(res, facts, {e: lab(e) for e in res.get("entities") or []})
    except Exception as err:
        return dict(error=f"summary not available: {err}")


class SummaryRequest(BaseModel):
    kind: str                                    # 'pharos' (a Pharos answer) | 'query' (a trial-graph answer) | 'trial' (a report)
    result: dict


@app.post("/api/summarize")
def api_summarize(r: SummaryRequest):
    """The bottom line for an answer the page already holds (a /api/query answer, a /api/pharos/demo item): same rules as the
    'summary' key of /api/ask and /api/pharos/ask. Read-only."""
    if r.kind == "trial":
        return trial_summary_safe(r.result)
    if r.kind == "query":
        out = graph_summary_safe(r.result, state()[0])
        res = r.result if isinstance(r.result, dict) else {}
        label = usability.answer_label(res)
        if label.get("kind") == "model_unavailable":                  # PK1: a failed model is not "the graph returned no rows"
            out = model_unavailable_summary(out, label)
        elif res.get("server_error") and isinstance(out, dict) and "headline" in out:   # FIX1 F5: not run, not "no rows"
            out = dict(out, headline=QUERY_FAILED_TEXT, next_step=res.get("next_step") or "", cards=res.get("cards") or [],
                       what_would_change=["The server running the question: the same question may return rows when asked again."],
                       points=[dict(text=label["text"], refs={})],
                       confidence=dict(out.get("confidence") or {}, level="low",
                                       reason="No query was run, so this says nothing about what the graph holds."))
        return out
    if r.kind == "pharos":
        return pjson(graph_summary_safe(r.result, answering_graph()))
    raise HTTPException(400, f"unknown kind {r.kind!r}: use pharos, query or trial")


def report_failed(err):
    """SEC-10: a failed step of the report. Logs the error; returns the JSON error body (HTTP 503, REPORT_FAILED_TEXT)."""
    traceback.print_exception(type(err), err, err.__traceback__, file=sys.stderr)
    return error_json(503, REPORT_FAILED_TEXT, "report_failed", retry_after=30)


QuestionText = Query(max_length=MAX_QUESTION_CHARS)       # the question of the GET endpoints: same cap as Question


@app.post("/api/ask")
def api_ask(q: Question):
    try:
        rep = run_report(q.question)
    except Busy:
        raise
    except Exception as err:                                # a stage failed: a JSON body, not a bare 500
        return report_failed(err)
    out = dict(rep, markdown=orchestrator.to_markdown(rep))
    out["summary"] = trial_summary_safe(out)                # additive (v3 bottom line)
    return routes_summary.report_answer(out, sys.modules[__name__])        # PK4: summary_text / summary_meta when written


@app.get("/api/report.pdf")
def api_pdf(q: str = QuestionText):
    """The report for q as a PDF: the report this server already wrote for the question (SEC-04), else a fresh run."""
    try:
        rep = report_for(q)
    except Busy:
        raise
    except Exception as err:
        return report_failed(err)
    return Response(report_pdf(rep), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="trialmindkg_report.pdf"'})


@app.get("/api/evidence/{edge_id}")
def api_evidence(edge_id: str, seeds: str = Query("", max_length=20000)):
    card = evidence_card(state()[0], edge_id, [s for s in seeds.split(",") if s])
    if card is None:
        raise HTTPException(404, f"no edge {edge_id}")
    return card


def report_subgraph_zip(kg, report):
    """Neptune bulk-load files (openCypher CSV) for exactly the nodes and edges behind one report."""
    import io, zipfile, tempfile
    from kg_schema import KG
    import export
    eids = sorted({e for s in report["sections"] for e in s["subgraph"].get("edge_ids", [])})
    g = KG()
    for e in eids:
        x = kg.edges[e]
        for n in (x["subject"], x["object"]):
            g.nodes[n] = dict(kg.nodes[n])
        g.edges[e] = dict(x)
    with tempfile.TemporaryDirectory() as d:
        nodes, edges, nk, ek = export.write_tables(g, Path(d))
        export.write_neptune(nodes, edges, nk, ek, Path(d))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in ("kg_nodes.csv", "kg_edges.csv", "neptune/opencypher/nodes.csv", "neptune/opencypher/edges.csv"):
                z.write(Path(d) / f, f)
            z.writestr("README.txt", f"Subgraph behind the TrialMindKG report for: {report['question']}\n"
                                     f"{len(g.nodes)} nodes, {len(g.edges)} edges; graph version "
                                     f"{report['run']['graph_version'].get('snapshot')}\n")
    return buf.getvalue()


@app.get("/api/report_subgraph.zip")
def api_report_subgraph(q: str = QuestionText):
    """The subgraph behind the report for q: from the report this server already wrote (SEC-04), else a fresh run."""
    try:
        rep = report_for(q)
    except Busy:
        raise
    except Exception as err:
        return report_failed(err)
    return Response(report_subgraph_zip(state()[0], rep), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="trialmindkg_report_subgraph.zip"'})


STEP = {"seeds": "Found the entities named in the question", "retrieve": "Selected a subgraph per specialist (PCST)",
        "failure_diagnostician": "Failure Diagnostician drafted claims", "population_stratifier":
        "Population Stratifier drafted claims", "indication_explorer": "Indication Explorer drafted claims",
        "redesign_advisor": "Trial Redesign Advisor drafted claims",
        "verify": "Verifier checked every claim (one rewrite for rejected claims)", "report_node": "Report assembled"}


def sse(event, data):
    """One server-sent event as text. Takes the event name and a JSON-able payload."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/ask_stream")
def api_ask_stream(q: str = QuestionText):
    """Server-sent events: one 'step' event per pipeline stage as it finishes, then a 'report' event. SEC-10: when a
    stage fails or the demo is busy, one 'error' event {detail: a plain sentence, error: kind} is sent before the
    stream ends (it used to end silently). The report is kept for the PDF / subgraph exports."""
    from fastapi.responses import StreamingResponse
    import time as _t
    kg = state()[0]

    def events():
        t0, report = _t.time(), None
        try:
            with model_slot():
                for update in orchestrator.build_app(kg, model_client()).stream({"question": q}):
                    for node, out in update.items():
                        yield sse("step", dict(step=node, label=STEP.get(node, node)))
                        if node == "report_node":
                            report = out["report"]
            report["run"]["seconds"] = round(_t.time() - t0, 2)
            report = remember_report(q, dict(report, label=usability.answer_label(report.get("run", {}))))
            final = dict(report, markdown=orchestrator.to_markdown(report))
            final["summary"] = trial_summary_safe(final)                                   # additive
            final = routes_summary.report_answer(final, sys.modules[__name__])            # PK4: model-written summary
            yield sse("report", final)
        except Busy as err:
            yield sse("error", dict(detail=str(err) or BUSY_TEXT, error="busy"))
        except Exception as err:
            traceback.print_exception(type(err), err, err.__traceback__, file=sys.stderr)
            yield sse("error", dict(detail=REPORT_FAILED_TEXT, error="report_failed"))
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/trial/{nct}")
def api_trial(nct: str):
    kg = state()[0]
    tid = nct if nct.startswith("NCT:") else f"NCT:{nct}"
    if tid not in kg.nodes:
        raise HTTPException(404, f"no trial {nct}")
    return dict(trial=kg.nodes[tid], outcomes=kg.trial_outcomes(nct, kind=None)["rows"],
                drugs=kg.trial_drugs(nct)["rows"], safety=kg.trial_safety(nct)["rows"],
                publications=kg.trial_publications(nct)["rows"])


@app.post("/api/query")
def api_query(q: Question):
    try:
        with model_slot():
            res = querier.Querier(model_client(), state()[0]).ask(q.question)
    except (Busy, querier.QueryBusy):
        raise                                            # their own handlers: 503 with Retry-After
    except Exception as err:                             # FIX1 F5 (G1-P1-02): never a bare HTTP 500 for a typed question
        traceback.print_exception(type(err), err, err.__traceback__, file=sys.stderr)
        return query_failed_answer(q.question)
    out = query_display(res)
    if isinstance(out, dict):
        note_model_outcome(usability.answer_label(out))  # FIX1 F4: /api/status tells the truth
    return out


QUERY_FAILED_TEXT = "This question could not be run on the trial graph because of a problem on the server."


def query_failed_answer(question):
    """FIX1 F5. Takes the question of a /api/query that raised. Returns an answer of the /api/query shape with no rows,
    no_query True, server_error True and its label (kind 'not_run'), and `cards`: the trials and failed programs named in
    the question (orchestrator.find_seeds; at most two, each {id, label, kind 'trial'}), so the page can lead to them."""
    cards = []
    try:
        kg = state()[0]
        for nid in orchestrator.find_seeds(kg, question):
            n = kg.nodes.get(nid) or {}
            if n.get("category") in ("biolink:ClinicalTrial", "tmkg:ClinicalTrialProgram"):
                cards.append(dict(id=nid, label=n.get("acronym") or n.get("name") or nid, kind="trial"))
        cards.sort(key=lambda c: (not c["id"].startswith("NCT:"), c["label"]))   # a named trial before its program
        seen, uniq = set(), []
        for c in cards:
            if c["label"] not in seen:
                seen.add(c["label"])
                uniq.append(c)
        cards = uniq[:2]
    except Exception:
        cards = []
    nxt = (("Open the " + " or the ".join(f"{c['label']} record" for c in cards) +
            f", which {'is' if len(cards) == 1 else 'are'} read from the trial graph without a query; or ") if cards else "") + "try again in a minute, or use one of the stored answers."
    label = dict(kind="not_run", offline=False, text=QUERY_FAILED_TEXT + " " + nxt[0].upper() + nxt[1:])
    return dict(question=question, entities=[c["id"] for c in cards], cypher="", neo4j="", problems=[QUERY_FAILED_TEXT],
                columns=[], rows=[], evidence=[], attempts=[], no_query=True, server_error=True, label=label, cards=cards,
                next_step=nxt[0].upper() + nxt[1:])


def query_display(res):
    """PK2: takes a live trial-graph query answer. Returns it with a failed program's internal id ('P22') in an id column
    shown as the program's name, the same display a stored answer gets (stored_answers.program_names). The cypher, the
    evidence ids and every other key stay as they are; a display step that fails leaves the answer as it is."""
    try:
        import stored_answers, answer_summary
        if isinstance(res, dict) and isinstance(res.get("rows"), list):
            return dict(res, rows=stored_answers.program_names(res["rows"], res.get("cypher"), answer_summary.program_facts()))
    except Exception:
        pass
    return res


def search(kg, q, limit=20):
    """Nodes whose id, name or acronym contains q (case-insensitive); trials and programs first."""
    q = q.lower().strip()
    order = {"tmkg:ClinicalTrialProgram": 0, "biolink:ClinicalTrial": 1, "biolink:Drug": 2, "biolink:Disease": 3}
    hits = [n for n in kg.nodes.values() if q and any(q in str(n.get(k) or "").lower() for k in ("id", "name", "acronym"))]
    hits.sort(key=lambda n: (order.get(n["category"], 9), len(n.get("name") or n["id"]), n["id"]))
    return [dict(id=n["id"], label=retrieval.label(n, kg), category=n["category"]) for n in hits[:limit]]


def neighbourhood(kg, node_id, limit=150):
    """1-hop neighbourhood of a node as drawable graph JSON (edges in a stable order)."""
    es = sorted(kg.by_subject.get(node_id, []) + kg.by_object.get(node_id, []), key=lambda e: (e["predicate"], e["id"]))
    es = es[:limit]
    nodes = sorted({node_id} | {e["subject"] for e in es} | {e["object"] for e in es})
    return orchestrator.graph_json(kg, nodes, [e["id"] for e in es])


@app.get("/api/search")
def api_search(q: str = Query(max_length=200)):
    return search(state()[0], q)


@app.get("/api/neighbourhood/{node_id}")
def api_neighbourhood(node_id: str):
    kg = state()[0]
    if node_id not in kg.nodes:
        raise HTTPException(404, f"no node {node_id}")
    return dict(center=node_id, node=kg.nodes[node_id], **neighbourhood(kg, node_id))


@app.head("/api/status", include_in_schema=False)
@app.get("/api/status")
def api_status():
    return status(state()[0])


@app.get("/api/landing")
def api_landing(request: Request):
    """The first screen: three example chips for the one search box (taken from the role pages' own examples), the model
    label for the header (with the start-up check of the hosted models, CC-36), the graph backends the switch offers and
    (PK15) via_cloudfront (this request came through Amazon CloudFront: its Via header) and runs_on, the "Runs on" line with
    each part only when it is true for this server (routes_neptune.runs_on)."""
    model = usability.model_label(state()[1], preflight_state(), paused=model_paused())   # FIX1 F4: says when typed questions are paused
    backends, via = neptune_backend.backend_options(), routes_neptune.via_cloudfront(request.headers)
    return dict(chips=usability.landing_chips(personas.load_personas()), model=model, headline=_headline(),
                backends=backends, backend_default=_default_backend(), via_cloudfront=via,
                runs_on=routes_neptune.runs_on(not model["offline"], backends[1]["state"], via))


def _default_backend():
    """TMKG_GRAPH_BACKEND (local | neptune) if set and available, else local."""
    want = os.environ.get("TMKG_GRAPH_BACKEND", "local")
    return want if any(o["id"] == want and o["available"] for o in neptune_backend.backend_options()) else "local"


@app.get("/api/attribution")
def api_attribution():
    """The data attribution footer (CC-40, pipelines/attribution.py): {notice, items: [{source, licence, text, url,
    conditions}], licences_file, versions}; shown at the bottom of the page."""
    import attribution
    return attribution.load_footer(ROOT)


@app.head("/DATA_LICENSES.md", include_in_schema=False)
@app.get("/DATA_LICENSES.md")
def data_licenses():
    """The full licence record (DATA_LICENSES.md) as plain text, linked from the footer."""
    p = ROOT / "DATA_LICENSES.md"
    if not p.is_file():
        raise HTTPException(404, "DATA_LICENSES.md is not in this build")
    return Response(p.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


@app.get("/api/backends")
def api_backends():
    return dict(options=neptune_backend.backend_options(), default=_default_backend())


@app.get("/api/generalization")
def api_generalization():
    """The Generalization tab: the trial layer, the bring-your-own-source example (built when asked) and the ADC example."""
    return usability.generalization(ROOT)


import routes_neptune; app.include_router(routes_neptune.router)   # noqa: E401,E702  PK15: GET /api/neptune/status, POST /api/neptune/wake (not questions)
import routes_agent; app.include_router(routes_agent.router)   # GET /api/agent-log: the dated "what the agent did" log
import routes_pages; app.include_router(routes_pages.router)   # noqa: E401,E702  PK7B: GET /api/page/{name}: the tutorial and the ADC answer as readable pages


@app.get("/api/agent-loop")
def api_agent_loop():
    """The "For judges" tab: the brief's agent loop (Ingest, Filter, Clean, Limit, Maintain) mapped to modules, commands and tests."""
    return usability.agent_loop(ROOT)


@app.get("/api/downloads")
def api_downloads():
    """The Downloads section of the For-judges page: files listed in downloads.json under the deploy-time base URL (hidden while the base is empty)."""
    return usability.downloads(ROOT)


@app.get("/api/personas")
def api_personas():
    return dict(personas=personas.load_personas())


@app.get("/api/wording")
def api_wording():
    return personas.wording()


def trials_for(kg, ids=(), names=(), max_hops=3, limit=10):
    """Takes the trial graph, node ids and/or names (a Pharos gene symbol, disease name, compound name). Finds the
    trial-graph nodes with that id or that name (case-insensitive; genes also by symbol) and walks up to max_hops
    edges from them to collect clinical trials and trial programs. Returns [{id, label, category, via}] (via = the
    labels on the path from the matched node), at most `limit`, trials first."""
    wanted_ids, wanted_names = set(ids), {n.lower() for n in names if n}
    starts = [n["id"] for n in kg.nodes.values() if n["id"] in wanted_ids or
              str(n.get("name") or "").lower() in wanted_names or str(n.get("symbol") or "").lower() in wanted_names]
    found, seen = {}, set(starts)
    frontier = [(s, [retrieval.label(kg.nodes[s], kg)]) for s in starts]
    for _ in range(max_hops):
        nxt = []
        for nid, path in frontier:
            for e in kg.by_subject.get(nid, []) + kg.by_object.get(nid, []):
                other = e["object"] if e["subject"] == nid else e["subject"]
                if other in seen:
                    continue
                seen.add(other)
                n = kg.nodes.get(other, {})
                lab = retrieval.label(n, kg) if n else other
                if n.get("category") in ("biolink:ClinicalTrial", "tmkg:ClinicalTrialProgram"):
                    found.setdefault(other, dict(id=other, label=lab, category=n["category"], via=path))
                nxt.append((other, path + [lab]))
        frontier = nxt
    out = sorted(found.values(), key=lambda t: (t["category"] != "biolink:ClinicalTrial", len(t["via"]), t["label"]))
    return out[:limit]


TRIAL_WORDS = re.compile(r"\b(trials?|failed|fail(s|ure|ures)?|phase|clinical|stopped|terminated)\b", re.I)


def trial_panel(question, res, graph, kg, max_names=10):
    """CC-31f (review finding M7): the trial layer for a graph question that mentions trials (TRIAL_WORDS). Takes the question,
    the graph answer, the graph that answered it and the trial graph. Returns None when the question does not mention trials,
    else {reason, names, trials}: trials_for on the answer's entities and the names in its answer column (at most max_names),
    so "failed trials" is answered from the trial layer next to the graph rows instead of being dropped."""
    m = TRIAL_WORDS.search(question or "")
    if not m:
        return None
    ids = list(res.get("entities") or [])
    names = [str(graph.label(i)) for i in ids if hasattr(graph, "label")]
    col = answer_summary._label_column(res.get("columns") or [], res.get("rows") or [])
    for r in res.get("rows") or []:
        v = r.get(col) if col else None
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and x and x not in names:
                names.append(x)
    names = names[:max_names + len(ids)]
    trials = trials_for(kg, ids, names) if (ids or names) else []
    return dict(reason=f"the question mentions '{m.group(0)}'", names=names, trials=trials)


@app.get("/api/stored_answers")
def api_stored_answers():
    """CC-31h: the stored answers the page may show for a worked example or a role question (no answers, only which exist)."""
    import stored_answers
    return dict(answers=stored_answers.index(stored_answers.FOLDER))


@app.get("/api/stored_answer/{sid}")
def api_stored_answer(sid: str):
    import stored_answers
    d = stored_answers.load(stored_answers.FOLDER, sid)
    if d is None:
        raise HTTPException(404, f"no stored answer {sid}")
    return pjson(routes_summary.stored_answer(d, sys.modules[__name__]))      # PK4: the stored summary, when there is one


@app.get("/api/trials_for")
def api_trials_for(ids: str = Query("", max_length=20000), names: str = Query("", max_length=20000)):
    return dict(trials=trials_for(state()[0], [i for i in ids.split(",") if i], [n for n in names.split(",") if n]))


BIOLINK_VOCAB = "https://w3id.org/biolink/vocab/"
DOWNLOADS = {                     # name -> (relative path, what it is); only these may be served by /api/file
    "compare.md": ("outputs/linkpred/compare.md", "frozen benchmark: each graph minus G0, paired over folds, 95% interval"),
    "predictions.tsv.gz": ("outputs/linkpred/predictions.tsv.gz", "predicted links with scores (linkpred/predict.py)"),
    "predictions_metapath.tsv.gz": ("outputs/linkpred/predictions_metapath.tsv.gz",
                                    "predicted links with the named paths and edge ids behind them (linkpred/predict.py --model metapath)"),
    "edge_verdicts.tsv.gz": ("data/kg/pharos/edge_verdicts.tsv.gz", "keep / prune verdict, confidence and reasons for every Pharos edge"),
    "prune_report.md": ("outputs/pharos/prune_report.md", "pruning v1 report"),
    "prune_calibration.md": ("outputs/pharos/prune_calibration.md", "pruning confidence vs Open Targets (held-out calibration)"),
    "summary.md": ("outputs/pharos/summary.md", "Pharos layer build summary"),
    "SCHEMA_CARD.md": ("docs/SCHEMA_CARD.md", "schema card: classes, predicates, IRIs, ontology terms, counts per layer"),
    "kg_edges.csv": ("outputs/kg_edges.csv", "trial layer, every edge with provenance (KGX-style CSV)"),
    "kgx_edges.tsv": ("outputs/kgx/edges.tsv", "trial layer, KGX edges"),
}


def results_page(root, pharos_graph=None, fallback_root=None):
    """Takes the data root, the optional fallback root (this repo's committed outputs/ in the image: a result file absent
    under root is read from there, numbers_page.numbers) (and the loaded Pharos graph, or the card index, which has the same status() /
    schema_card() numbers, when there is one). Returns the Numbers page data:
    benchmark = the frozen link-prediction runs and their paired comparisons (numbers_page.numbers: only
    outputs/linkpred/*@frozen_*.json, never smoke runs), calibration = outputs/pharos/prune_calibration.json with
    sentences computed from it, sources = the file each number came from, verdicts = keep/prune counts of the loaded
    graph (None without it), schema = classes, predicates and patterns with BioLink IRIs and counts (None without
    the Pharos graph), files = the downloadable files that exist."""
    import numbers_page
    root = Path(root)
    nums = numbers_page.numbers(root, fallback_root=fallback_root)
    status = getattr(pharos_graph, "graph_status", None) or getattr(pharos_graph, "status", None)
    verdicts = status()["verdicts"] if pharos_graph is not None else None
    schema = None
    if pharos_graph is not None:
        card, st = pharos_graph.schema_card(), status()
        counts = {k.split(":")[-1]: v for k, v in st["nodes_by_category"].items()}
        preds = {}
        for s, p, o, n in card["patterns"]:
            preds[p] = preds.get(p, 0) + n
        try:                                     # ontology terms per class, from the BioLink model file itself
            import schema_card
            terms = schema_card.load_biolink(schema_card.biolink_yaml(root, ROOT))["classes"]   # data root, then the image
        except (FileNotFoundError, ImportError):
            terms = {}
        schema = dict(classes=[dict(name=k, iri=BIOLINK_VOCAB + k, n_nodes=counts.get(k, 0), properties=v,
                                    terms=terms.get(k, {}).get("exact_mappings", [])) for k, v in card["labels"].items()],
                      predicates=[dict(name=p, iri=BIOLINK_VOCAB + p, n_edges=n) for p, n in sorted(preds.items())],
                      patterns=[dict(subject=s, predicate=p, object=o, n=n) for s, p, o, n in card["patterns"]],
                      n_classes=len(card["labels"]), n_predicates=len(preds), note="Pharos core: BioLink classes and "
                      "predicates only; the ontology terms are the BioLink model's own exact mappings (docs/SCHEMA_CARD.md)")
    try:                                         # the brief's three IRIs: where they are (CC-34)
        import schema_card
        iris = schema_card.brief_iris(ROOT / "sources" / "pharos.yaml",
                                      {k: list(v) for k, v in pharos_graph.schema_card()["labels"].items()} if pharos_graph is not None else None)
    except Exception:
        iris = None
    files = [dict(name=k, what=w, bytes=f.stat().st_size) for k, (p, w) in DOWNLOADS.items()
             if (f := _download_path(root, p, fallback_root)) is not None]
    return dict(benchmark=nums["benchmark"], calibration=nums["calibration"], experiments=nums["experiments"],
                sources=nums["sources"], fallback_files=nums["fallback_files"], headline=_headline(), brief_iris=iris,
                verdicts=verdicts, schema=schema, files=files, neptune=nums.get("neptune"))      # PK15: numbers_row or None


def _headline():
    """The headline (pipelines/headline.py) from this repo's committed result files: {text, sentences, files}; None on error."""
    try:
        import headline
        h = headline.headline(ROOT)
        return dict(text=h["text"], sentences=h["sentences"], files=h["files"]) if h["sentences"] else None
    except Exception:                                   # the page must not fail on a summary
        return None


def _download_path(root, rel, fallback_root=None):
    """Takes the data root, a DOWNLOADS relative path and the optional fallback root. Returns the existing file (data root
    first) or None."""
    for r in [Path(root)] + ([Path(fallback_root)] if fallback_root is not None else []):
        if (r / rel).is_file():
            return r / rel
    return None


def _data_root():
    """The repo root the Pharos files are read from: the loaded graph's, else TMKG_PHAROS_ROOT, else this repo."""
    import os
    g = _pharos.get("g")
    return Path(g.root if g is not None else os.environ.get("TMKG_PHAROS_ROOT") or ROOT)


@app.get("/api/results")
def api_results():
    g = _pharos.get("g")
    return results_page(_data_root(), g if g is not None else card_index(), fallback_root=ROOT)


@app.get("/api/file")
def api_file(name: str):
    import numbers_page
    root = _data_root()
    if name in DOWNLOADS:
        path = _download_path(root, DOWNLOADS[name][0], ROOT)
    else:                                      # a file the Numbers page cites (frozen results, calibration)
        path = numbers_page.source_file(root, name, fallback_root=ROOT)
        if path is None and not numbers_page.FROZEN_RE.match(name) and name not in numbers_page.OTHER_FILES:
            raise HTTPException(404, f"{name} is not a downloadable file")
    if path is None or not path.exists():
        raise HTTPException(404, f"{name} is not loaded on this server")
    url = big_file_url(name, path.stat().st_size)
    if url:                                    # SEC-16: a very large file comes from the downloads folder, not this worker
        return RedirectResponse(url, status_code=307)
    return FileResponse(path, filename=name)


def big_file_url(name, size, env=None):
    """SEC-16. Takes a /api/file name, its size in bytes and the environment. Returns the downloads-folder address to send
    the visitor to, or None: only for a file above BIG_FILE_BYTES that BIG_FILE_FOLDERS maps to a folder downloads.json
    lists, and only on a server whose first boot set TMKG_DOWNLOADS_BASE (the hosted instance, where Caddy serves that
    folder). Everywhere else (the Mac, a server without the folder) the file is served as before."""
    env = os.environ if env is None else env
    folder = BIG_FILE_FOLDERS.get(name)
    if size <= BIG_FILE_BYTES or not folder or not (env.get("TMKG_DOWNLOADS_BASE") or "").strip():
        return None
    d = usability.downloads(ROOT, env=env)
    hit = next((f for f in d.get("files", []) if f.get("path") == folder and f.get("url")), None) if d.get("available") else None
    return hit["url"] if hit else None


# ------------------------------------------------------------------ Pharos graph
_pharos = {}


def pharos():
    """The Pharos graph (pharos_app.PharosGraph), loaded once per process on first use; 503 when not built."""
    if "g" not in _pharos:
        import pharos_app
        with _state_lock:                           # SEC-17: one load, however many first requests arrive together
            if "g" not in _pharos:
                try:                                # TMKG_PHAROS_ROOT: another repo root (the test fixture, screenshots)
                    _pharos["g"] = pharos_app.PharosGraph(root=os.environ.get("TMKG_PHAROS_ROOT") or ROOT)
                except FileNotFoundError as err:
                    raise HTTPException(503, str(err))
    return _pharos["g"]


def card_index():
    """The build-time card index (pharos_index.CardIndex) when its file exists, else None; opened once per process.
    TMKG_PHAROS_INDEX names another file; TMKG_PHAROS_INDEX=off turns it off (the live graph answers everything)."""
    if "index" not in _pharos:
        import pharos_index
        with _state_lock:                           # SEC-17
            if "index" not in _pharos:
                env = os.environ.get("TMKG_PHAROS_INDEX", "")
                path = None if env == "off" else Path(env) if env else pharos_index.index_path(os.environ.get("TMKG_PHAROS_ROOT") or ROOT)
                _pharos["index"] = pharos_index.CardIndex(path) if path is not None and path.exists() else None
    return _pharos["index"]


def nan_safe(x):
    """Takes any JSON-able value. Returns it with every NaN / inf float replaced by None (a missing score must be `null`, not a 500:
    'Out of range float values are not JSON compliant'). Used by every /api/pharos/* answer."""
    import math
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: nan_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [nan_safe(v) for v in x]
    return x


def pjson(x, status_code=200):
    return JSONResponse(nan_safe(x), status_code=status_code)


def answering_graph():
    """What the page's explain / node / predict / demo / ask / subgraph calls read: the card index's graph view when the index exists
    (never the 26-million-edge live graph: it takes minutes and 10 GB), else the live graph (TMKG_PHAROS_INDEX=off, or no index built)."""
    idx = card_index()
    return idx.graph(_data_root()) if idx is not None else pharos()          # CC-38: overlay files in the data root


def _novelty_file(rel):
    """A CS-5 novelty file: under the data root, else the repo copy in the image."""
    p = _data_root() / rel
    return p if p.is_file() else ROOT / rel


@app.head("/api/pharos/status", include_in_schema=False)
@app.get("/api/pharos/status")
def api_pharos_status():
    import pharos_app
    idx = card_index()
    st = idx.graph_status() if idx is not None else pharos().status()    # the index: no 26M-edge load on page open
    return pjson(dict(st, novelty=pharos_app.novelty_summary(_novelty_file(pharos_app.NOVELTY_MD))))


@app.get("/api/pharos/search")
def api_pharos_search(q: str = Query(max_length=200), suggest: int = 0):
    """Nodes matching q. With suggest=1 and no match: the "did you mean" nodes instead (each with did_you_mean: true)."""
    idx = card_index()
    g = idx if idx is not None else pharos()
    hits = g.search(q)
    if not hits and suggest:
        hits = g.suggest(q)
    import pharos_app
    return pjson(pharos_app.rank_same_label(hits, _link_counts(g, [h["id"] for h in hits])))   # FIX1 F9


def _link_counts(g, ids):
    """FIX1 F9. Takes the searched graph (the card index or the live Pharos graph) and node ids. Returns {id: links of the node}
    (the card index: the sum of its evidence kinds; the live graph: PharosGraph.link_counts); {} when neither can say."""
    try:
        if hasattr(g, "link_counts"):
            return g.link_counts(ids)
        con = getattr(g, "con", None)
        if con is None or not ids:
            return {}
        rows = con.execute(f"SELECT node, sum(n) FROM kinds WHERE node IN ({','.join('?' * len(ids))}) GROUP BY node", list(ids))
        return {r[0]: int(r[1] or 0) for r in rows}
    except Exception:
        return {}


@app.get("/api/pharos/node/{node_id}")
def api_pharos_node(node_id: str):
    v = answering_graph().node_view(node_id)
    if v is None:
        raise HTTPException(404, f"no Pharos node {node_id}")
    return pjson(v)


@app.get("/api/pharos/explain/{edge_id}")
def api_pharos_explain(edge_id: str):
    x = answering_graph().explain_edge(edge_id)
    if x is None:
        raise HTTPException(404, f"no Pharos edge {edge_id}" + (" in the card index (it holds the links the cards and the tested "
                                                                "questions show; rebuild it with pipelines/pharos_index.py)"
                                                                if card_index() is not None else ""))
    return pjson(routes_summary.edge(x, sys.modules[__name__]))             # PK4: the stored summary, when there is one


def clamp_n(n):
    """SEC-06: the number of rows asked for, kept between 1 and MAX_N (n=100000000 and n=-5 used to be passed on)."""
    return max(1, min(MAX_N, int(n)))


@app.get("/api/pharos/predict")
def api_pharos_predict(node: str, n: int = 20, task: str = ""):
    p = answering_graph().predicted_links(node, n=clamp_n(n), task=task or None)
    if p is None:
        raise HTTPException(404, f"no Pharos node {node}")
    return pjson(p)


@app.post("/api/pharos/ask")
def api_pharos_ask(q: Question):
    """PK15: backend 'neptune' never fails the user. The question is answered on the local path as always (the model writes the
    query ONCE; a tested question uses its fixed query); when Amazon Neptune is available the same validated query then runs
    there live (20 s budget) and the answer is Neptune's, with neptune.match comparing the two evidence sets. Asleep, waking,
    unreachable, an error or the budget used up: the local answer, graph_backend 'local', and neptune {answered False, note}."""
    import pharos_app
    if q.backend not in ("local", "neptune"):
        raise HTTPException(400, f"unknown graph backend {q.backend!r}: use local or neptune")
    graph = answering_graph()
    with model_slot():
        res = pharos_app.ask(graph, model_client(), q.question)
    nep = None
    if q.backend == "neptune":
        nep, remote = routes_neptune.ask_on_neptune(res, neptune_backend.NeptuneRunner(live=True).run,
                                                    rank=lambda r: querier.rank_rows(res["cypher"], r, graph))
        if remote is not None:
            res = pharos_app.display_result(dict(res, columns=remote.get("columns") or [], rows=remote.get("rows") or [],
                                                 evidence=remote.get("evidence") or [], ordering=remote.get("ordering")))
    on = "neptune" if nep and nep.get("answered") else "local"
    out = dict(res, graph_backend=on, **({"neptune": nep} if nep else {}))
    out["label"] = usability.answer_label(out)
    note_model_outcome(out["label"])                                # FIX1 F4: /api/status tells the truth
    out["trials_panel"] = trial_panel(q.question, res, graph, state()[0])        # CC-31f: the trial layer, too
    if card_index() is not None and on == "local":
        out["from_index"] = True                         # answered from the links the card index holds (see index_graph.py)
    out["summary"] = graph_summary_safe(out, graph)     # additive (v3 bottom line)
    if out["label"]["kind"] == "model_unavailable":                 # SEC-03: not "the graph returned no rows"
        out["summary"] = model_unavailable_summary(out["summary"], out["label"])
    # PK4: model-written summary of the rows; a Neptune answer uses a stored summary only (the model is called once per question)
    return pjson(routes_summary.pharos_answer(out, sys.modules[__name__], call=on == "local"))


def model_unavailable_summary(summary, label):
    """SEC-03. Takes the bottom line of an answer that has no rows because the hosted model did not answer, and that
    answer's label. Returns the summary with the headline, confidence reason, next step and "what would change" saying
    so (instead of 'The graph returned no rows'); the recognised entities and the other keys stay. A summary that
    failed (an 'error' key) is returned as it is."""
    if not isinstance(summary, dict) or "headline" not in summary:
        return summary
    # FIX1 F4 (G1-U-02): when a gene or disease was recognised, the next step leads to its card (cards: the page's buttons)
    lead, cards = usability.card_next_steps(((summary.get("plan") or {}).get("entities")) or [])
    nxt = (f"{lead}: cards are read from the graph without the model. Or try the question again in a minute, or use one of "
           "the tested questions.") if lead else "Try again in a minute, or use one of the tested questions."
    out = dict(summary, headline="The hosted model did not answer, so this question was not run on the graph.",
               next_step=nxt, cards=cards,
               what_would_change=["The hosted model answering: the same question may return rows when asked again."],
               model_unavailable=True)
    out["confidence"] = dict(summary.get("confidence") or {}, level="low",
                             reason="No query was run, so this says nothing about what the graph holds.")
    out["points"] = [dict(text=label["text"], refs={})] + [p for p in summary.get("points") or []
                                                           if "recognised" in str(p.get("text", ""))]
    return out


@app.get("/api/pharos/demo")
def api_pharos_demo():
    return pjson(answering_graph().demo())


@app.get("/api/pharos/demo_questions")
def api_pharos_demo_questions():
    """The fixed demo questions (id, question, why) for the question strip; nothing is run, the graph is not loaded."""
    import pharos_app
    return [dict(id=d["id"], question=d["question"], why=d["why"]) for d in pharos_app.DEMO_QUESTIONS]


@app.get("/api/pharos/demo/{demo_id}")
def api_pharos_demo_one(demo_id: str):
    out = answering_graph().demo(ids={demo_id})
    if not out:
        raise HTTPException(404, f"no demo question {demo_id}")
    one = dict(out[0])
    one["summary"] = graph_summary_safe(one, answering_graph())     # additive (v3 bottom line)
    return pjson(routes_summary.demo_answer(one, sys.modules[__name__]))     # PK4: the stored summary, when there is one


class SubgraphRequest(BaseModel):
    edges: list[str]
    title: str = ""


MAX_SUBGRAPH_EDGES = 20_000


@app.post("/api/pharos/subgraph.zip")
def api_pharos_subgraph(r: SubgraphRequest):
    """The nodes and edges behind one answer (its evidence edge ids) as KGX tables + PyG-ready tables in one zip."""
    import subgraph_export
    if not r.edges:
        raise HTTPException(400, "no edge ids given")
    if len(r.edges) > MAX_SUBGRAPH_EDGES:
        raise HTTPException(413, f"at most {MAX_SUBGRAPH_EDGES:,} edges per download")
    nodes, edges = subgraph_export.collect(answering_graph(), r.edges)
    if not edges:
        raise HTTPException(404, "none of these edge ids is in the Pharos graph")
    return Response(subgraph_export.zip_bytes(nodes, edges, r.title or "answer"), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="trialmindkg_pharos_subgraph.zip"'})


@app.get("/api/pharos/card/{node_id}")
def api_pharos_card(node_id: str, n: int = 10):
    t0 = time.perf_counter()
    idx = card_index()
    import pharos_app
    n = clamp_n(n)
    pool = max(n, pharos_app.PRED_POOL)                  # CC-30: read more, show the novel ones
    c = idx.card(node_id, n_pred=pool) if idx is not None else pharos().card(node_id, n_pred=pool)
    if c is None:
        raise HTTPException(404, f"no Pharos node {node_id}")
    c = pharos_app.split_predictions(c, pharos_app.ancestor_pairs(_novelty_file(pharos_app.NOVELTY_TSV)), n)
    c = routes_summary.card(c, sys.modules[__name__])                     # PK4: the stored summary, when there is one
    c["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)          # shown on the card: the 2-second usability gate
    return pjson(c)


import routes_compare; app.include_router(routes_compare.make_router(sys.modules[__name__]))   # Pharos alone vs agent-built (compare marks)
import routes_summary; app.include_router(routes_summary.make_router(sys.modules[__name__])); ASK_PATHS.add(routes_summary.PATH)  # noqa: E401,E702  PK4: model-written summaries
import routes_audit; app.include_router(routes_audit.make_router(sys.modules[__name__]))   # noqa: E401,E702  independent check on an edge (PK11)
import routes_predictions; app.include_router(routes_predictions.router)   # noqa: E401,E702  PK6: strength words of a prediction score


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
