"""Post-redeploy check of the hosted app. Run: .venv/bin/python scripts/hosted_check.py [--get-only] [--neptune] [base_url]
--get-only (`make hosted-check`): GET and demo endpoints only; the two ask calls (POST /api/pharos/ask, /api/ask) are
skipped, so the check does not spend the shared visitor ask limit (20 per hour per IP). Exit 1 when any line is FAIL.
PK15: two more GET-only assertions, neptune_status (the state is one of the five; when Amazon Neptune is configured, the loaded
counts equal the export manifest's, /downloads/neptune/manifest.json) and front_door (only when the base is a cloudfront.net
address: the answer to / carries a Via header naming CloudFront, and /downloads/kgx/SHA256SUMS answers 200). --neptune sends ONE
tested question with backend "neptune" and reports answered, ms and match; without it the script never causes a Neptune query
(a query keeps the database awake, which costs money). GET /api/neptune/status never contacts Neptune.
Prints the smoke lines, then one line per review-panel assertion (FIX_PLAN_2026-11-08 CS-6):
  PASS <name>: ...   FAIL <name>: ...   EXPECTED-FAIL(<packet>) <name>: ...   (a failing check whose fixing packet has not landed)
No assertion aborts the script. When a packet lands and is redeployed, delete its entry from PENDING."""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path

B = "https://tmkg.16-178-197-241.sslip.io"

# check name -> the packet whose landing (plus a redeploy, CS-10) makes it pass. Remove an entry once its check shows PASS.
PENDING = {
    # CC-30 and CC-31 landed with redeploy #2 (9 Nov, overlay files).
    # byo_built is NOT pending (11 Nov): it failed because the image had no examples/ folder (aws/Dockerfile now copies it),
    # not because of the index, so it is a plain FAIL, a blocker, until the redeployed image builds the example.
    # schema_terms is NOT pending any more (11 Nov, S02): the rebuilt index is deployed and the check still fails, locally and
    # hosted alike (9 of 11 classes). The terms are BioLink's exact mappings, and BioLink 4.4.6 declares none for
    # "disease or phenotypic feature" and "small molecule". Owner decision 14 Nov (G1-ES-07, accepted): the check passes a
    # class BioLink leaves unmapped and names it (biolink_unmapped(), read from outputs/schema_card.json); the schema card
    # says so in that class's cell. Any other class without terms is still a FAIL.
}
DEVELOPER_TEXT = ["The Mac writes", "run.py --tag", "No result file on this machine"]   # CC-29's strings
TEXT_MINED = {"text_co_occurrence"}                                                     # BioLink KnowledgeLevel


def check_benchmark_runs(results):
    """Takes /api/results. Returns (ok, detail): ok when benchmark.runs is non-empty."""
    runs = (results.get("benchmark") or {}).get("runs") or []
    return len(runs) > 0, f"benchmark.runs = {len(runs)}"


def check_experiments_available(results):
    """Takes /api/results. Returns (ok, detail): ok when there is at least one experiment and every one has available true."""
    ex = results.get("experiments") or []
    off = [e.get("id") for e in ex if not e.get("available")]
    return bool(ex) and not off, f"{len(ex) - len(off)} of {len(ex)} available; not available: {off}"


def check_egfr_predictions_novel(card):
    """Takes /api/pharos/card/NCBIGene:1956. Returns (ok, detail): ok when the shown prediction list (card['novel'] once
    CC-30 splits it, else card['predicted']) is non-empty and every entry has known_in == []."""
    key = "novel" if "novel" in card else "predicted"
    shown = card.get(key) or []
    known = sum(1 for p in shown if p.get("known_in"))
    return bool(shown) and known == 0, f"{key}: {len(shown)} shown, {known} with known_in non-empty"


def check_demo_first_row(demo):
    """Takes /api/pharos/demo/pharos_demo_1. Returns (ok, detail): ok when there is a first row and its knowledge_level
    is not text-mined."""
    rows = demo.get("rows") or []
    if not rows:
        return False, "no rows"
    kl = rows[0].get("knowledge_level")
    levels = kl if isinstance(kl, list) else ([kl] if kl is not None else [])    # CC-31a merges one answer's rows: list cells
    return bool(levels) and levels[0] not in TEXT_MINED, f"row 1 knowledge_level={kl} source={rows[0].get('source')}"


def check_byo_built(gen):
    """Takes /api/generalization. Returns (ok, detail): ok when byo.built is not null."""
    built = (gen.get("byo") or {}).get("built")
    return built is not None, f"byo.built = {json.dumps(built)[:120]}"


SCHEMA_CARD_JSON = Path(__file__).resolve().parents[1] / "outputs" / "schema_card.json"


def biolink_unmapped(card_path=None):
    """Takes the path of the committed schema card JSON (default: the repository's outputs/schema_card.json). Returns the
    set of class names the card marks as BioLink classes whose BioLink entry has no exact mapping (is_biolink true,
    exact_mappings empty); an empty set when the file is missing or unreadable, so the check then falls back to the old rule."""
    try:
        card = json.loads(Path(card_path or SCHEMA_CARD_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {c.get("name") for c in card.get("classes") or [] if c.get("is_biolink") and not c.get("exact_mappings")}


def check_schema_terms(results, unmapped=None):
    """Takes /api/results and optionally the set of classes BioLink maps to no exact term (default: biolink_unmapped()).
    Returns (ok, detail): ok when schema.classes is non-empty and every class has non-empty terms, or has none because
    BioLink defines no exact mapping for it (G1-ES-07, owner decision 14 Nov); the detail names those classes."""
    classes = (results.get("schema") or {}).get("classes") or []
    unmapped = biolink_unmapped() if unmapped is None else set(unmapped)
    empty = [c.get("name") for c in classes if not c.get("terms")]
    missing = [n for n in empty if n not in unmapped]
    allowed = [n for n in empty if n in unmapped]
    detail = f"{len(classes) - len(empty)} of {len(classes)} classes with terms; empty: {missing}"
    if allowed:
        detail += f"; BioLink defines no exact mapping for: {allowed}"
    return bool(classes) and not missing, detail


def check_v3_no_developer_text(html):
    """Takes the /v3 HTML (bytes or str). Returns (ok, detail): ok when none of DEVELOPER_TEXT occurs in it."""
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    found = [s for s in DEVELOPER_TEXT if s in text]
    return not found, f"developer strings found: {found}"


def check_calibration_matches_prune_report(results, prune_report):
    """FIX1 F7 (gate 1, G1-AC-02). Takes /api/results and the text of prune_report.md (/api/file?name=prune_report.md).
    Returns (ok, detail): ok when the calibration's pruned count (calibration.by_verdict, verdict prune, n) equals the pruned
    non-text gene-disease count of the report's shipped-verdicts row ('non-text G-D pruned: n / ...' column). The live site
    once served an older calibration file (prune n 505) under a headline computed from the committed one (280)."""
    import re
    text = prune_report.decode("utf-8", "replace") if isinstance(prune_report, bytes) else prune_report
    rows = [r for r in (results.get("calibration") or {}).get("by_verdict") or [] if r.get("verdict") == "prune"]
    cal_n = rows[0].get("n") if rows else None
    head = next((ln for ln in text.splitlines() if "non-text G-D pruned" in ln), "")
    row = next((ln for ln in text.splitlines() if ln.startswith("| shipped verdicts")), "")
    rep_n = None
    if head and row:
        cols = [c.strip() for c in head.strip("|").split("|")]
        cells = [c.strip() for c in row.strip("|").split("|")]
        i = next((k for k, c in enumerate(cols) if c.startswith("non-text G-D pruned")), None)
        m = re.match(r"([\d,]+)", cells[i]) if i is not None and i < len(cells) else None
        rep_n = int(m.group(1).replace(",", "")) if m else None
    return cal_n is not None and cal_n == rep_n, f"calibration prune n = {cal_n}, prune report non-text G-D pruned = {rep_n}"


NEPTUNE_STATES = ("not_configured", "available", "asleep", "waking", "unavailable")


def check_neptune_status(status, manifest=None):
    """Takes /api/neptune/status and the export manifest's counts ({nodes, edges} of /downloads/neptune/manifest.json, or None
    when it is not reachable). Returns (ok, detail): ok when the state is one of NEPTUNE_STATES and, when Neptune is configured
    and both counts are known, status.loaded equals the manifest's nodes and edges; a status without loaded passes with
    "not compared" (in any state)."""
    st = (status or {}).get("state")
    if st not in NEPTUNE_STATES:
        return False, f"state = {st!r}, not one of {', '.join(NEPTUNE_STATES)}"
    if st == "not_configured":
        return True, "state = not_configured (Amazon Neptune is not set up on this server)"
    loaded = (status or {}).get("loaded")
    if not loaded:                    # FIX2 F5 / S17 item 1: absent in some states and builds; nothing to compare, not a failure
        return True, f"state = {st}; loaded counts not given by the server, not compared"
    if not manifest:
        return True, f"state = {st}; loaded = {loaded}; the manifest was not reachable, counts not compared"
    want = dict(nodes=manifest.get("nodes"), links=manifest.get("edges"))
    return loaded == want, f"state = {st}; loaded = {loaded}; manifest = {want}"


def is_cloudfront(base):
    """Takes the base URL. Returns True when its host is a cloudfront.net name."""
    host = urllib.parse.urlparse(base).hostname or ""
    return host == "cloudfront.net" or host.endswith(".cloudfront.net")


def check_front_door(base, via, sums_status):
    """Takes the base URL, the Via header of the answer to / ('' when none) and the HTTP status of /downloads/kgx/SHA256SUMS.
    Returns (ok, detail): for a cloudfront.net base, ok when Via names CloudFront and the checksums answer 200; for any other
    base, ok (nothing to check)."""
    if not is_cloudfront(base):
        return True, "not a cloudfront.net address: skipped"
    named = "cloudfront" in str(via or "").lower()
    return named and sums_status == 200, f"via = {via!r}; /downloads/kgx/SHA256SUMS -> {sums_status}"


def neptune_ask_line(status_code, answer):
    """Takes the HTTP status and the JSON of one POST /api/pharos/ask with backend 'neptune'. Returns the report line
    (answered, ms, match or the note); 'FAIL' when the request itself failed (Neptune unavailable is NOT a failure: the local
    answer with its note is the designed behaviour)."""
    n = (answer or {}).get("neptune") or {}
    if status_code != 200 or not n:
        return f"FAIL neptune_ask: HTTP {status_code}, neptune = {n}"
    if n.get("answered"):
        return f"PASS neptune_ask: answered=True ms={n.get('ms')} match={json.dumps(n.get('match'))}"
    return f"PASS neptune_ask: answered=False state={n.get('state')} note={n.get('note')!r} (the local answer was given)"


def neptune_assertions(fetch_full, base, ask_neptune=False, question=None):
    """Takes fetch_full(path, data=None) -> (status, headers dict with lower-case keys, body bytes), the base URL, whether to send
    the one Neptune question (--neptune) and the tested question to send. Prints and returns the verdict lines of neptune_status,
    front_door and (with ask_neptune) neptune_ask. Never raises; without ask_neptune nothing here reaches Neptune."""
    lines = []

    def emit(line):
        print(line)
        lines.append(line)
    try:
        st, _, body = fetch_full("/api/neptune/status")
        status = json.loads(body) if st == 200 else {"state": f"HTTP {st}"}
        ms, _, mb = fetch_full("/downloads/neptune/manifest.json")
        manifest = (json.loads(mb).get("counts") if ms == 200 else None)
        emit(verdict("neptune_status", *check_neptune_status(status, manifest)))
    except Exception as e:
        emit(verdict("neptune_status", False, f"error: {type(e).__name__}: {e}"))
    try:
        _, headers, _ = fetch_full("/")
        ss, _, _ = fetch_full("/downloads/kgx/SHA256SUMS")
        emit(verdict("front_door", *check_front_door(base, headers.get("via", ""), ss)))
    except Exception as e:
        emit(verdict("front_door", False, f"error: {type(e).__name__}: {e}"))
    if ask_neptune:
        try:
            st, _, body = fetch_full("/api/pharos/ask", {"question": question, "backend": "neptune"})
            emit(neptune_ask_line(st, json.loads(body) if st == 200 else {}))
        except Exception as e:
            emit(f"FAIL neptune_ask: error: {type(e).__name__}: {e}")
    return lines


def verdict(name, ok, detail, pending=PENDING):
    """Takes a check name, its result and detail. Returns the printed line: PASS, FAIL, or EXPECTED-FAIL(<packet>) when
    the check fails and its packet is still in pending."""
    if ok:
        return f"PASS {name}: {detail}"
    if name in pending:
        return f"EXPECTED-FAIL({pending[name]}) {name}: {detail}"
    return f"FAIL {name}: {detail}"


def run_assertions(fetch):
    """Takes fetch(path) -> body bytes. Runs every review-panel check, printing one verdict line each; an exception in a
    fetch or a check is printed as FAIL (or EXPECTED-FAIL) and never stops the rest. Returns the list of lines."""
    cache = {}
    def j(path):
        if path not in cache:
            cache[path] = json.loads(fetch(path))
        return cache[path]
    checks = [
        ("benchmark_runs", lambda: check_benchmark_runs(j("/api/results"))),
        ("experiments_available", lambda: check_experiments_available(j("/api/results"))),
        ("egfr_predictions_novel", lambda: check_egfr_predictions_novel(j("/api/pharos/card/NCBIGene:1956"))),
        ("demo_1_first_row_not_text_mined", lambda: check_demo_first_row(j("/api/pharos/demo/pharos_demo_1"))),
        ("byo_built", lambda: check_byo_built(j("/api/generalization"))),
        ("schema_terms", lambda: check_schema_terms(j("/api/results"))),
        ("v3_no_developer_text", lambda: check_v3_no_developer_text(fetch("/v3"))),
        ("calibration_matches_prune_report", lambda: check_calibration_matches_prune_report(
            j("/api/results"), fetch("/api/file?name=prune_report.md"))),
    ]
    lines = []
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:  # a missing key or a failed request is a failed check, not a crash
            ok, detail = False, f"error: {type(e).__name__}: {e}"
        line = verdict(name, ok, detail)
        print(line)
        lines.append(line)
    return lines


def call_full(path, data=None, timeout=90):
    """Like call(), with the response headers: returns (status, {header: value} with lower-case names, body bytes)."""
    req = urllib.request.Request(B + path, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()


def call(path, data=None, timeout=90):
    req = urllib.request.Request(B + path, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, time.time() - t, r.read()
    except urllib.error.HTTPError as e: return e.code, time.time() - t, e.read()


GET_ONLY = False      # --get-only: skip the ask endpoints (see the module text)
NEPTUNE = False       # --neptune: send ONE tested question with backend "neptune" (PK15)


def parse_args(argv):
    """Takes the command-line arguments (without the program name). Returns (base_url or None, get_only, neptune)."""
    get_only, neptune = "--get-only" in argv, "--neptune" in argv
    rest = [a for a in argv if a not in ("--get-only", "--neptune")]
    return (rest[0].rstrip("/") if rest else None), get_only, neptune


def main():
    for i in range(20):
        try: s, _, _ = call("/api/pharos/status"); break
        except Exception: time.sleep(30)
    print("http ->", urllib.request.urlopen(B.replace("https://", "http://") + "/", timeout=30).geturl())
    gets = ["/", "/api/landing", "/api/status", "/api/personas", "/api/wording", "/api/backends", "/api/neptune/status", "/api/generalization",
            "/api/agent-loop", "/api/downloads", "/api/results", "/api/search?q=EGFR", "/api/trials_for?q=EGFR",
            "/api/pharos/status", "/api/pharos/demo_questions", "/api/pharos/search?q=EGFR", "/api/pharos/card/NCBIGene:1956"]
    res = {p: call(p) for p in gets}
    print("GET non-200:", [(p, r[0]) for p, r in res.items() if r[0] != 200], f"of {len(gets)}; slowest {max(r[1] for r in res.values()):.1f}s")
    print("--- review-panel assertions (FIX_PLAN_2026-11-08 CS-6)")
    lines = run_assertions(lambda p: res[p][2] if p in res else call(p, timeout=120)[2])
    print("--- Amazon Neptune and the front door (PK15; GET only unless --neptune)")
    try:
        tested = json.loads(res["/api/pharos/demo_questions"][2])[0]["question"]
    except Exception:
        tested = None
    lines += neptune_assertions(call_full, B, ask_neptune=NEPTUNE and tested is not None, question=tested)
    print("--- smoke")
    try:
        smoke(res)
    except Exception as e:   # the smoke lines are informative; a shape change must not hide the assertion lines above
        print(f"smoke stopped: {type(e).__name__}: {e}")
    return 1 if any(line.startswith("FAIL ") for line in lines) else 0


def smoke(res):
    land = json.loads(res["/api/landing"][2]); print("landing chips:", [c.get("label") or c.get("text") or c for c in land.get("chips", [])][:3], "| model:", land.get("model", {}).get("label"))
    ps = json.loads(res["/api/pharos/status"][2]); print("index built:", (ps.get("card_index") or {}).get("built_at"), "| predictions:", (ps.get("predictions") or {}).get("rows"))
    qs = json.loads(res["/api/pharos/demo_questions"][2])
    for q in qs:
        st, dt, b = call(f"/api/pharos/demo/{q['id']}"); d = json.loads(b)
        print(f"  {q['id']}: {st} {dt:.1f}s passed={d.get('passed')} summary={'headline' in (d.get('summary') or {})}")
    c = json.loads(res["/api/pharos/card/NCBIGene:1956"][2]); p = c.get("novel") or c["predicted"]
    print("EGFR card:", len(p), "predictions;", p[0]["paths"][0]["edges"][0]["statement"] if p and p[0].get("paths") else None)
    if GET_ONLY:
        print("ask endpoints skipped (--get-only)")
    else:
        ask_smoke(qs)
    smoke_pages()


def ask_smoke(qs):
    """The two ask calls (POST; each counts against the shared visitor limit). Takes the demo question list."""
    st, dt, b = call("/api/pharos/ask", {"question": qs[0]["question"]}, timeout=120); a = json.loads(b) if st == 200 else {}
    print(f"ask online: {st} {dt:.1f}s backend={a.get('backend') or (a.get('run') or {}).get('backend')} model={a.get('model') or (a.get('run') or {}).get('model')} problems={a.get('problems')} rows={len(a.get('rows') or [])}")
    print(f"  ask summary (v3 bottom line): {'headline' in (a.get('summary') or {})} | {str((a.get('summary') or {}).get('headline'))[:120]}")
    st, dt, b = call("/api/ask", {"question": "Which trials test EGFR inhibitors?"}, timeout=120); a = json.loads(b) if st == 200 else {}
    print(f"trials ask: {st} {dt:.1f}s run={ {k: (a.get('run') or {}).get(k) for k in ('backend','model','cached')} } sections={len(a.get('sections') or [])} summary={'headline' in (a.get('summary') or {})}")


def smoke_pages():
    """GET-only smoke lines: /v2, /v3, agent loop, downloads listing and a range request."""
    st, dt, b = call("/v2"); print(f"/v2: {st} {dt:.1f}s {len(b):,} bytes | is the redesign: {b'app_page_v2' in b or b'tmkg2_' in b}")
    st, dt, b = call("/v3"); print(f"/v3: {st} {dt:.1f}s {len(b):,} bytes | is the rich redesign: {b'tmkg3_persona' in b and b'tmkg3_trail' in b}")
    st, dt, b = call("/api/agent-loop"); j = json.loads(b); print("agent-loop available:", j.get("available"), "| steps:", len(j.get("steps") or []))
    st, dt, b = call("/api/downloads"); d = json.loads(b)
    print("downloads available:", d.get("available"), "| base:", d.get("base_url"), "| entries:", [(f["path"], f["bytes"]) for f in d.get("files", [])])
    for f in d.get("files", []):
        st, dt, b = call("/downloads/" + f["path"]); print(f"  listing {f['path']}: {st}, SHA256SUMS listed: {b'SHA256SUMS' in b}")
    st, dt, b = call("/downloads/kgx/SHA256SUMS"); print("kgx/SHA256SUMS:", st, b.decode()[:160].replace(chr(10), " | "))
    req = urllib.request.Request(B + "/downloads/kgx/nodes.tsv.gz", headers={"Range": "bytes=0-1"})
    with urllib.request.urlopen(req, timeout=60) as r: print("kgx/nodes.tsv.gz range request:", r.status, r.headers.get("Content-Range"), "| gzip magic:", r.read() == b"\x1f\x8b")


if __name__ == "__main__":
    base, GET_ONLY, NEPTUNE = parse_args(sys.argv[1:])
    if base:
        B = base
    sys.exit(main())
