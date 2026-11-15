"""
In-process API for the jsdom journeys (no port is opened: the sandbox cannot bind one, and the hosted demo is not used).

run_page_v3.js starts this script and writes one JSON request per line to its stdin: {"id", "method", "url", "body"}.
Each request is answered by FastAPI's TestClient against pipelines/app.py (the real app, the local trial graph, the card
index named by TMKG_PHAROS_INDEX, the offline model stand-in) and written back as one JSON line:
{"id", "status", "type", "headers", "url", "text"} (binary bodies as {"b64": ...}; "url" is the address after redirects). A request {"method": "QUIT"} ends it.
CC-46: with CLICK_GATE_429=1 the app's real ask limiter runs with a full hour for every visitor (limit 20, 20 questions asked
30 minutes ago), so every ask answers 429 with its real Retry-After and /api/ask_quota reports 0 left.
PK15 (journey J12, click-gate rows): NEPTUNE_FAKE=available | asleep | asleep_flicker | error gives the app a configured Amazon Neptune without a
cluster. A temporary folder plays the host of PK14 A2 (status.json rewritten before every request, so it is never stale; a
wake.request left while "stopped" is taken: the state becomes "starting", the file is deleted, and after NEPTUNE_FAKE_WAKE_POLLS
status reads (default 2) the state is "available"), and the live Neptune call is answered by running the same query on the app's
own graph (querier.run_local, Neptune's {"results": [...]} shape); "error" answers every query with an HTTP 500;
"not_configured" removes NEPTUNE_ENDPOINT. Any of the four: without a card index the Pharos test fixture is the graph. Unset: nothing changes.
"""
import base64
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipelines"))
import pharos_index as _pharos_index         # noqa: E402
if os.environ.get("NEPTUNE_FAKE") and not os.environ.get("TMKG_PHAROS_ROOT") and not _pharos_index.index_path(ROOT).exists():
    # PK15: where no card index exists (a cloud checkout) the Neptune runs use the Pharos test fixture, so an answer has rows
    import tempfile as _tf
    sys.path.insert(0, str(ROOT / "tests"))
    from fixtures.pharos_fixture import build as _build_fixture   # noqa: E402
    os.environ.update(TMKG_PHAROS_ROOT=str(_build_fixture(Path(_tf.mkdtemp(prefix="tmkg_fx_")))), TMKG_PHAROS_INDEX="off")
# the same default as the app: the newest card index that exists (v3 = the shipped graph), not a fixed older file
os.environ.setdefault("TMKG_PHAROS_INDEX", str(_pharos_index.index_path(ROOT)))
from fastapi.testclient import TestClient   # noqa: E402
import app as A                             # noqa: E402

if os.environ.get("CLICK_GATE_429") == "1":
    import time as _time
    os.environ["TMKG_ASK_LIMIT_PER_HOUR"] = "20"

    class _FullHour(dict):
        """Every visitor has asked 20 questions 30 minutes ago."""
        def get(self, key, default=None):
            return [_time.time() - 1800.0] * 20
    A._ask_log = _FullHour()
if os.environ.get("NEPTUNE_FAKE") == "not_configured":      # the fixture graph when needed (above), and no Amazon Neptune at all
    os.environ.pop("NEPTUNE_ENDPOINT", None)
if os.environ.get("NEPTUNE_FAKE") in ("available", "asleep", "asleep_flicker", "error"):
    import tempfile as _tempfile, datetime as _dt
    import neptune_backend as _nb, querier as _querier
    _mode = os.environ["NEPTUNE_FAKE"]
    _dir = Path(_tempfile.mkdtemp(prefix="tmkg_neptune_"))
    os.environ.update(NEPTUNE_ENDPOINT="neptune.fake.invalid", NEPTUNE_STATUS_DIR=str(_dir))
    os.environ.setdefault("NEPTUNE_WAKE_MINUTES", "4")
    _host = {"state": "stopped" if _mode.startswith("asleep") else "available", "reads": 0, "lag": 0}
    # FIX2 / S17 item 2: asleep_flicker = the host takes the wake request but still reports "stopped" for
    # NEPTUNE_FAKE_FLICKER_READS status reads (default 2) before "starting", as Amazon Neptune did once on 14 Nov
    _flicker = int(os.environ.get("NEPTUNE_FAKE_FLICKER_READS", "2")) if _mode == "asleep_flicker" else 0

    def _host_tick(url):
        """The host of PK14 A2, one step per request: take a wake request (and, in asleep_flicker, keep reporting stopped for a
        few status reads after it), count status reads while starting, write status.json."""
        if _host["state"] == "stopped" and (_dir / "wake.request").exists():
            _host["state"] = "starting" if not _flicker else "stopped_after_wake"
            (_dir / "wake.request").unlink()
        elif _host["state"] == "stopped_after_wake" and url.startswith("/api/neptune/status"):
            _host["lag"] += 1
            if _host["lag"] >= _flicker:
                _host["state"] = "starting"
        elif _host["state"] == "starting" and url.startswith("/api/neptune/status"):
            _host["reads"] += 1
            if _host["reads"] >= int(os.environ.get("NEPTUNE_FAKE_WAKE_POLLS", "2")):
                _host["state"] = "available"
        now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        shown = "stopped" if _host["state"] == "stopped_after_wake" else _host["state"]
        (_dir / "status.json").write_text(json.dumps({"state": shown, "checked_at": now}))

    def _fake_post_live(self, cypher):
        if _mode == "error":
            raise RuntimeError("POST failed: HTTP 500")
        return {"results": _querier.run_local(cypher, A.answering_graph())["rows"]}
    _nb.NeptuneRunner._post_live = _fake_post_live
else:
    def _host_tick(url):
        return None
client = TestClient(A.app)
out = sys.stdout
sys.stdout = sys.stderr                      # anything the app prints must not corrupt the protocol
for line in sys.stdin:
    if not line.strip():
        continue
    req = json.loads(line)
    if req.get("method") == "QUIT":
        break
    try:
        _host_tick(req.get("url") or "")
        if req["method"] == "POST":
            r = client.post(req["url"], content=req.get("body") or "", headers={"Content-Type": "application/json"})
        else:
            r = client.get(req["url"])
        ctype = r.headers.get("content-type", "")
        res = dict(id=req["id"], status=r.status_code, type=ctype, headers={k.lower(): v for k, v in r.headers.items()},
                   url=str(r.url))             # the address after any redirect (an old /v3 link lands on "/")
        if "json" in ctype or "text" in ctype or "html" in ctype or "event-stream" in ctype:
            res["text"] = r.text
        else:
            res["b64"] = base64.b64encode(r.content).decode()
    except Exception as err:                 # a failing route is reported to the page as a 500, like a server would
        res = dict(id=req["id"], status=500, type="application/json", text=json.dumps({"detail": f"bridge: {err}"}))
    out.write(json.dumps(res) + "\n")
    out.flush()
