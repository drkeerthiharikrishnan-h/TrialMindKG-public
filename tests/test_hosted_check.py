"""Tests for the review-panel assertions in scripts/hosted_check.py (FIX_PLAN CS-6; moved there in FIX6 M1) and
for the deploy sync list that ships the benchmark JSONs to /data (CS-1). Fixtures only; no network."""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("hosted_check", ROOT / "scripts/hosted_check.py")
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


def test_benchmark_runs():
    assert hc.check_benchmark_runs({"benchmark": {"runs": [{"graph": "G2"}]}})[0]
    assert not hc.check_benchmark_runs({"benchmark": {"runs": []}})[0]
    assert not hc.check_benchmark_runs({})[0]


def test_experiments_available():
    ok, detail = hc.check_experiments_available({"experiments": [{"id": "E003", "available": True}]})
    assert ok and "1 of 1" in detail
    ok, detail = hc.check_experiments_available({"experiments": [{"id": "E003", "available": True}, {"id": "E004", "available": False}]})
    assert not ok and "E004" in detail
    assert not hc.check_experiments_available({"experiments": []})[0]


def test_egfr_predictions_novel():
    assert hc.check_egfr_predictions_novel({"predicted": [{"known_in": []}, {"known_in": []}]})[0]
    assert not hc.check_egfr_predictions_novel({"predicted": [{"known_in": []}, {"known_in": ["pharos"]}]})[0]
    assert not hc.check_egfr_predictions_novel({"predicted": []})[0]
    # after CC-30 the shown list is 'novel'; recovered known links elsewhere do not count
    ok, detail = hc.check_egfr_predictions_novel({"novel": [{"known_in": []}], "predicted": [{"known_in": ["pharos"]}]})
    assert ok and detail.startswith("novel")


def test_demo_first_row():
    assert hc.check_demo_first_row({"rows": [{"knowledge_level": "knowledge_assertion"}]})[0]
    assert not hc.check_demo_first_row({"rows": [{"knowledge_level": "text_co_occurrence", "source": "TIN-X"}]})[0]
    assert not hc.check_demo_first_row({"rows": []})[0]
    assert not hc.check_demo_first_row({"rows": [{"disease": "x"}]})[0]


def test_byo_built():
    assert hc.check_byo_built({"byo": {"built": {"nodes": 3, "edges": 2}}})[0]
    assert not hc.check_byo_built({"byo": {"built": None}})[0]
    assert not hc.check_byo_built({"byo": {}})[0]


def test_schema_terms():
    assert hc.check_schema_terms({"schema": {"classes": [{"name": "Gene", "terms": ["a"]}]}})[0]
    ok, detail = hc.check_schema_terms({"schema": {"classes": [{"name": "Gene", "terms": ["a"]}, {"name": "Disease", "terms": []}]}})
    assert not ok and "Disease" in detail
    assert not hc.check_schema_terms({"schema": None})[0]


def test_schema_terms_passes_a_class_biolink_leaves_unmapped_and_names_it(tmp_path):
    """G1-ES-07 (owner decision 14 Nov): a class with no terms passes only when BioLink defines no exact mapping for it."""
    res = {"schema": {"classes": [{"name": "Gene", "terms": ["a"]}, {"name": "SmallMolecule", "terms": []},
                                  {"name": "Disease", "terms": []}]}}
    ok, detail = hc.check_schema_terms(res, unmapped={"SmallMolecule"})
    assert not ok and "Disease" in detail.split("; BioLink")[0] and "SmallMolecule" in detail.split("; BioLink")[1]
    res["schema"]["classes"].pop()
    ok, detail = hc.check_schema_terms(res, unmapped={"SmallMolecule"})
    assert ok and "BioLink defines no exact mapping for: ['SmallMolecule']" in detail
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"classes": [{"name": "SmallMolecule", "is_biolink": True, "exact_mappings": []},
                                            {"name": "Gene", "is_biolink": True, "exact_mappings": ["SO:0000704"]},
                                            {"name": "Odd", "is_biolink": False, "exact_mappings": []}]}))
    assert hc.biolink_unmapped(card) == {"SmallMolecule"}
    assert hc.biolink_unmapped(tmp_path / "missing.json") == set()
    assert hc.biolink_unmapped() >= set()                              # the committed card reads without error


def test_v3_no_developer_text():
    assert hc.check_v3_no_developer_text(b"<html>Benchmark results are not loaded on this server</html>")[0]
    assert not hc.check_v3_no_developer_text("<p>The Mac writes them with run.py --tag frozen_a</p>")[0]


def test_verdict():
    assert hc.verdict("x", True, "d", pending={"x": "CC-1"}) == "PASS x: d"
    assert hc.verdict("x", False, "d", pending={"x": "CC-1"}) == "EXPECTED-FAIL(CC-1) x: d"
    assert hc.verdict("x", False, "d", pending={}) == "FAIL x: d"
    assert set(hc.PENDING) == set()                     # nothing is waiting for a packet: schema_terms is a plain FAIL since the 11 Nov redeploy
    # 11 Nov: byo_built is a blocker (the image lacked examples/; aws/Dockerfile now copies it), never an expected failure
    assert hc.verdict("byo_built", False, "byo.built = null").startswith("FAIL byo_built")


def test_run_assertions_never_aborts(capsys):
    pages = {"/api/results": {"benchmark": {"runs": [1]}, "experiments": [{"id": "E003", "available": True}],
                              "schema": {"classes": [{"name": "Gene", "terms": ["t"]}]}},
             "/api/pharos/card/NCBIGene:1956": {"predicted": [{"known_in": []}]},
             "/api/generalization": {"byo": {"built": {"nodes": 1}}}}

    def fetch(path):
        if path == "/v3":
            return b"<html></html>"
        if path not in pages:
            raise OSError("down")          # demo_1 unreachable: that one check fails, the rest still run
        return json.dumps(pages[path]).encode()
    lines = hc.run_assertions(fetch)
    assert len(lines) == 8                 # FIX1 F7: + calibration_matches_prune_report (its file is not served here: FAIL)
    assert sum(l.startswith("PASS") for l in lines) == 6
    bad = [l for l in lines if not l.startswith("PASS")]
    assert bad[0].startswith("FAIL demo_1_first_row_not_text_mined: error")
    assert bad[1].startswith("FAIL calibration_matches_prune_report: error")


def test_demo_first_row_accepts_merged_list_cells():
    # CC-31a merges one answer's rows, so knowledge_level is a list ordered by evidence (curated first) on the live site
    ok, _ = hc.check_demo_first_row({"rows": [{"knowledge_level": ["knowledge_assertion", "text_co_occurrence"], "source": ["CTD", "TIN-X"]}]})
    assert ok
    ok, _ = hc.check_demo_first_row({"rows": [{"knowledge_level": ["text_co_occurrence"], "source": ["TIN-X"]}]})
    assert not ok
    ok, _ = hc.check_demo_first_row({"rows": [{"knowledge_level": "knowledge_assertion"}]})
    assert ok


def test_deploy_sync_list_ships_linkpred_results():
    """CS-1: the S3 step of aws/deploy_app.sh copies outputs/linkpred/*.json and *.md to app/data/, which user_data syncs
    (as a whole directory) to /data, the Numbers page root (TMKG_PHAROS_ROOT)."""
    script = (ROOT / "aws/deploy_app.sh").read_text()
    loop = re.search(r"^for f in (.*?); do", script, re.S | re.M).group(1)
    assert "outputs/linkpred/*.json" in loop and "outputs/linkpred/*.md" in loop
    assert 'aws s3 sync "s3://BUCKET_NAME/app/data/" /srv/data/' in (ROOT / "aws/user_data.sh.tmpl").read_text()


# ---- PK15: Amazon Neptune and the front door (GET only unless --neptune)
def test_parse_args_knows_neptune():
    assert hc.parse_args(["--get-only", "https://x.cloudfront.net/"]) == ("https://x.cloudfront.net", True, False)
    assert hc.parse_args(["--neptune"]) == (None, False, True)


def test_neptune_status_check():
    for st in hc.NEPTUNE_STATES:
        assert hc.check_neptune_status({"state": st})[0]
    assert not hc.check_neptune_status({"state": "stopped"})[0]                 # the host's word, not one of the five
    assert not hc.check_neptune_status({"state": "HTTP 404"})[0]
    man = {"nodes": 11, "edges": 29}
    assert hc.check_neptune_status({"state": "asleep", "loaded": {"nodes": 11, "links": 29}}, man)[0]
    ok, detail = hc.check_neptune_status({"state": "available", "loaded": {"nodes": 11, "links": 28}}, man)
    assert not ok and "manifest" in detail
    for st in ("available", "asleep", "waking", "unavailable"):                  # FIX2 F5 / S17 item 1: absent in every state
        ok, detail = hc.check_neptune_status({"state": st}, man)                    # on the server: "not compared", not a FAIL
        assert ok and "not compared" in detail, (st, detail)
    assert hc.check_neptune_status({"state": "not_configured"}, man)[0]          # nothing to compare


def test_front_door_check():
    assert hc.is_cloudfront("https://d1abc.cloudfront.net") and not hc.is_cloudfront("https://tmkg.16-178-197-241.sslip.io")
    assert hc.check_front_door("https://d1abc.cloudfront.net", "1.1 d1abc.cloudfront.net (CloudFront)", 200)[0]
    assert not hc.check_front_door("https://d1abc.cloudfront.net", "", 200)[0]
    assert not hc.check_front_door("https://d1abc.cloudfront.net", "1.1 x.cloudfront.net (CloudFront)", 403)[0]
    ok, detail = hc.check_front_door("https://tmkg.16-178-197-241.sslip.io", "", 404)
    assert ok and "skipped" in detail


def test_neptune_ask_line():
    assert hc.neptune_ask_line(200, {"neptune": {"answered": True, "ms": 812, "match": {"links_identical": 3}}}).startswith(
        "PASS neptune_ask: answered=True ms=812 match=")
    assert "answered=False state=asleep" in hc.neptune_ask_line(200, {"neptune": {"answered": False, "state": "asleep", "note": "n"}})
    assert hc.neptune_ask_line(500, {}).startswith("FAIL neptune_ask")
    assert hc.neptune_ask_line(200, {"rows": []}).startswith("FAIL neptune_ask")   # an old server: no neptune block


def test_neptune_assertions_never_query_neptune_without_the_flag(capsys):
    asked = []

    def fetch_full(path, data=None):
        asked.append((path, data))
        if path == "/api/neptune/status":
            return 200, {}, json.dumps({"state": "available", "loaded": {"nodes": 2, "links": 3}}).encode()
        if path == "/downloads/neptune/manifest.json":
            return 200, {}, json.dumps({"counts": {"nodes": 2, "edges": 3}}).encode()
        if path == "/":
            return 200, {"via": "1.1 d1.cloudfront.net (CloudFront)"}, b"<html>"
        if path == "/downloads/kgx/SHA256SUMS":
            return 200, {}, b"abc  nodes.tsv.gz\n"
        if path == "/api/pharos/ask":
            return 200, {}, json.dumps({"neptune": {"answered": True, "ms": 5, "match": {}}}).encode()
        raise OSError(path)
    lines = hc.neptune_assertions(fetch_full, "https://d1.cloudfront.net")
    assert [l.split(":")[0] for l in lines] == ["PASS neptune_status", "PASS front_door"]
    assert all(p != "/api/pharos/ask" and d is None for p, d in asked)            # GET only
    lines = hc.neptune_assertions(fetch_full, "https://d1.cloudfront.net", ask_neptune=True, question="Q?")
    assert lines[-1].startswith("PASS neptune_ask: answered=True") and asked[-1] == ("/api/pharos/ask", {"question": "Q?", "backend": "neptune"})
    lines = hc.neptune_assertions(lambda p, d=None: (_ for _ in ()).throw(OSError("down")), "https://x")
    assert all(l.startswith("FAIL") for l in lines) and len(lines) == 2          # an error is a FAIL line, never a crash


def test_call_full_returns_status_lower_case_headers_and_body(monkeypatch):
    import io
    import urllib.error

    class R(io.BytesIO):
        status = 200
        headers = {"Via": "1.1 x.cloudfront.net (CloudFront)", "Content-Type": "text/html"}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    seen = []
    monkeypatch.setattr(hc.urllib.request, "urlopen", lambda req, timeout=90: seen.append(req) or R(b"<html>"))
    assert hc.call_full("/") == (200, {"via": "1.1 x.cloudfront.net (CloudFront)", "content-type": "text/html"}, b"<html>")
    assert seen[0].data is None
    hc.call_full("/api/pharos/ask", {"q": 1})
    assert json.loads(seen[1].data) == {"q": 1} and seen[1].get_header("Content-type") == "application/json"

    def boom(req, timeout=90):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {"X-A": "b"}, io.BytesIO(b"no"))
    monkeypatch.setattr(hc.urllib.request, "urlopen", boom)
    assert hc.call_full("/x") == (404, {"x-a": "b"}, b"no")


def test_hosted_check_lives_where_the_public_tree_keeps_it():
    """FIX6 M1: the public tree leaves out docs/claude_science/, so the maintained copy is scripts/hosted_check.py; no EXCLUDES
    pattern of scripts/make_public_tree.sh covers it, and the old path only forwards to it (private repository)."""
    import fnmatch
    sh = (ROOT / "scripts/make_public_tree.sh").read_text()
    block = sh.split("EXCLUDES=(", 1)[1].split("\n)", 1)[0]
    pats = [l.strip().strip('"') for l in block.splitlines() if l.strip() and not l.strip().startswith("#")]
    rel = "scripts/hosted_check.py"
    assert (ROOT / rel).is_file() and pats
    assert not any(fnmatch.fnmatch(rel, p) or rel.startswith(p.rstrip("/") + "/") for p in pats)
    old = ROOT / "docs/claude_science/trialmindkg-continuity/hosted_check.py"
    if old.is_file():                                    # private repository only; the public tree leaves the folder out
        text = old.read_text()
        assert "scripts" in text and "runpy.run_path" in text and "def check_" not in text
    assert hc.SCHEMA_CARD_JSON == ROOT / "outputs" / "schema_card.json"
