"""
NVIDIA NIM client: the "brain" calls of every agent (chat, embeddings, reranking).

Two backends, chosen by TMKG_LLM_BACKEND (default: 'nim' when NVIDIA_API_KEY or NIM_BASE_URL is set, else 'offline'):
  nim      real NVIDIA NIM - hosted at build.nvidia.com, or self-hosted on AWS (set NIM_BASE_URL)
  offline  deterministic stand-in so the whole pipeline runs and is tested without a key or network:
           character-trigram embeddings, overlap-based reranking, rule-based chat handlers registered by
           each agent. Every result carries backend='offline-stand-in' so it is never mistaken for NIM output.

Every NIM request goes through snapshot.Snapshot (folder data/snapshots_llm/): the answer is frozen, so a
run can be replayed exactly (TMKG_SNAPSHOT_MODE=offline) and every model output is traceable to its request.
The API key is sent as a header only - never stored, never part of the request key.

    nim = NIM()
    r = nim.chat("extractor", [{"role": "user", "content": "..."}], schema={...})   -> ChatResult
    vecs = nim.embed(["sacituzumab govitecan"], input_type="query")                 -> [[float]]
    order = nim.rerank("IMMU-132", ["SACITUZUMAB GOVITECAN; ...", ...])             -> [(index, score)]

Cached and fallback answers are labelled, never silent:
  ChatResult.meta["cached"] is True when the answer came from the frozen snapshot (a pinned question answered without
  the API); with fallback on (NIM(fallback="offline"), env TMKG_NIM_FALLBACK=offline or `fallback: offline` in
  config/nim_models.yaml) a failing hosted chat call is answered by the offline stand-in of the role (chat only: vectors of two
  backends cannot be mixed) and the result has backend='offline-stand-in' and meta["fallback_from"] / meta["reason"]. Fallback is OFF by default so evaluations
  never mix stand-in answers into NIM numbers; the app turns it on.
preflight(nim) checks every configured model id against the live catalogue with one tiny call each
(bin/agent-cli nim-check). preflight_summary(rep) turns that report into the app's start-up note (CC-36).

Second model (CC-36, only with fallback on, i.e. in the app): when a hosted chat call fails and config `fallback_models`
names a backup id for that model, the backup is called once before the stand-in; the result then carries model=<backup>,
meta["model_fallback_from"]=<primary id> and meta["reason"]. The backup id changes the request key, so its answers are
frozen apart from the primary's. NIM.fallbacks lists every fallback of this client: {role, from, to, reason}.

Web requests (the app): web_client() returns the model client for ONE request. It is a copy of the app's client with
its own call log, a short per-call budget (web_budget(): 30 s per call, one retry, no waiting behind a rate limit) and a
deadline for the whole request (75 s). A call that fails or has no time left is answered by the labelled stand-in at
once, so a slow or rate-limited model can no longer hold a request for minutes. Batch pipelines use NIM() as before
and keep the long waits. Every router module of the app takes its client from web_client().
"""
import copy, hashlib, json, math, os, re, tempfile, threading, time
from dataclasses import dataclass, field
from pathlib import Path
import yaml

from snapshot import HttpBudget, RateLimited, Snapshot

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "nim_models.yaml"
OFFLINE = "offline-stand-in"
_HANDLERS = {}                     # role -> fn(messages, schema) -> str   (offline stand-ins)


class OfflineUnavailable(RuntimeError):
    """No offline stand-in exists for this role; a real NIM backend is needed."""


@dataclass
class ChatResult:
    text: str                      # raw model text
    data: object                   # parsed JSON (when a schema was requested), else None
    model: str
    backend: str                   # 'nim' or 'offline-stand-in'
    key: str = ""                  # snapshot key of the frozen request ('' for offline)
    meta: dict = field(default_factory=dict)


def load_env(path=ROOT / ".env"):
    """Reads KEY=VALUE lines from .env into os.environ (existing variables win). No extra dependency.
    A .env that cannot be read (sandbox, locked-down container, permissions) counts as absent: the caller then
    uses variables already in the environment or the offline stand-ins, instead of crashing."""
    try:
        text = Path(path).read_text() if Path(path).exists() else ""
    except OSError:
        return
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def register_offline(role):
    """Decorator: an agent module registers its deterministic stand-in for a role."""
    def deco(fn):
        _HANDLERS[role] = fn
        return fn
    return deco


def parse_json(text):
    """First JSON object in a model reply (tolerates ```json fences and text around it), or None."""
    try:                                              # the usual case: the reply IS the JSON object
        whole = json.loads((text or "").strip())
        if isinstance(whole, dict):
            return whole
    except json.JSONDecodeError:
        pass
    t = re.sub(r"```(?:json)?", "", text or "")
    start = t.find("{")
    while start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            c = t[i]
            if in_str:                                # a quote right after a backslash is part of the text
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None


def check_schema(data, schema):
    """Minimal JSON-schema check (type object, required keys, enum, arrays of objects). -> list of problems."""
    problems = []
    def walk(d, s, path):
        t = s.get("type")
        if t == "object":
            if not isinstance(d, dict):
                problems.append(f"{path}: expected object"); return
            for k in s.get("required", []):
                if k not in d:
                    problems.append(f"{path}.{k}: missing")
            for k, sub in s.get("properties", {}).items():
                if k in d:
                    walk(d[k], sub, f"{path}.{k}")
        elif t == "array":
            if not isinstance(d, list):
                problems.append(f"{path}: expected array"); return
            for i, x in enumerate(d):
                walk(x, s.get("items", {}), f"{path}[{i}]")
        elif t == "string" and not isinstance(d, str):
            problems.append(f"{path}: expected string")
        elif t == "boolean" and not isinstance(d, bool):
            problems.append(f"{path}: expected boolean, got {type(d).__name__} {d!r}")   # "false" is truthy in Python
        elif t == "integer" and (not isinstance(d, int) or isinstance(d, bool)):
            problems.append(f"{path}: expected integer, got {type(d).__name__}")
        elif t == "number" and (not isinstance(d, (int, float)) or isinstance(d, bool)):
            problems.append(f"{path}: expected number, got {type(d).__name__}")
        if "enum" in s and d not in s["enum"]:
            problems.append(f"{path}: {d!r} not in {s['enum']}")
    walk(data, schema, "$")
    return problems


# ---------------------------------------------------------------- offline stand-ins for retrieval models
def _trigrams(text):
    t = " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip() + " "
    return [t[i:i + 3] for i in range(len(t) - 2)]


def offline_embed(text, dim=256):
    """Hashed character-trigram vector, L2-normalised (lexical similarity only - a stand-in, not semantics)."""
    v = [0.0] * dim
    for g in _trigrams(text):
        v[int(hashlib.md5(g.encode()).hexdigest(), 16) % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


class NIM:
    def __init__(self, backend=None, snap=None, config=CONFIG, fallback=None, budget=None):
        """budget: an HttpBudget for every live call of this client (the web app; see web_client), None = batch waits."""
        load_env()
        self.cfg = yaml.safe_load(Path(config).read_text())
        fb = fallback if fallback is not None else os.environ.get("TMKG_NIM_FALLBACK") or self.cfg.get("fallback") or "none"
        self.fallback = "offline" if fb == "offline" else "none"
        self.key = os.environ.get("NVIDIA_API_KEY", "")
        self.base_url = os.environ.get("NIM_BASE_URL") or self.cfg["base_url"]
        self.rerank_url = os.environ.get("NIM_RERANK_URL") or self.cfg["rerank_url"]
        default = "nim" if (self.key or os.environ.get("NIM_BASE_URL")) else "offline"
        self.backend = backend or os.environ.get("TMKG_LLM_BACKEND", default)
        self.snap = snap or Snapshot(ROOT / "data" / "snapshots_llm")
        self.calls = []            # (role/kind, model, backend, key) for the run log
        self.fallbacks = []        # {role, from, to ('offline-stand-in' or a backup model id), reason}: every fallback taken
        self.budget = budget       # None = the batch waits of snapshot.Snapshot; an HttpBudget = short waits + deadline

    # -------------------------------------------------------------- helpers
    def model(self, role):
        return self.cfg["roles"][role]

    def backup_model(self, model):
        """Takes a model id. Returns the backup id config `fallback_models` names for it, or None."""
        b = (self.cfg.get("fallback_models") or {}).get(model)
        return b if b and b != model else None

    def _hosted_chat(self, role, model, messages, schema):
        """One hosted chat call with `model`. Returns a ChatResult (backend 'nim'); raises on any failure."""
        gen = self.cfg.get("generation", {})
        msgs = list(messages)
        prefix = (self.cfg.get("system_prefix") or {}).get(model)
        if prefix:
            msgs = [{"role": "system", "content": prefix}] + msgs
        if schema:
            msgs = msgs + [{"role": "user", "content": "Reply with JSON only, matching this JSON schema:\n"
                            + json.dumps(schema, sort_keys=True)}]
        body = dict(model=model, messages=msgs, **gen)
        body.update((self.cfg.get("model_options") or {}).get(model) or {})   # e.g. thinking switched off
        mode = self.cfg.get("structured_output")
        if schema and mode == "response_format":        # server enforces the JSON schema (hosted Nemotron 3)
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": role, "schema": schema}}
        elif schema and (mode == "guided_json" or self.cfg.get("guided_json")):   # older / self-hosted NIM
            body["nvext"] = {"guided_json": schema}
        r = self._post(self.base_url.rstrip("/") + "/chat/completions", body)
        text = r.body["choices"][0]["message"]["content"]
        return ChatResult(text, parse_json(text) if schema else None, model, "nim", r.key,
                          {"usage": r.body.get("usage"), "fetched_at": r.fetched_at, "cached": r.cached})

    def _headers(self):
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def _post(self, url, body):
        budget = getattr(self, "budget", None)
        if budget is None:                               # batch pipelines: unchanged call
            return self.snap.post("nim", url, body, headers=self._headers(), store_client_errors=False)
        return self.snap.post("nim", url, body, headers=self._headers(), store_client_errors=False, budget=budget)

    def seconds_left(self):
        """Seconds until this client's deadline (None when it has none: a batch client)."""
        budget = getattr(self, "budget", None)
        return None if budget is None else budget.remaining()

    # -------------------------------------------------------------- chat
    def chat(self, role, messages, schema=None):
        """messages: OpenAI-style list. schema: optional JSON schema; the reply must contain matching JSON.
        Returns ChatResult. Raises ValueError when the reply does not satisfy the schema."""
        model = self.model(role)
        if self.backend == "offline":
            if role not in _HANDLERS:
                raise OfflineUnavailable(f"no offline stand-in for role {role!r}")
            text = _HANDLERS[role](messages, schema)
            res = ChatResult(text, parse_json(text) if schema else None, model, OFFLINE)
        else:
            try:
                res = self._hosted_chat(role, model, messages, schema)
            except Exception as err:                            # API down, rate limit, 404 for this account, no snapshot
                if self.fallback != "offline":
                    raise
                reason = f"{type(err).__name__}: {str(err)[:200]}"
                res, backup = None, self.backup_model(model)
                if backup:                                      # the second hosted model, once
                    try:
                        res = self._hosted_chat(role, backup, messages, schema)
                        res.meta.update(model_fallback_from=model, reason=reason)
                        self.fallbacks.append({"role": role, "from": model, "to": backup, "reason": reason})
                    except Exception as err2:
                        reason += f"; backup {backup}: {type(err2).__name__}: {str(err2)[:200]}"
                if res is None:
                    if role not in _HANDLERS:
                        raise
                    text = _HANDLERS[role](messages, schema)
                    res = ChatResult(text, parse_json(text) if schema else None, model, OFFLINE, "",
                                     {"fallback_from": "nim", "reason": reason})
                    self.fallbacks.append({"role": role, "from": backup or model, "to": OFFLINE, "reason": reason})
        if schema:
            problems = ["no JSON object in reply"] if res.data is None else check_schema(res.data, schema)
            if problems:
                raise ValueError(f"{role} reply does not match schema: {problems[:5]}")
        self.calls.append((role, model, res.backend, res.key))
        return res

    # -------------------------------------------------------------- embeddings
    def embed(self, texts, input_type="query"):
        """input_type: 'query' for questions/names to look up, 'passage' for the indexed candidates."""
        model = self.cfg["embedding"]
        if self.backend == "offline":
            self.calls.append(("embed", model, OFFLINE, ""))
            return [offline_embed(t) for t in texts]
        out = []
        for i in range(0, len(texts), 50):                       # batches keep request keys stable
            body = dict(model=model, input=list(texts[i:i + 50]), input_type=input_type,
                        encoding_format="float", truncate="END")
            r = self._post(self.base_url.rstrip("/") + "/embeddings", body)
            out += [d["embedding"] for d in sorted(r.body["data"], key=lambda d: d["index"])]
            self.calls.append(("embed", model, "nim", r.key))
        return out

    # -------------------------------------------------------------- reranking
    def rerank(self, query, passages):
        """-> [(passage index, score)] best first. Score scale depends on the backend (compare within a call)."""
        model = self.cfg["reranker"]
        if self.backend == "offline":
            q = offline_embed(query)
            qt = set(re.findall(r"[a-z0-9]+", query.lower()))
            scores = []
            for i, p in enumerate(passages):
                pt = set(re.findall(r"[a-z0-9]+", p.lower()))
                jacc = len(qt & pt) / (len(qt | pt) or 1)
                scores.append((i, round(0.7 * cosine(q, offline_embed(p)) + 0.3 * jacc, 6)))
            self.calls.append(("rerank", model, OFFLINE, ""))
            return sorted(scores, key=lambda x: (-x[1], x[0]))
        body = dict(model=model, query={"text": query}, passages=[{"text": p} for p in passages], truncate="END")
        r = self._post(self.rerank_url, body)
        self.calls.append(("rerank", model, "nim", r.key))
        return sorted(((x["index"], x["logit"]) for x in r.body["rankings"]), key=lambda x: (-x[1], x[0]))


# ---------------------------------------------------------------- the web app's model client (one per request)
_web = {}                          # process-wide: {"nim": the app's client}; pipelines/app.py's _state IS this dict
_web_lock = threading.RLock()


def _env_number(name, default):
    """Takes an environment variable name and a default. Returns its value as a float (the default when unset or not a number)."""
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


def web_budget(seconds=None, now=None):
    """The waiting rules of ONE web request as a snapshot.HttpBudget. Takes the seconds the whole request may spend on
    hosted model calls (default TMKG_WEB_NIM_DEADLINE, 75) and the current time.monotonic() (default: now). Per call:
    TMKG_WEB_NIM_TIMEOUT seconds (30), TMKG_WEB_NIM_ATTEMPTS tries (2 = one retry), no rate-limit wait."""
    seconds = _env_number("TMKG_WEB_NIM_DEADLINE", 75) if seconds is None else float(seconds)
    now = time.monotonic() if now is None else now
    return HttpBudget(timeout=_env_number("TMKG_WEB_NIM_TIMEOUT", 30), attempts=int(_env_number("TMKG_WEB_NIM_ATTEMPTS", 2)),
                      rate_limit_waits=0, backoff=1.0, deadline=now + seconds if seconds > 0 else None)


def web_base():
    """The app's one model client, made on first use (a failing hosted call falls back to the labelled stand-in unless
    TMKG_NIM_FALLBACK says otherwise). Thread-safe. Returns a NIM."""
    with _web_lock:
        if "nim" not in _web:
            _web["nim"] = NIM(fallback=os.environ.get("TMKG_NIM_FALLBACK") or "offline")
        return _web["nim"]


def web_client(seconds=None, base=None):
    """The model client for ONE web request: the one function every endpoint that calls a model takes its client from.
    Takes the seconds the request may spend on hosted calls (default web_budget's 75) and optionally the client to copy
    (default: the app's, web_base()). Returns a copy that shares the configuration, key and frozen answers but has its
    own call log (calls, fallbacks), the short per-call budget and the request's deadline. chat() on it never waits
    behind a rate limit and answers with the labelled stand-in as soon as a hosted call fails or the time is used up."""
    client = copy.copy(base if base is not None else web_base())
    client.calls, client.fallbacks = [], []
    client.budget = web_budget(seconds)
    return client


# ---------------------------------------------------------------- pre-flight: are the configured models usable today?
PREFLIGHT_SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
PREFLIGHT_PROMPT = 'Reply with the JSON object {"ok": true} and nothing else.'


def live_model_ids(nim, snap, budget=None):
    """Takes a NIM, a Snapshot and optionally an HttpBudget (FIX1 F4: the start-up check's short waits). Returns the sorted
    model ids the endpoint's /models lists (hosted catalogue or a self-hosted NIM), or None when the listing cannot be read.
    The catalogue can list a model an account may not call (2026-10-31: nemotron-3-super-120b-a12b listed, 404 on call), so
    a listing is a hint, the tiny call is the test. With a budget an HTTP 429 raises RateLimited (the caller stops there)."""
    url = nim.base_url.rstrip("/") + "/models"
    try:
        if budget is not None:                           # one quick try; nothing is stored (the folder is a throw-away)
            body = snap._http("GET", url, None, None, headers=nim._headers(), budget=budget) or {}
        else:
            body = snap.get("nim_models", url, headers=nim._headers()).body or {}
        return sorted({m["id"] for m in body.get("data", []) if "id" in m})
    except RateLimited:
        raise
    except Exception:
        return None


RATE_LIMITED_DETAIL = "rate limited (HTTP 429): the service is refusing calls right now"


def preflight(nim=None, snap=None, list_models=True, budget=web_budget):
    """Checks every model id of config/nim_models.yaml (chat roles, embedding, reranker) with one tiny call each, the
    chat ones with a structured-output request (a JSON object {"ok": true}); nothing is stored in data/snapshots_llm
    (a throw-away snapshot folder is used) so a pre-flight never becomes a frozen answer. Takes a NIM (default
    NIM()), an optional Snapshot for the calls, and whether to read the live model list.
    Returns {backend, base_url, listed (list of ids or None), ok (no model failed), rows: [{kind, model, roles,
    listed, status 'ok' | 'failed', seconds, detail}]}. On the offline backend nothing is called: rows is empty and
    ok is None (there is no hosted model to check).
    budget (FIX1 F4, gate 1 G1-SEC-06): a function returning a fresh snapshot.HttpBudget per call (default web_budget: 30 s
    per call, one retry, no waiting behind a rate limit), or None for the batch waits. Under an HTTP 429 at start-up the
    batch waits made 80 sends over ~49 minutes; now the first 429 ends the check: that model and every model not yet
    checked are reported "rate limited" without another send, and rate_limited is True."""
    import copy
    nim = nim or NIM()
    rep = {"backend": nim.backend, "base_url": nim.base_url, "listed": None, "ok": None, "rows": [], "rate_limited": False}
    if nim.backend == "offline":
        return rep
    tmp = tempfile.TemporaryDirectory()
    snap = snap or Snapshot(Path(tmp.name), mode="refresh", sleep=0)
    probe = copy.copy(nim)
    probe.snap, probe.backend, probe.fallback, probe.calls = snap, "nim", "none", []
    if list_models:
        try:
            rep["listed"] = live_model_ids(probe, snap, budget=budget() if budget else None)
        except RateLimited:
            rep["rate_limited"] = True
    by_model = {}
    for role, model in nim.cfg["roles"].items():
        by_model.setdefault(model, []).append(role)
    jobs = [("chat", m, roles, lambda r=roles[0]: probe.chat(r, [{"role": "user", "content": PREFLIGHT_PROMPT}],
                                                            schema=PREFLIGHT_SCHEMA))
            for m, roles in by_model.items()]
    jobs.append(("embedding", nim.cfg["embedding"], ["embedding"], lambda: probe.embed(["ping"])))
    jobs.append(("reranker", nim.cfg["reranker"], ["reranker"], lambda: probe.rerank("ping", ["a", "b"])))
    for kind, model, roles, call in jobs:
        t0 = time.time()
        if rep["rate_limited"]:                          # no further send once the service said 429
            rep["rows"].append(dict(kind=kind, model=model, roles=roles, status="failed",
                                    listed=None if rep["listed"] is None else model in rep["listed"],
                                    seconds=0.0, detail=RATE_LIMITED_DETAIL + " (not checked)"))
            continue
        probe.budget = budget() if budget else None
        try:
            out = call()
            ok, detail = True, "answered"
            if kind == "chat" and out.data != {"ok": True}:
                ok, detail = False, f"unexpected reply {out.text[:80]!r}"
        except RateLimited:
            rep["rate_limited"] = True
            ok, detail = False, RATE_LIMITED_DETAIL
        except Exception as err:
            ok, detail = False, f"{type(err).__name__}: {str(err)[:160]}"
            if "404" in detail:
                detail += "  (not found for this account: pick another id in config/nim_models.yaml)"
        rep["rows"].append(dict(kind=kind, model=model, roles=roles, status="ok" if ok else "failed",
                                listed=None if rep["listed"] is None else model in rep["listed"],
                                seconds=round(time.time() - t0, 2), detail=detail))
    rep["ok"] = all(r["status"] == "ok" for r in rep["rows"])
    tmp.cleanup()
    return rep


def preflight_summary(rep):
    """Takes a preflight() report (or None while it has not run). Returns {state, ok, failed, n, sentence} for the app's
    start-up note: state 'offline' (nothing hosted to check), 'pending' (not finished yet), 'ok' or 'failed'; failed =
    the model ids that did not answer; n = models checked. The sentence is built from those values."""
    if rep is None:
        return dict(state="pending", ok=None, failed=[], n=0, sentence="The start-up check of the hosted models has not finished yet.")
    if rep.get("backend") == "offline":
        return dict(state="offline", ok=None, failed=[], n=0, sentence="")
    rows = rep.get("rows") or []
    failed = sorted({r["model"] for r in rows if r.get("status") != "ok"})
    n = len({r["model"] for r in rows})
    if not failed:
        return dict(state="ok", ok=True, failed=[], n=n, sentence=f"Checked at start-up: all {n} hosted models answered.")
    return dict(state="failed", ok=False, failed=failed, n=n,
                sentence=f"Checked at start-up: {len(failed)} of {n} hosted models did not answer. "
                         "Questions that need them are answered by a backup model or by fixed rules, and the answer says so.")


def preflight_text(rep):
    """The pre-flight report as a table of plain lines (one per model) plus a verdict line."""
    if rep["backend"] == "offline":
        return ("backend: offline stand-in (no NVIDIA_API_KEY / NIM_BASE_URL): nothing hosted to check. "
                "Set the key in .env and run again.")
    lines = [f"endpoint {rep['base_url']}; live catalogue: "
             + ("not readable" if rep["listed"] is None else f"{len(rep['listed'])} models listed"), ""]
    for r in rep["rows"]:
        listed = {None: "?", True: "listed", False: "NOT LISTED"}[r["listed"]]
        lines.append(f"{r['status'].upper():7} {r['kind']:9} {r['model']}  [{', '.join(r['roles'])}]  {listed}, "
                     f"{r['seconds']} s" + ("" if r["status"] == "ok" else f"\n        {r['detail']}"))
    bad = [r for r in rep["rows"] if r["status"] != "ok"]
    lines += ["", "all models answer" if not bad else f"{len(bad)} model(s) FAILED: fix config/nim_models.yaml before any demo"]
    return "\n".join(lines)
