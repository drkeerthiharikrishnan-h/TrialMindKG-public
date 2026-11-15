"""
Frozen API responses ("snapshot") - makes every build repeatable and traceable.

Every HTTP request the pipeline makes goes through Snapshot.get() / get_text() / post();
non-HTTP queries (AACT database) go through Snapshot.call().
Each response is stored as one gzipped JSON file under data/snapshots/<source>/<key>.json.gz,
holding the exact request, the time it was fetched and the raw response body.
The file name (key) is a hash of the request, so the same request always maps to the same file.

Modes (environment variable TMKG_SNAPSHOT_MODE, or the `mode` argument):
  live     (default) use the stored response if there is one, otherwise fetch and store it
  offline  never touch the network; a request with no stored response raises MissingFromSnapshot
  refresh  fetch every request again and overwrite the stored response (= a new data version)

HTTP 400/404 are stored as body=None (the server's definitive answer).
Other failed requests (timeouts, 5xx) are NEVER stored, so a temporary outage cannot silently remove data from the graph.

Returned object (Response): body (parsed JSON), fetched_at (ISO time, UTC), key (file name stem),
source (folder name). Edges built from a response carry fetched_at as `retrieved` and key as
`snapshot_key`, so every statement can be traced back to the raw API answer.

Waiting (HttpBudget): a batch pipeline may wait minutes for a slow or rate-limited server (the default: 60 s per GET,
180 s per POST, 4 attempts, up to 12 rate-limit waits of 10-60 s). A web request must not: Snapshot.post(..., budget=
HttpBudget(...)) gives one call its own timeout, number of attempts and an absolute deadline, and a rate-limit answer
(HTTP 429) then fails at once instead of waiting. The pipelines never pass a budget, so their behaviour is unchanged.
"""
import gzip, hashlib, json, os, time, datetime
from dataclasses import dataclass
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data" / "snapshots"


class MissingFromSnapshot(RuntimeError):
    """Raised in offline mode when a request has no stored response."""


class DeadlineExceeded(TimeoutError):
    """Raised when a call with an HttpBudget has no time left (its deadline passed) before or between attempts."""


class RateLimited(RuntimeError):
    """Raised when the server answers HTTP 429 and the call's HttpBudget allows no rate-limit wait."""


@dataclass(frozen=True)
class HttpBudget:
    """How long ONE live request may take (used by the web app's model client; nim.web_budget()).
    timeout = seconds per attempt; attempts = tries in total (2 = one retry); rate_limit_waits = how many HTTP 429
    answers are waited out (0 = a 429 raises RateLimited at once); backoff = seconds slept between attempts;
    deadline = a time.monotonic() value after which nothing is sent any more (None = no deadline);
    store_optional = True: an answer that cannot be written to the snapshot folder is still returned."""
    timeout: float = 30.0
    attempts: int = 2
    rate_limit_waits: int = 0
    backoff: float = 1.0
    deadline: float = None
    store_optional: bool = True

    def remaining(self, now=None):
        """Seconds left until the deadline (None when there is none). Takes the current time.monotonic() (default: now)."""
        if self.deadline is None:
            return None
        return self.deadline - (time.monotonic() if now is None else now)


@dataclass(frozen=True)
class Response:
    body: object
    fetched_at: str
    key: str
    source: str
    cached: bool = False          # True when the answer was read from the stored snapshot, not fetched in this call


def request_key(method, url, params=None, body=None):
    """Stable 16-hex-character id for a request (same request -> same key, on any machine)."""
    canon = json.dumps({"m": method, "u": url, "p": params or {}, "b": body or {}},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def _takes_budget(fn):
    """True when fn (Snapshot._http or a stand-in put in its place) accepts the seventh argument, the HttpBudget."""
    import inspect
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return True
    return any(p.kind == p.VAR_POSITIONAL or p.name == "budget" for p in params) or \
        len([p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 7


class Snapshot:
    def __init__(self, directory=DEFAULT_DIR, mode=None, sleep=0.3, post_timeout=180):
        self.dir = Path(directory)
        self.mode = mode or os.environ.get("TMKG_SNAPSHOT_MODE", "live")
        if self.mode not in ("live", "offline", "refresh"):
            raise ValueError(f"unknown snapshot mode {self.mode!r}")
        self.sleep = sleep            # politeness delay between live requests (seconds)
        self.post_timeout = post_timeout   # seconds to wait for a POST answer (a long model reply can need more than 180)
        self.used = set()             # (source, key) of every response used in this run

    # ------------------------------------------------------------------ public
    def get(self, source, url, params=None, headers=None):
        """GET url with params. `source` is a short folder name, e.g. 'ctgov'. Returns Response.
        headers (e.g. an API key) are sent but never stored and never part of the request key."""
        return self._fetch(source, "GET", url, params=params, headers=headers)

    def get_text(self, source, url, params=None):
        """GET a non-JSON document (YAML, TSV, plain text). Returns Response with body = {"text": "..."}."""
        return self._fetch(source, "GET_TEXT", url, params=params)

    def call(self, source, request, fn):
        """Freeze the result of a non-HTTP query (e.g. an AACT database query): `request` (JSON-able) identifies
        it, fn() produces the JSON-able result on a miss. Same modes and storage as get()."""
        key = request_key("CALL", source, None, request)
        f = self.path(source, key)
        if f.exists() and self.mode != "refresh":
            rec = json.loads(gzip.decompress(f.read_bytes()))
            self.used.add((source, key))
            return Response(rec["body"], rec["fetched_at"], key, source)
        if self.mode == "offline":
            raise MissingFromSnapshot(f"{source} {request} (key {key}) not in {self.dir}")
        data = fn()
        fetched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(gzip.compress(json.dumps({"request": {"call": request}, "fetched_at": fetched_at, "body": data},
                                               sort_keys=True, indent=0, default=str).encode(), mtime=0))
        self.used.add((source, key))
        return Response(data, fetched_at, key, source)

    def post(self, source, url, body, headers=None, store_client_errors=True, budget=None):
        """POST a JSON body (GraphQL, NIM). Returns Response.
        headers (e.g. an API key) are sent but never stored and never part of the request key.
        store_client_errors=False: an HTTP 400/404 raises instead of being stored (used for model calls).
        budget: an HttpBudget for a live call that must answer quickly (the web app); None = the batch waits.
        A stored response is returned whatever the budget says (no network is needed for it)."""
        return self._fetch(source, "POST", url, body=body, headers=headers, store_client_errors=store_client_errors,
                           budget=budget)

    def download(self, url, dest):
        """Save a document (PDF, HTML, XML) to `dest`. Never raises: returns
        dict(url, path, status, bytes, sha256, fetched_at). Existing files are kept (except in refresh mode),
        offline mode never downloads. Used for the raw/ review folder, not for building the graph."""
        dest = Path(dest)
        rec = dict(url=url, path=str(dest), status="", bytes=None, sha256=None, fetched_at=None)
        if dest.exists() and self.mode != "refresh":
            data = dest.read_bytes()
            return {**rec, "status": "ok (already downloaded)", "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()}
        if self.mode == "offline":
            return {**rec, "status": "not downloaded (offline mode)"}
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0 (TrialMindKG document review)"})
        except Exception as e:
            return {**rec, "status": f"not reachable from this computer ({type(e).__name__})"}
        if r.status_code != 200:
            return {**rec, "status": f"HTTP {r.status_code} (may need a browser or subscription)"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        time.sleep(self.sleep)
        return {**rec, "status": "ok", "bytes": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest(),
                "fetched_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def unused(self):
        """Stored responses this run did not use (e.g. requests the code no longer makes)."""
        return sorted(f for f in self.dir.glob("*/*.json.gz") if (f.parent.name, f.name[:-8]) not in self.used)

    def prune(self):
        """Delete stored responses not used by this run. Returns how many were deleted."""
        old = self.unused()
        for f in old:
            f.unlink()
        return len(old)

    def path(self, source, key):
        return self.dir / source / f"{key}.json.gz"

    # ----------------------------------------------------------------- private
    def _fetch(self, source, method, url, params=None, body=None, headers=None, store_client_errors=True, budget=None):
        key = request_key(method, url, params, body)
        f = self.path(source, key)
        if f.exists() and self.mode != "refresh":
            rec = json.loads(gzip.decompress(f.read_bytes()))
            self.used.add((source, key))
            return Response(rec["body"], rec["fetched_at"], key, source, cached=True)
        if self.mode == "offline":
            raise MissingFromSnapshot(f"{method} {url} {params or body} (key {key}) not in {self.dir}")
        if budget is None or not _takes_budget(self._http):
            # the batch call, exactly as before; also for an _http replaced by a stand-in with the older six arguments
            data = self._http(method, url, params, body, headers, store_client_errors)
        else:
            data = self._http(method, url, params, body, headers, store_client_errors, budget)
        fetched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rec = {"request": {"method": method, "url": url, "params": params, "body": body},
               "fetched_at": fetched_at, "body": data}
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            # mtime=0 -> identical bytes for identical content (git sees no change)
            f.write_bytes(gzip.compress(json.dumps(rec, sort_keys=True, indent=0).encode(), mtime=0))
        except OSError:               # a read-only or full disk: a pipeline must stop, a web answer is still an answer
            if budget is None or not budget.store_optional:
                raise
        self.used.add((source, key))
        return Response(data, fetched_at, key, source)

    def _http(self, method, url, params, body, headers=None, store_client_errors=True, budget=None):
        """One live request with retries. Without a budget: the batch waits (60 s per GET, post_timeout (default 180 s) per POST, 4 attempts, up
        to 12 rate-limit waits). With an HttpBudget: its timeout (never longer than the time left to its deadline), its
        number of attempts, no politeness delay, DeadlineExceeded once the deadline has passed and RateLimited on an
        HTTP 429 that may not be waited out. Returns the parsed body; raises RuntimeError after the last attempt."""
        attempts = 4 if budget is None else max(1, int(budget.attempts))
        waits = 12 if budget is None else max(0, int(budget.rate_limit_waits))
        last, limited, attempt = None, 0, 0
        while attempt < attempts:
            timeout = 60 if method in ("GET", "GET_TEXT") else self.post_timeout
            if budget is not None:
                left = budget.remaining()
                if left is not None and left <= 0:
                    raise DeadlineExceeded(f"{method} {url}: no time left for this request"
                                           + (f" (last error: {last})" if last else ""))
                timeout = budget.timeout if left is None else max(0.1, min(budget.timeout, left))
            try:
                if method in ("GET", "GET_TEXT"):
                    r = requests.get(url, params=params, timeout=timeout, headers=headers)
                else:
                    r = requests.post(url, json=body, timeout=timeout, headers=headers)
                if r.status_code == 200:
                    if budget is None:
                        time.sleep(self.sleep)
                    return {"text": r.text} if method == "GET_TEXT" else r.json()
                if r.status_code in (400, 404) and store_client_errors:
                    return None       # definitive "bad request" / "no such record": a valid, storable answer
                if r.status_code in (400, 401, 403, 404, 422):
                    raise PermissionError(f"HTTP {r.status_code}: {r.text[:300]}")   # not retried, never stored
                last = f"HTTP {r.status_code}"
                if r.status_code == 429:
                    if limited < waits:                       # rate limited: wait (10, 20 .. 60 s) - not a failure
                        limited += 1
                        time.sleep(min(60, 10 * limited))
                        continue
                    if budget is not None:                    # a web request does not queue behind a rate limit
                        raise RateLimited(f"HTTP 429 from {url}: rate limited, not waited out")
            except (PermissionError, RateLimited):
                raise
            except Exception as e:     # network error -> retry
                last = repr(e)
            attempt += 1
            if budget is None:
                time.sleep(2 ** (attempt - 1))
            elif attempt < attempts:
                left = budget.remaining()
                time.sleep(max(0.0, budget.backoff if left is None else min(budget.backoff, left)))
        raise RuntimeError(f"{method} {url} {params or body} failed after retries: {last}")
