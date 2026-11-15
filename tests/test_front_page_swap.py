"""The front-page swap and the image fix of 11 Nov 2026 (review findings U2 and U4).

U2: there is one front page. "/" serves app_page_v3.html with the worked examples injected (the page with stored answers
and a bottom line); the first page (app_page.html) is kept at /classic for the regression journey only; /v2 is gone; a link
to the old address /v3, shared before the swap, redirects to "/" with its query. Nothing a visitor sees points at the
other pages, the README links only the root, and the click gate and the journeys run on "/".
U4: the Docker image contains examples/, so the bring-your-own-source example builds on the hosted site; the hosted check
treats a failed build as a plain FAIL.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / "pipelines"
HARNESS = ROOT / "docs" / "ux" / "harness_v3"
sys.path.insert(0, str(PIPE))
sys.path.insert(0, str(ROOT / "scripts"))   # hosted_check.py (FIX6 M1)

FRONT_FILE, CLASSIC_FILE = PIPE / "app_page_v3.html", PIPE / "app_page.html"
OTHER_PAGES = ("/classic", "/v2", "/v3")


def client(**kw):
    """A TestClient on the real app with the offline model stand-in. Takes TestClient keyword arguments; returns the client."""
    from fastapi.testclient import TestClient
    import app as appmod
    from kg_query import KGIndex
    from nim import NIM
    appmod._state.update(kg=KGIndex(), nim=NIM(backend="offline"))
    return TestClient(appmod.app, **kw)


# ---------------------------------------------------------------- U2: routes

def test_root_serves_the_page_with_stored_answers_and_worked_examples():
    r = client().get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers.get("cache-control") == "no-cache"
    assert "/api/stored_answers" in r.text and "bottomline" in r.text          # what the first page lacks (U2)
    assert "/*__PERSONA_EXAMPLES__*/null" in FRONT_FILE.read_text(encoding="utf-8")
    assert "/*__PERSONA_EXAMPLES__*/null" not in r.text                        # the examples were injected
    examples = json.loads(re.search(r"const EXAMPLES = (\{.*?\}) \|\| \{\};", r.text, re.S).group(1))
    assert len(examples) == 5 and all(examples.values())


def test_classic_serves_the_first_page_unchanged():
    r = client().get("/classic")
    assert r.status_code == 200 and r.headers.get("cache-control") == "no-cache"
    assert r.text == CLASSIC_FILE.read_text(encoding="utf-8")
    assert "/api/stored_answers" not in r.text


def test_v2_is_gone():
    import app as appmod
    assert client().get("/v2").status_code == 404
    assert "/v2" not in {getattr(r, "path", "") for r in appmod.app.routes}
    assert "app_page_v2" not in (PIPE / "app.py").read_text(encoding="utf-8")


def test_old_v3_links_redirect_to_root_with_their_query():
    c = client(follow_redirects=False)
    r = c.get("/v3")
    assert r.status_code == 307 and r.headers["location"] == "/"
    r = c.get("/v3?persona=clinician&node=NCBIGene%3A1956")
    assert r.status_code == 307 and r.headers["location"] == "/?persona=clinician&node=NCBIGene%3A1956"
    landed = client().get("/v3?node=NCBIGene%3A1956")                          # followed, as a browser does
    assert landed.status_code == 200 and landed.url.path == "/" and "node=NCBIGene%3A1956" in str(landed.url)
    assert landed.text == client().get("/").text


def test_exactly_two_page_routes_and_both_set_the_visitor_cookie():
    import app as appmod
    assert appmod.PAGE_PATHS == {"/", "/classic"}
    html_routes = {r.path for r in appmod.app.routes
                   if getattr(r, "response_class", None) is appmod.HTMLResponse and not r.path.startswith("/api/")}
    assert html_routes == {"/", "/classic"}
    for path in sorted(appmod.PAGE_PATHS):
        r = client().get(path)                                                 # a new client: no cookie yet
        assert appmod._VID.match(r.cookies.get(appmod.VISITOR_COOKIE) or ""), path
    assert appmod.VISITOR_COOKIE not in client(follow_redirects=False).get("/v3").headers.get("set-cookie", "")


def test_no_page_and_no_readme_link_points_at_the_other_pages():
    for f in (FRONT_FILE, CLASSIC_FILE):
        t = f.read_text(encoding="utf-8")
        for other in OTHER_PAGES:
            assert not re.search(r"""["'`(=]%s(["'`?#)/]|\b)""" % re.escape(other), t), (f.name, other)
        assert "location.pathname" in t                                        # shared links are built from the page's own address
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://tmkg.16-178-197-241.sslip.io/ " in readme
    assert not re.search(r"sslip\.io/(v2|v3|classic)\b", readme)
    assert not re.search(r"localhost:8000/(v2|v3|classic)\b", readme)


# ---------------------------------------------------------------- U2: the click gate and the journeys follow the swap

def test_journeys_run_on_the_front_page_and_the_regressions_are_repointed():
    sh = (HARNESS / "journeys.sh").read_text(encoding="utf-8")
    runs = dict(re.findall(r"^run (\S+) (\S+) ", sh, re.M))
    assert len(runs) == 14                                                     # PK3: + J10; PK12: + J11 (axe)
    front = {name: page for name, page in runs.items() if name.startswith("J")}
    assert len(front) == 12 and set(front.values()) == {"/"}
    assert runs["R1_v1_unchanged"] == "/classic"                               # the first page: regression journey only
    assert runs["R2_old_v3_link"] == "/v3"                                     # an old shared link must still open the card
    assert "/v2" not in runs.values()
    assert not (HARNESS / "R2_v2_unchanged.json").exists()                     # the result of the removed /v2 journey


def test_click_gate_clicks_only_the_front_page_and_checks_the_routes():
    js = (HARNESS / "click_gate.js").read_text(encoding="utf-8")
    assert 'const FRONT = "/", PAGES = [FRONT];' in js
    assert '["/", "/v3"]' not in js and 'openPage(bridge, "/v3"' not in js and 'page: "/v3"' not in js
    handlers = re.search(r"const HANDLERS = \{\[FRONT\]: \[(.*?)\]\};", js).group(1)
    front = FRONT_FILE.read_text(encoding="utf-8")
    for name in re.findall(r'"(\w+)"', handlers):                              # the gate's handler list is true to the front page
        assert re.search(rf"function\s+{name}\s*\(", front), name
    assert {"runExample", "trialQuery"} <= set(re.findall(r'"(\w+)"', handlers))
    for must in ('bridge.ask("GET", "/classic")', 'bridge.ask("GET", "/v2")', "v2.status === 404", 'bridge.ask("GET", "/v3?node=NCBIGene%3A1956")'):
        assert must in js, must
    assert '"routes,pills,chips,first,forms,repeat,examples,limit,neptune"' in js    # the route checks run by default
    bridge = (HARNESS / "bridge.py").read_text(encoding="utf-8")
    assert "url=str(r.url)" in bridge                                          # the gate reads where a redirect landed
    run_page = (HARNESS / "run_page_v3.js").read_text(encoding="utf-8")
    assert "const url = BASE + landed + hash;" in run_page                     # the window gets the address after the redirect


JSDOM_DIR = Path(os.environ.get("TMKG_JSDOM_DIR", "/tmp/tmkg/jsdom"))
NODE = shutil.which("node")
needs_jsdom = pytest.mark.skipif(not NODE or not (JSDOM_DIR / "node_modules" / "jsdom").exists(), reason="node + jsdom not available")


@needs_jsdom
def test_click_gate_route_checks_pass_on_the_real_app(tmp_path):
    """The gate's own route section against the real app: "/" is the page with stored answers and worked examples, /classic
    opens, /v2 answers 404, an old /v3 link lands on "/" with its query."""
    out = tmp_path / "gate.json"
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"), GATE_ONLY="routes")
    r = subprocess.run([NODE, str(HARNESS / "click_gate.js"), sys.executable, str(HARNESS / "bridge.py"), str(out)], cwd=JSDOM_DIR,
                       env=env, capture_output=True, text=True, timeout=300)
    assert out.exists(), r.stderr[-2000:]
    d = json.loads(out.read_text())
    assert r.returncode == 0 and d["failed"] == 0, [(x["page"], x["why"]) for x in d["rows"] if not x["pass"]]
    assert [x["page"] for x in d["rows"]] == ["/", "/classic", "/v2", "/v3"]


@needs_jsdom
def test_an_old_v3_link_opens_the_front_page_at_root_in_the_journey_runner():
    """run_page_v3.js on /v3?persona=clinician (a link shared before the swap): the window ends on "/" with the query, runs
    the front page without script errors and shows the role the link named."""
    acts = json.dumps([{"wait": 1500}, {"snap": "landed"}])
    env = dict(os.environ, NODE_PATH=str(JSDOM_DIR / "node_modules"), TMKG_LLM_BACKEND="offline")
    r = subprocess.run([NODE, str(HARNESS / "run_page_v3.js"), sys.executable, str(HARNESS / "bridge.py"), "/v3", "?persona=clinician", acts],
                       cwd=JSDOM_DIR, env=env, capture_output=True, text=True, timeout=300)
    d = json.loads(r.stdout)
    assert "harness_error" not in d, d.get("harness_error")
    assert d["page"] == "/v3" and d["landed"] == "/?persona=clinician"
    assert d["script_errors"] == 0, d["errors"]
    assert d["snapshots"][0]["persona"]                                        # the role named in the link is set

# ---------------------------------------------------------------- U4: the image holds what the Generalization page builds from

def copy_sources():
    """The first argument of every COPY line in aws/Dockerfile (paths relative to the repo root). Returns a list."""
    docker = (ROOT / "aws" / "Dockerfile").read_text(encoding="utf-8")
    return [line.split()[1] for line in docker.splitlines() if line.startswith("COPY ")]


def test_dockerfile_copies_the_example_and_nothing_ignores_it():
    assert "COPY examples ./examples\n" in (ROOT / "aws" / "Dockerfile").read_text(encoding="utf-8")
    ignore = (ROOT / "aws" / "Dockerfile.dockerignore").read_text(encoding="utf-8").split()
    assert not [i for i in ignore if i.startswith("examples") or i in ("**/*.gz", "*.gz", "**/*.yaml")]
    import usability
    card = usability._byo(ROOT)["card"]                                         # the file the page builds from
    assert card.startswith("examples/") and (ROOT / card).exists()
    tracked = subprocess.run(["git", "ls-files", "examples"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if tracked:                                                                # a git checkout: both files are committed
        assert {card, "examples/byo_source/screen.tsv.gz"} <= set(tracked)


def test_the_example_builds_from_the_files_the_image_holds(tmp_path):
    """The hosted failure of 11 Nov, reproduced: a tree holding only what aws/Dockerfile copies (without the big outputs/ and
    static/, which the example does not read) must build the bring-your-own-source example. Without `COPY examples` this
    ends with byo.built = null and "No such file or directory: ... examples/byo_source/lab_screen.yaml"."""
    skip = {"outputs", "static", "requirements.txt"}
    for src in copy_sources():
        if src in skip:
            continue
        s, d = ROOT / src, tmp_path / src
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(s, d)
    # as in the image: only the committed BioLink copy; data/external holds the download record alone (FIX4), no downloaded file
    assert [p.name for p in (tmp_path / "data" / "external").rglob("*")] == ["DATA_MANIFEST.tsv"]
    code = ("import json, sys; sys.path.insert(0, 'pipelines'); import usability; "
            "print(json.dumps(usability.generalization('.')['byo']))")
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    byo = json.loads(out.stdout.strip().splitlines()[-1])
    assert byo["reason"] == "" and byo["built"] is not None, byo["reason"]
    assert byo["built"]["nodes"] > 0 and byo["built"]["edges"] > 0 and byo["built"]["problems"] == []
    assert "SYNTHETIC" in byo["label"]                                         # rule 4: the example stays labelled
    here = json.loads(json.dumps(__import__("usability")._byo(ROOT)))         # the same counts as in the checkout
    assert here["built"] == byo["built"]


def test_hosted_check_treats_a_failed_example_build_as_a_blocker():
    import hosted_check as hc
    assert "byo_built" not in hc.PENDING
    ok, detail = hc.check_byo_built({"byo": {"built": None, "reason": "could not be built here: FileNotFoundError"}})
    assert not ok and hc.verdict("byo_built", ok, detail).startswith("FAIL byo_built")
    ok, detail = hc.check_byo_built({"byo": {"built": {"nodes": 9, "edges": 6}}})
    assert ok and hc.verdict("byo_built", ok, detail).startswith("PASS byo_built")
