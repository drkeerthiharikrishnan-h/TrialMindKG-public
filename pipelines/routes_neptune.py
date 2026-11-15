"""
Amazon Neptune beside the local graph (PK15): its state, waking it, asking the same query on it, and the sentences the page,
the Numbers tab and the "Runs on" line show. The local card index stays the DEFAULT answer path; Neptune is an addition that
nothing depends on (hand-off of 14 Nov, D1).

How the server knows the state (PK14 A2 builds the host side): the app container has NO AWS credentials. The host writes
$NEPTUNE_STATUS_DIR/status.json every 20 s, {"state": "available" | "stopped" | "starting" | "stopping" | ... | "unknown",
"checked_at": "<UTC ISO>"}, and starts the cluster when the file $NEPTUNE_STATUS_DIR/wake.request exists and the state is
"stopped", then deletes the file. Environment: NEPTUNE_ENDPOINT (empty or unset = not configured), NEPTUNE_STATUS_DIR (default
/neptune), NEPTUNE_WAKE_MINUTES (the minutes the page names; unset = "a few minutes"). Nothing here contacts Neptune except
ask_on_neptune, which the ask route calls only when the state is "available".

  neptune_state(env, now)          -> 'not_configured' | 'available' | 'asleep' | 'waking' | 'unavailable'   (pure, over the folder)
  wake_minutes(env)                -> int or None;   wake_words(env) -> "about 6 minutes" | "a few minutes"
  state_sentence(state, env)       -> the user sentence for a state that is not 'available' ('' for available)
  manifest_counts(roots)           -> {nodes, links} of outputs/neptune/full/manifest.json (first root that has it), or None
  request_wake(env, now)           -> the state after a wake request (writes wake.request at most once per 60 s, atomically;
                                      at most TMKG_NEPTUNE_WAKES_PER_DAY a day, then 'capped')
  loaded_counts(roots)             -> what is loaded: the export manifest, else outputs/neptune/live_parity.json
  cross_site(headers)              -> True for a wake sent by another site's page (refused)
  ask_on_neptune(answer, run, ...) -> the 'neptune' block of an answer {asked, answered, ...} and Neptune's answer (or None)
  compare(local, remote)           -> {rows_local, rows_neptune, links_local, links_neptune, links_identical}
  match_sentence(match)            -> "Same question on Amazon Neptune: 37 rows in 1.2 s; 37 of 37 evidence links identical."
  numbers_row(root, fallback_root) -> the Numbers sentence from outputs/neptune/live_parity.json, or None
  via_cloudfront(headers)          -> True when the request came through Amazon CloudFront (its Via header names it)
  runs_on(model_hosted, state, via_cf) -> {models, neptune, cloudfront, aws, text}: the "Runs on" line, each part only when true
  router                           -> GET /api/neptune/status, POST /api/neptune/wake (neither counts against the ask limit)
"""
import collections
import datetime
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
STATES = ("not_configured", "available", "asleep", "waking", "unavailable")
STALE_SECONDS = 90           # a status.json older than this is not believed (the host writes it every 20 s)
WAKE_EVERY = 60              # at most one wake.request write per this many seconds, for the whole server
WAKE_HOLD = 90               # S17 item 2: 'waking' for this long after this server wrote a wake request, whatever the host says
WAKES_PER_DAY = 6            # FIX2 F4: wake requests written per 24 h for the whole server (TMKG_NEPTUNE_WAKES_PER_DAY)
KNOWN_HOSTS = ("tmkg.16-178-197-241.sslip.io", "d1mdyg1ov91fxt.cloudfront.net")   # the two addresses of the hosted site
WAKE_CAPPED = "Amazon Neptune has been woken often today; the answer from this server's graph stands."
WAKE_CROSS_SITE = "Amazon Neptune can only be woken from this site's own page."
ASK_SECONDS = 20             # the whole budget of one Neptune query from the web app
MANIFEST = Path("outputs") / "neptune" / "full" / "manifest.json"
LIVE_PARITY = Path("outputs") / "neptune" / "live_parity.json"
STATUS_FILE, WAKE_FILE = "status.json", "wake.request"
TRIAL_LAYER = "trialmindkg"  # make_graphs.TRIAL_LAYER: the layer of failed drug programs

router = APIRouter()
_wake = {"at": None, "written": {}, "day": collections.deque()}   # monotonic time of the last write; per folder: epoch of it; day log
_wake_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="neptune")


# ---------------------------------------------------------------------------------------------------------------- the state
def _env(env):
    return os.environ if env is None else env


def status_dir(env=None):
    """Takes the environment. Returns the Path of the folder the host writes status.json into (NEPTUNE_STATUS_DIR, default /neptune)."""
    return Path(_env(env).get("NEPTUNE_STATUS_DIR") or "/neptune")


def _checked_at(text):
    """Takes the status file's checked_at text (UTC ISO, 'Z' or an offset). Returns epoch seconds, or None when unreadable."""
    try:
        t = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t.timestamp()


def neptune_state(env=None, now=None):
    """Takes the environment (default os.environ) and the time in epoch seconds (default now). Returns one of STATES:
    'not_configured' when NEPTUNE_ENDPOINT is empty or unset; 'available' when the host says available; 'waking' when the host
    says starting, or a wake request is waiting in the folder, or the host says stopped less than WAKE_HOLD seconds after this
    server wrote a wake request into that folder (the host deletes the request when it starts the cluster, and Amazon Neptune
    can still report stopped for a few seconds: S17 saw the page flicker back to asleep); 'asleep' when the host says stopped; 'unavailable' for any other
    state, and when status.json is missing, unreadable, without a readable checked_at, or older than STALE_SECONDS."""
    env = _env(env)
    if not (env.get("NEPTUNE_ENDPOINT") or "").strip():
        return "not_configured"
    now = time.time() if now is None else now
    d = status_dir(env)
    try:
        st = json.loads((d / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unavailable"
    if not isinstance(st, dict):
        return "unavailable"
    at = _checked_at(st.get("checked_at"))
    if at is None or abs(now - at) > STALE_SECONDS:
        return "unavailable"
    host = str(st.get("state") or "").strip().lower()
    if host == "available":
        return "available"
    if host == "starting" or (d / WAKE_FILE).exists():
        return "waking"
    written = _wake["written"].get(str(d))
    if host == "stopped" and written is not None and 0 <= now - written < WAKE_HOLD:
        return "waking"                       # the host took the request but still reports stopped for a few seconds (S17)
    if host == "stopped":
        return "asleep"
    return "unavailable"


def wake_minutes(env=None, roots=None):
    """Takes the environment and optionally the roots. Returns NEPTUNE_WAKE_MINUTES as a positive int (rounded up); when it is
    unset and roots are given, the measured wake_minutes of outputs/neptune/live_parity.json; else None."""
    raw = str(_env(env).get("NEPTUNE_WAKE_MINUTES") or "").strip()
    try:
        v = float(raw)
    except ValueError:
        v = None
    if (v is None or v <= 0) and roots:
        lp = _read_json(roots, LIVE_PARITY)
        m = lp.get("wake_minutes") if isinstance(lp, dict) else None
        v = float(m) if isinstance(m, (int, float)) and not isinstance(m, bool) else None
    return int(-(-v // 1)) if v and v > 0 else None


def wake_words(env=None, roots=None):
    """Takes the environment (and optionally the roots, see wake_minutes). Returns how long waking takes, in words: 'about 1
    minute', 'about N minutes' or 'a few minutes'."""
    m = wake_minutes(env, roots)
    return "a few minutes" if m is None else f"about {m} minute{'' if m == 1 else 's'}"


def state_sentence(state, env=None, roots=None):
    """Takes a state (STATES), the environment and optionally the roots (for the measured wake time). Returns the sentence a
    user reads about it ('' for 'available')."""
    return {
        "available": "",
        "not_configured": "Amazon Neptune is not set up on this server; the answers come from this server's graph.",
        "asleep": ("This answer comes from this server's copy of the graph. The same question can also run on Amazon Neptune "
                   f"(asleep to save cost; waking takes {wake_words(env, roots)})."),
        "waking": f"Amazon Neptune is waking up; this takes {wake_words(env, roots)}.",
        "unavailable": "Amazon Neptune cannot be reached from this server right now; the answers come from this server's graph.",
    }.get(state, "Amazon Neptune cannot be reached from this server right now; the answers come from this server's graph.")


def _roots(root=None, fallback_root=None):
    roots = [Path(root or ROOT)]
    if fallback_root is not None and Path(fallback_root).resolve() != roots[0].resolve():
        roots.append(Path(fallback_root))
    return roots


def _read_json(roots, rel):
    """Takes the roots (first wins) and a relative path. Returns the parsed JSON of the first root that has a readable file, or None."""
    for r in roots:
        p = Path(r) / rel
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
    return None


def _counts(d, links_key):
    """Takes a {nodes, <links_key>} dict. Returns {nodes, links} as ints, or None when either is missing or not a whole number."""
    if not isinstance(d, dict):
        return None
    n, e = d.get("nodes"), d.get(links_key)
    if isinstance(n, bool) or isinstance(e, bool) or not isinstance(n, int) or not isinstance(e, int):
        return None
    return dict(nodes=n, links=e)


def manifest_counts(roots):
    """Takes the roots to look in (first wins). Returns {nodes, links} from the export's manifest (counts.nodes, counts.edges of
    outputs/neptune/full/manifest.json), or None when no root ships the file or it has no counts."""
    man = _read_json(roots, MANIFEST)
    return _counts((man or {}).get("counts"), "edges") if isinstance(man, dict) else None


def loaded_counts(roots):
    """Takes the roots. Returns ({nodes, links}, source) of what is loaded in Amazon Neptune: the shipped export manifest when a
    root has it ('manifest'), else the measured counts of outputs/neptune/live_parity.json when they equal its own manifest
    counts ('live_parity'; S17 item 1: the export manifest is not in git, so the server never has it), else (None, '')."""
    counts = manifest_counts(roots)
    if counts:
        return counts, "manifest"
    lp = _read_json(roots, LIVE_PARITY)
    if isinstance(lp, dict) and lp.get("counts_equal") is not False:
        loaded, man = _counts(lp.get("loaded"), "links"), _counts(lp.get("manifest"), "links")
        if loaded and loaded == man:
            return loaded, "live_parity"
    return None, ""


def status(env=None, now=None, roots=None):
    """Takes the environment, the time and the roots. Returns GET /api/neptune/status: {state, wake_minutes, wake_words, sentence}
    plus loaded {nodes, links} and loaded_from ('manifest' | 'live_parity', loaded_counts) when Neptune is configured, in every
    state, and numbers (the Numbers-page sentence, numbers_row, in every state) when it is not None. Never contacts Neptune."""
    st = neptune_state(env, now)
    roots = roots or _roots()
    out = dict(state=st, wake_minutes=wake_minutes(env, roots), wake_words=wake_words(env, roots),
               sentence=state_sentence(st, env, roots))
    if st != "not_configured":
        counts, src = loaded_counts(roots)
        if counts:
            out["loaded"], out["loaded_from"] = counts, src
    row = numbers_row(*roots[:2])            # a measured fact about the load, true whatever the state is now
    if row:
        out["numbers"] = row
    return out


def wakes_per_day(env=None):
    """Takes the environment. Returns TMKG_NEPTUNE_WAKES_PER_DAY as an int (default WAKES_PER_DAY; 0 or less = no cap)."""
    try:
        return int(str(_env(env).get("TMKG_NEPTUNE_WAKES_PER_DAY") or WAKES_PER_DAY).strip())
    except ValueError:
        return WAKES_PER_DAY


def request_wake(env=None, now=None, clock=None):
    """Takes the environment, the time in epoch seconds (default now) and a monotonic clock value (default time.monotonic()).
    When the state is 'asleep' writes wake.request (atomically: a temporary file renamed into place) and returns 'waking';
    inside WAKE_EVERY seconds of the last write it writes nothing and returns 'waking'; when wakes_per_day() requests were
    written in the last 24 h it writes nothing and returns 'capped' (FIX2 F4); in any other state it writes nothing and returns
    the state unchanged. A folder that cannot be written returns 'unavailable'."""
    env = _env(env)
    st = neptune_state(env, now)
    if st != "asleep":
        return st
    clock = time.monotonic() if clock is None else clock
    epoch = time.time() if now is None else now
    with _wake_lock:
        if _wake["at"] is not None and clock - _wake["at"] < WAKE_EVERY:
            return "waking"
        day, cap = _wake["day"], wakes_per_day(env)
        while day and epoch - day[0] >= 86400.0:
            day.popleft()
        if cap > 0 and len(day) >= cap:
            return "capped"
        d = status_dir(env)
        stamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        tmp = d / f".{WAKE_FILE}.{os.getpid()}.tmp"
        try:
            tmp.write_text(json.dumps({"requested_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n", encoding="utf-8")
            os.replace(tmp, d / WAKE_FILE)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return "unavailable"
        _wake["at"] = clock
        _wake["written"][str(d)] = epoch
        day.append(epoch)
    return "waking"


# ------------------------------------------------------------------------------------------------- the same query on Neptune
def _links(answer):
    return {str(x) for x in (answer or {}).get("evidence") or [] if x}


def compare(local, remote):
    """Takes the local answer and Neptune's ({rows, evidence}). Returns {rows_local, rows_neptune, links_local, links_neptune,
    links_identical}: row counts, evidence link counts (as sets) and how many evidence links both share."""
    a, b = _links(local), _links(remote)
    return dict(rows_local=len((local or {}).get("rows") or []), rows_neptune=len((remote or {}).get("rows") or []),
                links_local=len(a), links_neptune=len(b), links_identical=len(a & b))


def match_sentence(match, ms=None):
    """Takes a compare() dict and the milliseconds Neptune took. Returns the line the page prints under the answer, with the
    numbers as they are: "<k> of <a> evidence links identical" (a = the local answer's links), and how many Neptune returned when
    that differs, so "<a> of <a>" is printed only when the two sets are equal."""
    m = match or {}
    rows = m.get("rows_neptune", 0)
    t = "" if ms is None else f" in {ms / 1000:.1f} s" if ms >= 100 else " in under 0.1 s"
    a, b, k = m.get("links_local", 0), m.get("links_neptune", 0), m.get("links_identical", 0)
    if a == b == 0:
        links = "no evidence links on either graph"
    else:
        links = f"{k} of {a} evidence link{'' if a == 1 else 's'} identical" + ("" if b == a else f" (Amazon Neptune returned {b})")
    return f"Same question on Amazon Neptune: {rows} row{'' if rows == 1 else 's'}{t}; {links}."


ASK_NOTES = {   # why a question asked on Neptune was answered by this server's graph, per state
    "not_configured": "Amazon Neptune is not set up on this server, so this answer comes from this server's graph.",
    "asleep": ("This answer comes from this server's copy of the graph. The same question can also run on Amazon Neptune "
               "(asleep to save cost; waking takes {words})."),
    "waking": "Amazon Neptune is waking up ({words}), so this answer comes from this server's graph for now.",
    "unavailable": "Amazon Neptune cannot be reached from this server right now, so this answer comes from this server's graph.",
}


def _not_answered(state, note):
    return dict(asked=True, answered=False, state=state, note=note)


def ask_on_neptune(answer, run, env=None, now=None, seconds=None, pool=None, rank=None):
    """Takes the local answer of a question (pharos_app.ask: its validated cypher, rows, evidence), run(cypher) -> {columns, rows,
    evidence} (NeptuneRunner(live=True).run), the environment, the time and the budget in seconds (default ASK_SECONDS). Runs the SAME query on Neptune
    only when the state is 'available' and the answer has a query. Returns (block, remote): block = the answer's 'neptune' key,
    {asked True, answered True, ms, match, sentence} when Neptune answered within the budget, else {asked True, answered False,
    state, note} (asleep, waking, unavailable, an error or the budget used up: the caller keeps the local answer); remote =
    Neptune's {columns, rows, evidence} or None. rank(remote) -> remote (querier.rank_rows on the local graph) orders Neptune's
    rows evidence-first as the local path does; a failing rank keeps Neptune's order. Never raises."""
    import neptune_backend
    st = neptune_state(env, now)
    if st != "available":
        return _not_answered(st, ASK_NOTES.get(st, ASK_NOTES["unavailable"]).format(
            words=wake_words(env, _data_roots() if env is None else None))), None
    cypher = (answer or {}).get("cypher") or ""
    if not cypher or answer.get("abstained"):
        return _not_answered(st, "This question was not run on Amazon Neptune: no graph query was written for it."), None
    if answer.get("problems"):               # the query did not pass here (too broad, invalid): it is not sent to Neptune either
        return _not_answered(st, "This question was not run on Amazon Neptune: its query did not pass the checks on this server."), None
    seconds = ASK_SECONDS if seconds is None else seconds
    t0 = time.monotonic()
    fut = (pool or _pool).submit(run, cypher)
    try:
        remote = fut.result(timeout=seconds)
    except FutureTimeout:
        fut.cancel()
        return _not_answered("unavailable", f"Amazon Neptune did not answer within {seconds:g} s, so the answer comes from this server's graph."), None
    except neptune_backend.NeptuneError:
        return _not_answered("unavailable", "Amazon Neptune could not run this question just now, so the answer comes from this server's graph."), None
    except Exception:            # a Neptune problem must never fail the user's question
        return _not_answered("unavailable", "Amazon Neptune could not run this question just now, so the answer comes from this server's graph."), None
    ms = int(round((time.monotonic() - t0) * 1000))
    if not isinstance(remote, dict):
        return _not_answered("unavailable", "Amazon Neptune could not run this question just now, so the answer comes from this server's graph."), None
    if rank is not None and remote.get("rows"):
        try:
            remote = rank(remote)
        except Exception:
            pass
    match = compare(answer, remote)
    return dict(asked=True, answered=True, state="available", ms=ms, match=match, sentence=match_sentence(match, ms)), remote


# ----------------------------------------------------------------------------------------------------- Numbers and Runs on
def _num(n):
    return f"{n:,}"


def numbers_row(root=None, fallback_root=None):
    """Takes the data root and an optional fallback root. Returns the Numbers-page sentence from outputs/neptune/live_parity.json
    (Claude Science writes it in task S16): "The whole graph is loaded in Amazon Neptune: <nodes> nodes and <links> links, equal to
    the download. <k> of <n> pinned questions return the same evidence there." None when the file is absent or unreadable, when
    its loaded counts differ from its own manifest counts (or counts_equal is false), or when the shipped export manifest is
    present and its counts differ. Every number is read from the files."""
    roots = _roots(root, fallback_root)
    lp = _read_json(roots, LIVE_PARITY)
    if not isinstance(lp, dict):
        return None
    loaded, man = _counts(lp.get("loaded"), "links"), _counts(lp.get("manifest"), "links")
    if not loaded or loaded != man or lp.get("counts_equal") is False:
        return None
    shipped = manifest_counts(roots)
    if shipped is not None and shipped != loaded:
        return None
    text = (f"The whole graph is loaded in Amazon Neptune: {_num(loaded['nodes'])} nodes and {_num(loaded['links'])} links, "
            "equal to the download.")
    split = layer_split(roots, loaded["links"])
    if split:
        text += (f" The {_num(loaded['links'])} links are the {_num(split[0])} kept links of the pruned graph plus the "
                 f"{_num(split[1])} links of the trial layer.")
    pinned = [p for p in lp.get("pinned") or [] if isinstance(p, dict)]
    if pinned:
        k = sum(1 for p in pinned if p.get("digest_equal") is True)
        text += f" {k} of {len(pinned)} pinned question{'' if len(pinned) == 1 else 's'} return{'s' if len(pinned) == 1 else ''} the same evidence there."
    demos = [a for a in lp.get("answers") or [] if isinstance(a, dict) and str(a.get("id", "")).startswith(DEMO_PREFIX)]
    if demos:                                # FIX2 F5 (G2-ES-03): the ready questions, measured live, as they are
        same = sum(1 for a in demos if _identical(a))
        text += f" Of the {len(demos)} ready questions, {same} return{'s' if same == 1 else ''} identical evidence links there"
        if same < len(demos):                        # computed: said only when some ready question differs
            text += ("; this site's answers come from a search index that holds more rows than the download, so the two "
                     "copies can return different links (each answer prints how many are the same)")
        text += "."
    return text


DEMO_PREFIX = "pharos_demo_"


def _identical(a):
    """Takes one 'answers' row of live_parity.json. Returns True when both answers hold the same, non-empty set of evidence links."""
    n = [a.get(k) for k in ("links_local", "links_neptune", "links_identical")]
    return all(isinstance(x, int) and not isinstance(x, bool) for x in n) and n[0] == n[1] == n[2] > 0


def layer_split(roots, links):
    """Takes the roots and the loaded link count. Returns (kept links of the pruned graph, trial-layer links) when they add up
    to `links`, else None. Read from the export manifest's kept_by_layer when a root has it, else from outputs/g3/summary.md
    (its edge count) and outputs/kg_edges.csv (the trial layer's edges, one row each after the header)."""
    man = _read_json(roots, MANIFEST)
    kb = man.get("kept_by_layer") if isinstance(man, dict) else None
    if isinstance(kb, dict) and kb:
        trial = kb.get(TRIAL_LAYER, 0)
        split = (sum(v for v in kb.values() if isinstance(v, int)) - trial, trial)
    else:
        g3 = trial = None
        for r in roots:
            sm, ke = Path(r) / "outputs" / "g3" / "summary.md", Path(r) / "outputs" / "kg_edges.csv"
            if g3 is None and sm.is_file():
                m = re.search(r"[\d,]+ nodes, ([\d,]+) edges", sm.read_text(encoding="utf-8"))
                g3 = int(m.group(1).replace(",", "")) if m else None
            if trial is None and ke.is_file():
                with ke.open(encoding="utf-8") as f:
                    trial = max(0, sum(1 for _ in f) - 1)
        if g3 is None or trial is None:
            return None
        split = (g3, trial)
    return split if split[0] + split[1] == links and split[1] > 0 else None


def via_cloudfront(headers):
    """Takes the request headers (any mapping with .get). Returns True when a Via header names CloudFront
    (CloudFront adds 'Via: 1.1 <id>.cloudfront.net (CloudFront)' to the request it sends to the origin)."""
    v = str((headers or {}).get("via") or "").lower()
    return "cloudfront" in v


def runs_on(model_hosted, state, via_cf):
    """Takes whether the model client is the hosted one, the Neptune state and whether the request came through CloudFront.
    Returns {models, neptune, cloudfront, aws, text}: the "Runs on" line with each part only when it is true for this server
    ("Models: NVIDIA NIM. Graph database: Amazon Neptune. Delivery: Amazon CloudFront. Hosted on AWS."). aws (and its part) is
    true when Neptune is configured or the request came through CloudFront (both exist only on the AWS deployment); text is ''
    when no part is true."""
    neptune = state not in (None, "", "not_configured")
    parts = dict(models=bool(model_hosted), neptune=neptune, cloudfront=bool(via_cf), aws=neptune or bool(via_cf))
    nep = ("Graph database: Amazon Neptune (the full graph; asleep, woken on request)." if state == "asleep"
           else "Graph database: Amazon Neptune.")             # FIX2 F5 (G2-U-02)
    words = [("models", "Models: NVIDIA NIM."), ("neptune", nep),
             ("cloudfront", "Delivery: Amazon CloudFront."), ("aws", "Hosted on AWS.")]
    return dict(parts, text=" ".join(w for k, w in words if parts[k]))


# --------------------------------------------------------------------------------------------------------------- the routes
def _data_roots():
    """The roots the app reads shipped files from: TMKG_PHAROS_ROOT (the data root) first, then this repo."""
    return _roots(os.environ.get("TMKG_PHAROS_ROOT") or ROOT, ROOT)


@router.get("/api/neptune/status")
def api_neptune_status():
    """The state of Amazon Neptune as the host last wrote it, the wake time in words and the loaded counts (from the shipped
    manifest). Never contacts Neptune, never counts against the ask limit."""
    return status(roots=_data_roots())


def cross_site(headers, allowed=None):
    """Takes the request headers and optionally the allowed host names. Returns True for a request another site's page sent:
    Sec-Fetch-Site 'cross-site', or an Origin whose host is neither the request's own Host nor one of the site's addresses
    (KNOWN_HOSTS plus TMKG_ALLOWED_ORIGINS, comma-separated host names). A request without an Origin (curl, a script) is not
    cross-site."""
    h = headers or {}
    if str(h.get("sec-fetch-site") or "").strip().lower() == "cross-site":
        return True
    origin = str(h.get("origin") or "").strip()
    if not origin:
        return False
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", origin.lower()).split("/")[0].split(":")[0]
    own = str(h.get("host") or "").lower().split(":")[0]
    extra = [x.strip().lower() for x in os.environ.get("TMKG_ALLOWED_ORIGINS", "").split(",") if x.strip()]
    ok = set(KNOWN_HOSTS) | set(extra) | ({own} if own else set()) | set(allowed or ())
    return host not in ok


def _host_app():
    """The running app module (pipelines/app.py, imported as 'app' or run as '__main__'), for its per-address ceiling; None in
    a test that mounts this router alone."""
    for name in ("app", "__main__", "pipelines.app"):
        m = sys.modules.get(name)
        if m is not None and hasattr(m, "ip_ceiling_allowed") and hasattr(m, "ip_limit"):
            return m
    return None


@router.post("/api/neptune/wake")
async def api_neptune_wake(request: Request):
    """Asks the host to start Amazon Neptune when it is asleep (no body needed). Returns {state, wake_minutes, wake_words,
    sentence}: 'waking' after a request (at most one file write per 60 s for the whole server), else the state unchanged.
    FIX2 F4 (G2-SEC-02): a request from another site's page is refused (403); a wake of a sleeping cluster counts under the
    per-address ceiling of the ask limit (the app's ip_ceiling_allowed, TMKG_ASK_LIMIT_PER_HOUR x 3) and, past it, is refused
    (429); at most wakes_per_day() requests are written per 24 h for the whole server, then 429 with WAKE_CAPPED. It is not a
    question, so it never counts against the visitor's own ask limit."""
    roots = _data_roots()
    words = dict(wake_minutes=wake_minutes(None, roots), wake_words=wake_words(None, roots))
    if cross_site(request.headers):
        return JSONResponse(status_code=403, content=dict(state=neptune_state(), detail=WAKE_CROSS_SITE, **words))
    host = _host_app()
    if host is not None and neptune_state() == "asleep":
        fwd, peer = request.headers.get("x-forwarded-for", ""), request.client.host if request.client else ""
        net, limit = host.ip_key(fwd, peer), host.ip_limit(host._env_int("TMKG_ASK_LIMIT_PER_HOUR"))
        ok, wait = host.ip_ceiling_allowed(net, time.time(), limit)
        if not ok:
            return JSONResponse(status_code=429, headers={"Retry-After": str(wait)},
                                content=dict(host.ip_ceiling_refusal(limit, wait), state="asleep", **words))
    st = request_wake()
    if st == "capped":
        return JSONResponse(status_code=429, content=dict(state="asleep", detail=WAKE_CAPPED, sentence=WAKE_CAPPED, **words))
    return dict(state=st, sentence=state_sentence(st, None, roots), **words)
