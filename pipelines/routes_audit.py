"""
"Independent check" on one edge: the server part.

    GET /api/pharos/audit/{edge_id}

A second agent, the auditor, checks one link of the graph against sources the pruner never reads (IntAct, Complex
Portal, Reactome, Europe PMC, Open Targets, QuickGO, ChEMBL, IMPC, UniProt, WikiPathways, STRING; the collectors are
in pipelines/edge_evidence.py) and says whether the link is supported, unsupported, or cannot be decided.

Order of the answer
1. A STORED record, when the edge was audited ahead of time (the audit sample and every edge a stored answer cites):
   the file <data root>/data/audit/edges/<edge id>.json written by scripts/audit_spotcheck.py publish. No outbound
   call, no model call, not counted.
2. Otherwise an ON-DEMAND check: the collectors and ONE model judge under a total time budget (BUDGET_S, 10 seconds),
   cached by edge id for as long as the process runs, and counted toward the visitor's hourly question limit
   and the daily limit for all visitors together (the same budgets as the ask endpoints; a stored or cached answer
   is never counted). A check that gave no
   judgement is not repeated for a minute: the edge answers "unavailable" meanwhile, uncounted.
3. On ANY failure (the edge is not in the index, no hosted model, a source or the model too slow, a reply that
   cannot be used, too many checks at once) the plain sentence UNAVAILABLE, with HTTP 200 and available: false.
   The only other status is 429, when the visitor's hourly limit or the daily limit for all visitors is used up
   (same shape as the ask endpoints).

Blindness. The auditor never receives the pruner's verdict, confidence or reasons: blind_edge() reads only the
named columns of the edge, edge_evidence.build_dossier() copies only BLIND_FIELDS into the dossier, and the
payload is built from the dossier and the judgement alone. tests/test_routes_audit.py checks it with marker values.

PAYLOAD (available: true)
  edge_id      the edge asked for
  available    true
  origin       'stored' (audited ahead of time) or 'on_demand' (checked now, or earlier in this process)
  checked      the date of the check (YYYY-MM-DD)
  statement    "checked by an auditing agent against sources the pruner does not read"
  claim_kind   what kind of claim it is, in plain words ('' when unknown)
  sources      one entry per source consulted, in the order the auditor read them:
               {source, status ('answered' | 'failed' | 'not found' | 'not applicable'), url (the source's own page
               for this edge, '' when there is none), returned (one line: what the source returned),
               items: [{id, text, url, kind ('evidence' | 'context')}]}
  judgement    {label ('supported' | 'unsupported' | 'cannot tell'), rationale (at most two sentences),
               raters (how many raters judged it: three for a stored record, one on demand),
               raters_agreeing, items (the ids of the items the judgement rests on)}
  complete     true when every source answered (none 'failed')
  note         '' or one computed sentence: how many sources did not answer
  raters_note  '' or one sentence under every judgement: how often the automated raters agreed with the scientist's
               blind labels, read from outputs/pharos/spotcheck_blind_result.md through numbers_page (raters_note())
PAYLOAD (available: false)
  {edge_id, available: false, message: UNAVAILABLE}

Functions (each says what it takes and returns)
- stat_string(rec), references(rec), blind_edge(explained)   the verdict-blind columns of an edge
- safe_edge_id(edge_id), load_stored(edge_id, folders)       a stored record, or None
- public_payload(record, edge_id, origin), unavailable(edge_id)   what the page receives
- BudgetFetcher, OneShotPost, judge_client(nim, deadline)    outbound calls that respect the time budget
- plain_rationale(text)                                      the rationale the page shows (no technical error text)
- Checker                                                    the on-demand check: budget, cache, one run per edge
- make_router(host, checker=None)                            the FastAPI router (host = the app module)

The page block that shows this is a separate change.
"""
import copy
import datetime
import json
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from types import SimpleNamespace

import requests

try:                                    # run from pipelines/ (the app, the tests)
    import edge_evidence as ee
except ImportError:                     # imported as pipelines.routes_audit
    from pipelines import edge_evidence as ee

UNAVAILABLE = "independent check unavailable right now"
STORED_REL = Path("data") / "audit" / "edges"
BUDGET_S = 10.0              # total time of one on-demand check, collectors and judge together
COLLECT_S = 6.5              # the collectors' part of it; what is left goes to the judge
MIN_JUDGE_S = 1.0            # with less than this left, the judge is not called
CALL_TIMEOUT_S = 4.0         # one outbound call of a collector, at most
WORKERS = 4                  # on-demand checks running at the same time
CACHE_MAX = 2000             # on-demand results kept in memory
PARTIAL_TTL_S = 300.0        # a result with a source that did not answer is kept this long, then checked again
FAILED_TTL_S = 60.0          # after a check that gave no judgement, the edge answers "unavailable" this long without a new check
CONNECT_S = 2.0              # opening a connection, at most (a call can overshoot its deadline by this much)
PER_MINUTE = 8               # on-demand checks started per minute, all visitors together (TMKG_AUDIT_PER_MINUTE; 0 = no limit)
CLAIM_KINDS = {"protein_protein": "physical interaction of two proteins", "gene_disease": "gene-disease association",
               "gene_ontology": "Gene Ontology annotation of a protein", "pathway": "pathway membership",
               "ligand": "compound acting on a protein", "phenotype": "phenotype of a gene knockout",
               "gene_product": "gene and its protein product", "other": "relation between two entities"}
STATUSES = ("answered", "failed", "not found", "not applicable")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")

# The statistic columns of an edge, as the blind spot-check sheet shows them. Kept equal to STAT_COLS, stat_string and
# references of scripts/make_spotcheck_blinded.py (the app image holds pipelines/ and not scripts/, so they are
# repeated here; tests/test_routes_audit.py compares the two).
STAT_COLS = ["activity", "disgenet_score", "jensenlab_zscore", "jensenlab_confidence", "monarch_s2o",
             "expression_atlas_log2fc", "mean_rank_score", "median_p_value", "median_odds_ratio", "p_value",
             "effect_size", "study_count", "snp_count", "stringdb_score", "bioplex_probability", "tinx_importance",
             "tinx_disease_novelty", "evidence_code", "action"]


# ------------------------------------------------------------------------------------------------ the blind edge
def stat_string(rec):
    """Takes one edge as a dict. Returns 'name=value; ...' for its non-empty statistic columns (activity carries its
    type; n_records is added when it is more than 1)."""
    parts = []
    for c in STAT_COLS:
        v = str(rec.get(c, "") or "").strip()
        if not v:
            continue
        if c == "activity" and str(rec.get("activity_type", "") or "").strip():
            v = f"{v} ({rec['activity_type']})"
        parts.append(f"{c}={v}")
    n = str(rec.get("n_records", "") or "").strip()
    if n and n != "1":
        parts.append(f"n_records={n}")
    return "; ".join(parts)


def references(rec, k=5):
    """Takes one edge as a dict. Returns its first k PubMed ids as 'PMID:x | PMID:y (+m more)'; '' when it has none."""
    p = [x for x in str(rec.get("pmids", "") or "").split("|") if x.strip()]
    s = " | ".join(f"PMID:{x.strip()}" for x in p[:k])
    return s + (f" (+{len(p) - k} more)" if len(p) > k else "")


def blind_edge(explained):
    """Takes the graph's explain_edge() record of one edge. Returns the verdict-blind columns the auditor may read
    (edge_evidence.BLIND_FIELDS), or None when the graph does not hold the edge (a clinical-trial registry link is
    not in the card index). Only the subject, object, provenance and properties parts are read: the verdict, its
    confidence and its reasons are never looked at. Same result as scripts/audit_spotcheck.py blind_row()."""
    if not isinstance(explained, dict) or not explained.get("provenance"):
        return None
    pv, props = explained["provenance"], explained.get("properties") or {}
    sub, obj = explained.get("subject") or {}, explained.get("object") or {}
    pks = pv.get("primary_knowledge_source") or ""
    return {"subject_name": sub.get("symbol") or sub.get("name") or props.get("ligand_name") or props.get("drug_name") or "",
            "subject_id": pv.get("subject") or sub.get("id") or "",
            "predicate": str(pv.get("predicate") or "").replace("biolink:", ""),
            "object_name": obj.get("symbol") or obj.get("name") or "", "object_id": pv.get("object") or obj.get("id") or "",
            "source": f"{pv.get('source')} ({pks})" if pks else str(pv.get("source") or ""),
            "knowledge_level": pv.get("knowledge_level") or "",
            "source_statistic": stat_string({k: props.get(k) for k in STAT_COLS + ["activity_type"]} | {"n_records": pv.get("n_records")}),
            "references": references({"pmids": props.get("pmids")})}


# ------------------------------------------------------------------------------------------------ stored records
def safe_edge_id(edge_id):
    """Takes an edge id from the URL. Returns True when it can be used as a file name and a cache key: letters,
    digits, '_', '.', ':', '-' only, at most 128 characters, no '..'."""
    return bool(_SAFE_ID.match(str(edge_id))) and ".." not in str(edge_id)


def valid_record(rec):
    """Takes a parsed record. Returns True when it has the shape of edge_evidence.audit_record(): a judgement with
    one of the three labels and a list of sources."""
    if not isinstance(rec, dict) or not isinstance(rec.get("judgement"), dict) or not isinstance(rec.get("sources"), list):
        return False
    return rec["judgement"].get("label") in ee.LABELS and all(isinstance(s, dict) and s.get("source") for s in rec["sources"])


def load_stored(edge_id, folders):
    """Takes an edge id and the folders that may hold stored records (first hit wins). Returns the record of
    <folder>/<edge id>.json, or None when there is none, the id is not a safe file name, or the file is not a
    readable record. Never raises."""
    if not safe_edge_id(edge_id):
        return None
    for folder in folders:
        try:
            folder = Path(folder).resolve()
            f = (folder / f"{edge_id}.json").resolve()
            if f.parent != folder or not f.is_file():
                continue
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if valid_record(rec):
            return rec
    return None


# ------------------------------------------------------------------------------------------------ the payload
def _link(url):
    """Takes a URL from a record. Returns it when it is a web link, else ''."""
    url = str(url or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def two_sentences(text):
    """Takes a rationale. Returns its first two sentences."""
    return " ".join(ee.split_sentences(" ".join(str(text or "").split()))[:2])


_TECHNICAL = re.compile(r"https?://|\b\w*(?:Error|Exception|Timeout)\b|HTTPSConnectionPool|Traceback|time budget")
NO_DECISION = "The auditor could not reach a decision from what the sources returned."


def plain_rationale(text):
    """Takes a stored or model-written rationale. Returns its first two sentences; when it carries technical error
    text (an exception name, a URL, a connection message) the plain sentence NO_DECISION instead."""
    text = " ".join(str(text or "").split())
    return NO_DECISION if _TECHNICAL.search(text) else two_sentences(text)


def _count(value):
    """Takes a stored count. Returns it as a whole number, 0 when it is not one."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _returned(source):
    """Takes one source entry of a record. Returns the line the page shows for what it returned. A source that did
    not answer gets a plain sentence (its stored note holds the technical error)."""
    status = source.get("status")
    if status == "failed":
        note = str(source.get("returned") or "")
        partial = "only the claimed term was searched" in note          # Open Targets sentences: the narrower-term query failed
        return ("This source answered only in part: its search over more specific disease terms did not answer."
                if partial else "This source did not answer.")
    return " ".join(str(source.get("returned") or "").split())


def public_payload(record, edge_id, origin):
    """Takes a record (edge_evidence.audit_record shape), the edge id asked for and 'stored' or 'on_demand'. Returns
    the PAYLOAD of the module docstring. Only the listed fields are copied, so nothing else in a record can reach
    the page."""
    sources = []
    for s in record.get("sources") or []:
        items = [{"id": str(it.get("id") or ""), "text": " ".join(str(it.get("text") or "").split()), "url": _link(it.get("url")),
                  "kind": "context" if it.get("kind") == "context" else "evidence"}
                 for it in (s.get("items") if isinstance(s.get("items"), list) else []) if isinstance(it, dict)]
        status = s.get("status") if s.get("status") in STATUSES else "failed"
        sources.append({"source": str(s.get("source")), "status": status, "url": _link(s.get("url")),
                        "returned": _returned({**s, "status": status}), "items": items})
    j = record["judgement"]
    known = {it["id"] for s in sources for it in s["items"]}
    failed = sum(s["status"] == "failed" for s in sources)
    answered = sum(s["status"] == "answered" for s in sources)
    note = ""
    if sources and failed == len(sources):
        note = f"None of the {len(sources)} sources answered in full."
    elif failed and not answered:
        note = (f"{failed} of {len(sources)} sources did not answer in full, and the "
                f"{'other' if len(sources) - failed == 1 else 'others'} had nothing to look up for this link.")
    elif failed:
        rest = len(sources) - failed
        who = (("other" if rest == 1 else "others") if answered == rest
               else ("one that had something to look up" if answered == 1 else f"{answered} that had something to look up"))
        note = f"{failed} of {len(sources)} sources did not answer in full; the judgement rests on what the {who} returned."
    return {"edge_id": edge_id, "available": True, "origin": origin, "checked": str(record.get("checked") or ""),
            "statement": ee.STATEMENT, "claim_kind": CLAIM_KINDS.get(record.get("group"), ""), "sources": sources,
            "judgement": {"label": j["label"], "rationale": plain_rationale(j.get("rationale")),
                          "raters": _count(j.get("raters")), "raters_agreeing": _count(j.get("raters_agreeing")),
                          "items": [i for i in (str(x) for x in (j.get("items") if isinstance(j.get("items"), list) else [])) if i in known]},
            "complete": failed == 0, "note": note}


def raters_note(roots):
    """Takes the folders to look in (data root, image root). Returns the sentence shown under every independent-check
    judgement (FIX2 F3, G2-AC-06): "Automated raters. On the 39 links a scientist rated blind, they agreed with her on 7.",
    both numbers read from the spot-check result file by numbers_page.spot_check_counts; '' when no root holds the file
    or the file has no owner-agreement line."""
    try:
        import numbers_page as npg
    except ImportError:
        from pipelines import numbers_page as npg
    for root in roots:
        if root is None:
            continue
        f = Path(root) / npg.SPOTCHECK_RESULT
        if f.is_file():
            c = npg.spot_check_counts(f.read_text(encoding="utf-8"))
            if "owner_agree" in c:
                return (f"Automated raters. On the {c['owner_rows']} links a scientist rated blind, they agreed with her "
                        f"on {c['owner_agree']}.")
    return ""


def unavailable(edge_id):
    """Takes an edge id. Returns the payload of a check that could not be made."""
    return {"edge_id": edge_id, "available": False, "message": UNAVAILABLE}


# ------------------------------------------------------------------------------------------------ outbound calls
class BudgetFetcher(ee.FastFetcher):
    """edge_evidence.FastFetcher (no cache, no retry; same three methods, same return values) whose calls end at
    the deadline: before every call the timeout is cut to the time that is left (at most CALL_TIMEOUT_S), the answer
    is read in pieces and the reading stops at the deadline (a plain timeout only limits the wait between two
    pieces, so a slow answer could run on), and after the deadline every call raises TimeoutError. A collector
    turns that into a 'failed' source."""

    def __init__(self, deadline, timeout=CALL_TIMEOUT_S):
        super().__init__(timeout=timeout, deadline=deadline)
        self.max_timeout = timeout

    def _check(self):
        super()._check()
        self.timeout = max(0.05, min(self.max_timeout, self.deadline - time.monotonic()))

    def _request(self, method, url, text=False, **kwargs):
        """One request. Returns the parsed JSON body (the text with text=True), None for HTTP 400 / 404; raises on
        any other failure and when the deadline passes before the whole answer has arrived."""
        self._check()
        with requests.request(method, url, timeout=(min(CONNECT_S, self.timeout), self.timeout), stream=True, **kwargs) as r:
            if r.status_code in (400, 404):
                return None
            r.raise_for_status()
            parts = []
            for part in r.iter_content(65536):
                parts.append(part)
                if time.monotonic() >= self.deadline:
                    raise TimeoutError("time budget for the independent check is used up")
            body = b"".join(parts)
            return body.decode(r.encoding or "utf-8", errors="replace") if text else json.loads(body)

    def json(self, source, url, params=None, headers=None):
        return self._request("GET", url, params=params, headers=headers)

    def text(self, source, url, params=None):
        return self._request("GET", url, text=True, params=params)

    def post(self, source, url, body):
        return self._request("POST", url, json=body)


class OneShotPost:
    """Stands in for the model client's snapshot during an on-demand check: ONE request, no retry, no waiting on a
    rate limit, nothing stored, and a timeout that ends at the deadline (snapshot.Snapshot retries for minutes,
    which a 10-second budget cannot carry). post() returns an object with body / key / fetched_at / cached, as
    Snapshot.post does, and raises on any failure."""

    def __init__(self, deadline):
        self.deadline = deadline

    def post(self, source, url, body, headers=None, store_client_errors=True):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("time budget for the independent check is used up")
        r = requests.post(url, json=body, headers=headers, timeout=(min(CONNECT_S, left), left))
        r.raise_for_status()
        return SimpleNamespace(body=r.json(), key="", source=source, cached=False,
                               fetched_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def judge_client(nim, deadline):
    """Takes the app's model client and a deadline (time.monotonic() value). Returns a copy for ONE judge call: its
    own role table (edge_evidence.judge adds a role), no stand-in answer and no second model when the call fails
    (a failed judge is 'unavailable', never an invented judgement), and OneShotPost for the request. The app's
    client is not changed."""
    c = copy.copy(nim)
    c.cfg = {**nim.cfg, "roles": dict(nim.cfg.get("roles") or {})}
    c.fallback, c.calls, c.fallbacks = "none", [], []
    c.snap = OneShotPost(deadline)
    return c


def judge_model(nim):
    """Takes the model client. Returns the model id of the on-demand judge: TMKG_AUDIT_MODEL when set, else the
    faster of the two pre-registered judges (config key spotcheck_raters.judge_b), else judge_a; '' when the
    configuration names none."""
    cfg = (getattr(nim, "cfg", None) or {}).get("spotcheck_raters") or {}
    return os.environ.get("TMKG_AUDIT_MODEL") or cfg.get("judge_b") or cfg.get("judge_a") or ""


# ------------------------------------------------------------------------------------------------ the on-demand check
class Unavailable(Exception):
    """The check could not be made; the reason is for the server log only."""


class Checker:
    """The on-demand check with its time budget, its cache and one run per edge at a time.

    build(edge, fetch, lookups, edge_id=...) -> dossier and judge(dossier, nim, model) -> judgement default to
    edge_evidence.build_dossier and edge_evidence.judge; the tests pass fakes. lookups: the local reference tables
    (edge_evidence.Lookups), loaded on first use when None.

    How a request uses it: cached() / failed_recently() first; then reserve() says 'join' (a check of this edge is
    running: wait for it), 'go' (this request may start one: the slot is taken in the same step, so two requests
    can never both start, or both be counted for, the same check) or 'busy'; after 'go' either start() or release();
    then wait(). At most `workers` checks run at once and none waits in a queue, so a check starts the moment it is
    reserved and the request's wait and the check's budget cover the same seconds."""

    def __init__(self, build=None, judge=None, lookups=None, budget=BUDGET_S, collect=COLLECT_S, min_judge=MIN_JUDGE_S,
                 workers=WORKERS, per_minute=None, cache_max=CACHE_MAX, partial_ttl=PARTIAL_TTL_S, failed_ttl=FAILED_TTL_S):
        self.build, self.judge = build or ee.build_dossier, judge or ee.judge
        self.budget, self.collect, self.min_judge = float(budget), min(float(collect), float(budget)), float(min_judge)
        self.per_minute = per_minute
        self.cache_max, self.partial_ttl, self.failed_ttl = cache_max, partial_ttl, failed_ttl
        self._lookups = lookups
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="edge-audit")
        self._max_running = workers
        self._lock, self._lookups_lock = threading.Lock(), threading.Lock()
        self._cache, self._failed, self._running, self._starts = {}, {}, {}, []

    # ---- pieces
    def lookups(self):
        """The local reference tables, read once per process (about a second; not part of a check's budget)."""
        with self._lookups_lock:
            if self._lookups is None:
                self._lookups = ee.Lookups()
            return self._lookups

    def cached(self, edge_id, now=None):
        """Takes an edge id. Returns its cached record, or None (never checked, or a partial result that is too old)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hit = self._cache.get(edge_id)
            if hit is None:
                return None
            record, expires = hit
            if expires is not None and now >= expires:
                del self._cache[edge_id]
                return None
            return record

    def failed_recently(self, edge_id, now=None):
        """Takes an edge id. Returns True when a check of it ended without a judgement less than failed_ttl seconds
        ago: the answer is "unavailable" again, without a new check and without counting a question."""
        now = time.monotonic() if now is None else now
        with self._lock:
            until = self._failed.get(edge_id)
            if until is not None and now >= until:
                del self._failed[edge_id]
                until = None
            return until is not None

    def _store(self, edge_id, record, complete):
        with self._lock:
            if len(self._cache) >= self.cache_max and edge_id not in self._cache:
                self._cache.pop(next(iter(self._cache)))                  # the oldest entry leaves
            self._cache[edge_id] = (record, None if complete else time.monotonic() + self.partial_ttl)
            self._failed.pop(edge_id, None)

    def _store_failure(self, edge_id):
        with self._lock:
            if len(self._failed) >= self.cache_max:
                self._failed.pop(next(iter(self._failed)))
            self._failed[edge_id] = time.monotonic() + self.failed_ttl

    def minute_limit(self):
        if self.per_minute is not None:
            return int(self.per_minute)
        return int(os.environ.get("TMKG_AUDIT_PER_MINUTE", str(PER_MINUTE)) or 0)

    def needs_model(self, edge):
        """Takes a blind edge. Returns False for the one kind of claim that is decided from the UniProt record alone."""
        return ee.edge_group(edge) != "gene_product"

    # ---- one run
    def run(self, edge_id, edge, nim, model, start=None):
        """One check, start to end, inside the budget (a call that is opening a connection at the deadline can
        overshoot by CONNECT_S). Takes the edge id, the blind edge, the model client and the judge's model id.
        Returns (record, complete): the record is edge_evidence.audit_record(dossier, [judgement]); complete is
        False when a source did not answer. Raises Unavailable (or the judge's own error) when no judgement can be
        given."""
        start = time.monotonic() if start is None else start
        end = start + self.budget
        blind = {k: edge.get(k, "") for k in ee.BLIND_FIELDS}             # nothing else can reach a collector or the judge
        dossier = self.build(blind, BudgetFetcher(deadline=min(end, start + self.collect)), self.lookups(), edge_id=edge_id)
        sources = dossier.get("sources") or []
        if not any(s.get("status") == "answered" for s in sources):     # failed, not found or not applicable only:
            raise Unavailable("no source answered")                         # nothing to judge, the judge is not called
        if ee.deterministic_label(dossier) is None:
            if nim is None or not model:
                raise Unavailable("no hosted model for the judge")
            if end - time.monotonic() < self.min_judge:
                raise Unavailable("no time left for the judge")
            nim = judge_client(nim, end)
        judgement = self.judge(dossier, nim, model)
        if not isinstance(judgement, dict) or judgement.get("label") not in ee.LABELS:
            raise Unavailable("the judge gave no label")
        if judgement.get("problems") or not judgement.get("label_raw", judgement["label"]):
            raise Unavailable("the judge's reply did not pass the citation checks: " + "; ".join(judgement.get("problems") or []))
        record = ee.audit_record(dict(dossier, edge_id=edge_id), [judgement])
        return record, not any(s.get("status") == "failed" for s in sources)

    # ---- one run per edge
    def reserve(self, edge_id, now=None):
        """Takes an edge id. Returns (status, ticket). 'join': a check of this edge is running, ticket waits for it.
        'go': the caller holds the slot for a new check (counted in the per-minute cap) and must call start() or
        release(). 'busy': too many checks are running or were started in the last minute; ticket is None."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if edge_id in self._running:
                return "join", self._running[edge_id]
            self._starts = [t for t in self._starts if now - t < 60.0]
            limit = self.minute_limit()
            if len(self._running) >= self._max_running or (limit > 0 and len(self._starts) >= limit):
                return "busy", None
            ticket = self._running[edge_id] = Future()
            ticket.started_at = now
            self._starts.append(now)
            return "go", ticket

    def release(self, edge_id, ticket, why="the check was not started"):
        """Gives back a slot reserved with 'go' that will not be used (the visitor's limit refused it, or an error
        came first). Requests that joined meanwhile get Unavailable. The per-minute cap forgets it."""
        with self._lock:
            if self._running.get(edge_id) is ticket:
                del self._running[edge_id]
            if getattr(ticket, "started_at", None) in self._starts:
                self._starts.remove(ticket.started_at)
        if not ticket.done():
            ticket.set_exception(Unavailable(why))

    def start(self, edge_id, ticket, edge, nim, model):
        """Starts the check a 'go' reserved. Takes the ticket, the blind edge, the model client and the judge's
        model id. The result (or the error) arrives on the ticket; a result is cached, a failure is remembered for
        failed_ttl seconds."""
        def job():
            try:
                record, complete = self.run(edge_id, edge, nim, model)
                self._store(edge_id, record, complete)
                ticket.set_result(record)
            except BaseException as err:                                  # the waiters get the error; nothing is cached
                self._store_failure(edge_id)
                ticket.set_exception(err)
            finally:
                with self._lock:
                    if self._running.get(edge_id) is ticket:
                        del self._running[edge_id]
        try:
            self._pool.submit(job)
        except BaseException:
            self.release(edge_id, ticket, "the check could not be started")
            raise

    def wait(self, ticket):
        """Takes a ticket. Returns the record, waiting at most the budget; raises TimeoutError when the check is not
        done by then (it still ends by itself and its result is cached for the next request) and the check's own
        error when it failed."""
        try:
            return ticket.result(timeout=self.budget)
        except FutureTimeout:
            raise TimeoutError("the independent check did not finish inside its time budget") from None

    def check(self, edge_id, edge, nim, model):
        """reserve + start + wait in one call, for callers without a visitor limit. Returns the record; raises
        Unavailable when busy."""
        self.lookups()                                                    # read before the clock starts
        status, ticket = self.reserve(edge_id)
        if status == "busy":
            raise Unavailable("too many checks running or started in the last minute")
        if status == "go":
            self.start(edge_id, ticket, edge, nim, model)
        return self.wait(ticket)


# ------------------------------------------------------------------------------------------------ the router
def make_router(host, checker=None):
    """Takes the app module (pipelines/app.py: answering_graph(), state(), _data_root(), visitor_key(),
    ask_allowed(), ask_remaining(), day_allowed() when it has one, nan_safe(), ROOT) and optionally a Checker (the tests pass one with fake collectors and a
    fake judge). Returns a fastapi.APIRouter with GET /api/pharos/audit/{edge_id}."""
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter()
    chk = checker or Checker()
    router.checker = chk

    def folders():
        out = []
        for root in (host._data_root(), getattr(host, "ROOT", None)):
            if root is not None and Path(root) / STORED_REL not in out:
                out.append(Path(root) / STORED_REL)
        return out

    def reply(payload, status_code=200, headers=None):
        if isinstance(payload, dict) and payload.get("available"):
            payload = dict(payload, raters_note=raters_note([host._data_root(), getattr(host, "ROOT", None)]))
        return JSONResponse(host.nan_safe(payload), status_code=status_code, headers=headers or None)

    def log(edge_id, why):
        print(f"[independent check] {edge_id}: {why}", flush=True)       # the server log; never sent to the page

    @router.get("/api/pharos/audit/{edge_id}")
    def api_pharos_audit(edge_id: str, request: Request):
        """The independent check of one edge: a stored record, else an on-demand check, else the sentence that it is
        unavailable (see the module docstring for the payload)."""
        if not safe_edge_id(edge_id):
            return reply(unavailable(edge_id[:128]))
        headers, ticket, started = {}, None, False
        try:
            stored = load_stored(edge_id, folders())
            if stored is not None:
                return reply(public_payload(stored, edge_id, "stored"))
            hit = chk.cached(edge_id)
            if hit is not None:
                return reply(public_payload(hit, edge_id, "on_demand"))
            if chk.failed_recently(edge_id):
                return reply(unavailable(edge_id))
            # the explained edge also carries the pruner's verdict; blind_edge() reads only the blind columns of it
            edge = blind_edge(host.answering_graph().explain_edge(edge_id))
            if edge is None:
                raise Unavailable("the graph that answers does not hold this edge")
            nim = host.state()[1]
            hosted = getattr(nim, "backend", "") == "nim"
            model = judge_model(nim) if hosted else ""
            if chk.needs_model(edge) and not model:
                raise Unavailable("no hosted model for the judge")
            chk.lookups()                                                 # read before the clock starts
            status, ticket = chk.reserve(edge_id)
            if status == "busy":
                raise Unavailable("too many checks running or started in the last minute")
            if status == "go":                                            # a new check counts as one question of this visitor
                limit = int(os.environ.get("TMKG_ASK_LIMIT_PER_HOUR", "0") or 0)
                visitor = host.visitor_key(request.cookies, request.headers.get("x-forwarded-for", ""),
                                           request.client.host if request.client else "")
                now = time.time()
                per_day = int(os.environ.get("TMKG_ASK_LIMIT_PER_DAY", "0") or 0)
                day_allowed = getattr(host, "day_allowed", None)          # the cap for all visitors together, when the app has one
                if day_allowed is not None:
                    open_today, day_wait = day_allowed(now, per_day, record=False)
                    if not open_today:
                        chk.release(edge_id, ticket, "today's questions for all visitors are used up")
                        started = True                                    # nothing left to give back
                        return reply({"detail": f"This demo runs at most {per_day} questions and independent checks a day for "
                                                "all visitors together, and today's are used up. Please try again in about "
                                                f"{max(1, day_wait // 3600)} hour{'s' if day_wait >= 7200 else ''}. Checks made "
                                                "ahead of time, cards, search and the tested questions still work.",
                                      "remaining": 0, "limit": per_day, "retry_after": day_wait, "scope": "day",
                                      "edge_id": edge_id, "available": False},
                                     status_code=429, headers={"Retry-After": str(day_wait), "X-Ask-Remaining": "0"})
                ceiling = getattr(host, "ip_ceiling_allowed", None)       # FIX1 F3 (G1-SEC-01): the per-address ceiling
                if ceiling is not None:
                    net = host.ip_key(request.headers.get("x-forwarded-for", ""), request.client.host if request.client else "")
                    net_limit = host.ip_limit(limit)
                    net_ok, net_wait = ceiling(net, now, net_limit, record=False)
                    if not net_ok:
                        chk.release(edge_id, ticket, "the address's hourly ceiling is used up")
                        started = True                                    # nothing left to give back
                        return reply(dict(host.ip_ceiling_refusal(net_limit, net_wait), remaining=0, edge_id=edge_id,
                                          available=False),
                                     status_code=429, headers={"Retry-After": str(net_wait), "X-Ask-Remaining": "0"})
                ok, wait = host.ask_allowed(visitor, now, limit)
                if ok and day_allowed is not None:
                    day_allowed(now, per_day)                             # count it for the day
                if ok and ceiling is not None:
                    ceiling(net, now, net_limit)                          # and for the address
                if not ok:
                    chk.release(edge_id, ticket, "the visitor's hourly limit is used up")
                    started = True                                        # nothing left to give back
                    return reply({"detail": f"This demo runs at most {limit} questions and independent checks per visitor "
                                            f"per hour, to keep it available for everyone. Please try again in about "
                                            f"{max(1, wait // 60)} minutes. Checks made ahead of time, cards, search and "
                                            "the tested questions still work.",
                                  "remaining": 0, "limit": limit, "retry_after": wait, "edge_id": edge_id, "available": False},
                                 status_code=429, headers={"Retry-After": str(wait), "X-Ask-Remaining": "0"})
                left = host.ask_remaining(visitor, now, limit)
                if left is not None:
                    headers["X-Ask-Remaining"] = str(left)
                chk.start(edge_id, ticket, edge, nim if hosted else None, model)
            started = True
            return reply(public_payload(chk.wait(ticket), edge_id, "on_demand"), headers=headers)
        except Exception as err:                                          # every failure is the same plain sentence
            if ticket is not None and not started:
                chk.release(edge_id, ticket)
            log(edge_id, f"{type(err).__name__}: {str(err)[:200]}")
            return reply(unavailable(edge_id), headers=headers)

    return router
