"""FIX4 (owner decision 15 Nov 2026; S03 finding A, outputs/review/S03_verify_2026-11-12.md): the server image carries the
experiment log, the download record of the reference files and the records of the assembled graphs, so the hosted
"What the agent did" log and the all-layer graph numbers are the same as on a full copy of the repository.

The image file set is simulated the way S03 did it: the committed files (`git ls-files`, i.e. `git archive HEAD` once the
tree is committed), minus what aws/Dockerfile.dockerignore drops, placed where the COPY lines of aws/Dockerfile put them.
Nothing is built and no Docker is needed.

  image_files(dockerfile, ignore, files) -> {path in the image (relative to /app): path in the repository}
  dropped(path, ignore)                  -> True when the dockerignore keeps the path out of the build context

Tests
  - the Dockerfile copies the five files and the dockerignore lets them through; everything else it dropped is still dropped,
    and the new exceptions let in exactly those files (no folder, no pattern);
  - through the test client on the simulated image: /api/agent-log has the same entries and scientist decisions as the full
    copy at the same commit (decisions counted from loop/ledger.tsv), no undated entry (the image before this change had
    some), nothing "missing"; /api/graph_numbers has non-null all_layers, also without the shipped totals file;
  - R11 (no file path, run tag, graph code, packet id or model-operations word) on the full JSON of both endpoints, and,
    rendered in jsdom against the app of the simulated image, on the first screen, the agent tab and the Numbers tab.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "tests"))
import routes_agent as ra  # noqa: E402
import routes_answers  # noqa: E402

DOCKERFILE = (ROOT / "aws" / "Dockerfile").read_text(encoding="utf-8")
IGNORE = (ROOT / "aws" / "Dockerfile.dockerignore").read_text(encoding="utf-8")
SHIPPED = ["loop/ledger.tsv", "data/external/DATA_MANIFEST.tsv", "outputs/g0/summary.md", "outputs/g2/summary.md",
           "outputs/g3/summary.md"]
# The two COPY lines this change added; removing them and the dockerignore exceptions gives the image as it was before.
FIX4_COPY = ["COPY loop/ledger.tsv ./loop/ledger.tsv", "COPY data/external/DATA_MANIFEST.tsv ./data/external/DATA_MANIFEST.tsv"]
# R11 as S03 ran it (logs/s03_probes_2026-11-12.py.txt): the union of the committed wording tests plus run tags and paths.
S03_BANNED = re.compile(r"(\.tsv|\.ya?ml|\.py\b|\.json\b|\.sqlite|data/|outputs/|pipelines/|examples/|/tmp|/Users/|\bG[0-4]\b|\bPK\d|\bCC-\d|\bCS-\d"
                        r"|\bNIM\b|stand-in|fallback|snapshot|ref_file|_layer\b|chembl37|@frozen|@diag|\bdiag_|kfold)")


def _tracked():
    """The committed files that exist in this checkout ([] when this is not a git checkout)."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    return [f for f in out.split("\0") if f and (ROOT / f).is_file()]


TRACKED = _tracked()
needs_git = pytest.mark.skipif(not TRACKED, reason="not a git checkout: the committed file set is unknown")


# ------------------------------------------------------------------ the dockerignore and COPY rules, as Docker reads them
def _pattern(p):
    """A dockerignore pattern -> a regex for one path: '*' and '?' stay inside one folder, '**' crosses folders."""
    out, i = "", 0
    while i < len(p):
        if p.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif p.startswith("**", i):
            out, i = out + ".*", i + 2
        elif p[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif p[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(p[i]), i + 1
    return re.compile(out + r"\Z")


def _rules(ignore):
    rules = []
    for line in ignore.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            neg = line.startswith("!")
            rules.append((neg, _pattern(line.lstrip("!").strip().strip("/"))))
    return rules


def dropped(path, ignore=IGNORE):
    """Takes a path relative to the repo root. Returns True when the dockerignore keeps it out of the build context: the
    last pattern that matches the path or one of its folders decides, a '!' pattern lets it back in (Docker's rule)."""
    parts = path.split("/")
    prefixes = ["/".join(parts[:k]) for k in range(1, len(parts) + 1)]
    out = False
    for neg, rx in _rules(ignore):
        if any(rx.match(p) for p in prefixes):
            out = not neg
    return out


def image_files(dockerfile=DOCKERFILE, ignore=IGNORE, files=None):
    """Takes the Dockerfile text, the dockerignore text and the repository files. Returns {path in the image under /app:
    path in the repository} for every file a COPY line puts there from the build context."""
    context = [f for f in (TRACKED if files is None else files) if not dropped(f, ignore)]
    out = {}
    for line in dockerfile.splitlines():
        if not line.startswith("COPY "):
            continue
        src, dest = line.split()[1:3]
        src = src.rstrip("/")
        into = dest in (".", "./") or dest.endswith("/")              # a folder: the file keeps its own name
        dest = re.sub(r"^\.(?:/|$)", "", dest).rstrip("/")            # relative to WORKDIR /app
        prefix = dest + "/" if dest else ""
        for f in context:
            if f == src:
                out[prefix + f.split("/")[-1] if into else dest] = f
            elif f.startswith(src + "/"):
                out[prefix + f[len(src) + 1:]] = f
    return out


def _before_fix4():
    """The Dockerfile and dockerignore as they were before this change (no FIX4 COPY lines, no '!' exceptions)."""
    docker = "\n".join(line for line in DOCKERFILE.splitlines() if line.strip() not in FIX4_COPY)
    ignore = "\n".join(line for line in IGNORE.splitlines() if not line.strip().startswith("!"))
    return docker, ignore


def materialize(mapping, dest, copy_prefixes=()):
    """Takes {image path: repo path} and a folder. Writes the image tree there: links to the repository files, real copies
    under copy_prefixes (code that finds its root from its own file must sit in the tree). Returns the folder."""
    for d, s in mapping.items():
        p = dest / d
        p.parent.mkdir(parents=True, exist_ok=True)
        if d.startswith(tuple(copy_prefixes)):
            shutil.copy2(ROOT / s, p)
            continue
        try:
            p.symlink_to(ROOT / s)
        except OSError:
            shutil.copy2(ROOT / s, p)
    return dest


@pytest.fixture(scope="module")
def trees(tmp_path_factory):
    """{'full': every committed file, 'image': the image now, 'before': the image before this change}."""
    if not TRACKED:
        pytest.skip("not a git checkout")
    base = tmp_path_factory.mktemp("fix4")
    return dict(full=materialize({f: f for f in TRACKED}, base / "full"),
                image=materialize(image_files(), base / "image"),
                before=materialize(image_files(*_before_fix4()), base / "before"))


@pytest.fixture(scope="module")
def app_module():
    import app as A
    return A


def _endpoints(A, root, monkeypatch):
    """GET /api/agent-log and /api/graph_numbers through the test client with `root` as the tree the two endpoints read."""
    from fastapi.testclient import TestClient
    monkeypatch.setattr(ra, "ROOT", root)
    monkeypatch.setattr(routes_answers, "ROOT", root)
    c = TestClient(A.app)
    log, nums = c.get("/api/agent-log"), c.get("/api/graph_numbers")
    assert log.status_code == 200 and nums.status_code == 200
    return log.json(), nums.json()


# ------------------------------------------------------------------ F1: the files are in the image
def test_the_dockerfile_copies_the_five_files():
    sources = [line.split()[1] for line in DOCKERFILE.splitlines() if line.startswith("COPY ")]
    for f in SHIPPED:
        assert any(f == s or f.startswith(s.rstrip("/") + "/") for s in sources), f"{f} is under no COPY source"
        assert (ROOT / f).is_file(), f
    for line in FIX4_COPY:
        assert line in DOCKERFILE.splitlines()
    assert "COPY loop ./loop" not in DOCKERFILE and "COPY data/external ./" not in DOCKERFILE   # only the files, not the folders


def test_the_dockerignore_lets_the_five_files_through_and_keeps_their_folders_out():
    for f in SHIPPED:
        assert not dropped(f), f
    folders = {line.strip() for line in IGNORE.splitlines() if line.strip() and not line.startswith(("!", "#"))}
    assert {"data/external", "outputs/g0", "outputs/g1", "outputs/g2", "outputs/g3", "outputs/g4", "data/kg", "data/pharos",
            "data/internal", ".env", "aws/config.env", ".git", "logs", "raw", "raw_text"} <= folders
    # files that exist only on the Mac stay out: the 20 GB card index, the graph tensors, the downloaded reference data
    for f in ("data/kg/pharos_index/card_index_v3.sqlite", "data/kg/pharos/edges.parquet", "data/pharos/dto.tsv.gz",
              "data/external/chembl/chembl_37_sqlite.tar.gz", "data/external/mondo/mondo.json", "data/external/README.md",
              "outputs/g0/heterodata.pt", "outputs/g2/heterodata.pt", "outputs/g3/heterodata.pt", "outputs/g1/summary.md",
              "outputs/g4/summary.md", "outputs/g2/summary.md.bak", "data/internal/x.csv", "data/snapshots_llm/a.json",
              "logs/run.log", "pipelines/__pycache__/app.cpython-311.pyc", ".env"):
        assert dropped(f), f


@needs_git
def test_the_new_rules_let_in_exactly_the_five_files_and_nothing_else():
    now, before = image_files(), image_files(*_before_fix4())
    assert set(before) <= set(now)
    assert sorted(set(now) - set(before)) == sorted(SHIPPED)
    for d in now:                                    # every other dropped folder is still dropped
        assert not d.startswith(("data/kg/", "data/pharos/", "data/internal/", "data/snapshots", "raw/", "raw_text/", "logs/",
                                 "outputs/g1/", "outputs/g4/", ".git/")), d
        if d.startswith(("loop/", "data/external/", "outputs/g0/", "outputs/g2/", "outputs/g3/")):
            assert d in SHIPPED, d


# ------------------------------------------------------------------ F1: what the endpoints return on the simulated image
@needs_git
def test_the_image_agent_log_has_the_full_copys_entries_and_decisions(trees, app_module, monkeypatch):
    full = ra.agent_log(trees["full"])
    rows = ra.ledger_rows(trees["full"])
    decisions = sum(ra._is_scientist(r) for r in rows)          # counted from loop/ledger.tsv, not typed in
    assert rows and decisions > 0 and full["counts"]["scientist_decisions"] == decisions
    log, _ = _endpoints(app_module, trees["image"], monkeypatch)
    assert log["counts"] == full["counts"]
    assert log["counts"]["entries"] == len(full["entries"]) and log["counts"]["scientist_decisions"] == decisions
    assert [(e["date"], e["title"]) for e in log["entries"]] == [(e["date"], e["title"]) for e in full["entries"]]
    assert log["missing"] == [] and all(e["date"] for e in log["entries"])


@needs_git
def test_the_undated_entries_of_the_image_before_are_dated_now(trees, app_module, monkeypatch):
    """S03 saw 5 undated entries and 0 decisions on the image file set of 12 Nov; the same rule gives 6 at this commit."""
    before, _ = _endpoints(app_module, trees["before"], monkeypatch)
    undated = [e["title"] for e in before["entries"] if not e["date"]]
    assert undated and before["counts"]["scientist_decisions"] == 0
    assert "experiment log is not on this machine" in " ".join(before["missing"])
    log, _ = _endpoints(app_module, trees["image"], monkeypatch)
    dated = {e["title"]: e["date"] for e in log["entries"]}
    assert all(dated.get(t) for t in undated), [t for t in undated if not dated.get(t)]


@needs_git
def test_the_image_graph_numbers_have_all_layers(trees, app_module, monkeypatch, tmp_path):
    _, nums = _endpoints(app_module, trees["image"], monkeypatch)
    full = routes_answers.graph_numbers(trees["full"])
    assert nums["all_layers"] is not None and nums == full
    assert nums["added_layers"] and "Across all layers" in nums["sentence"]
    # the numbers come from the shipped summaries even without the shipped totals file
    bare = materialize({d: s for d, s in image_files().items() if d != routes_answers.TOTALS}, tmp_path / "bare")
    _, nums2 = _endpoints(app_module, bare, monkeypatch)
    assert nums2["all_layers"] == nums["all_layers"]


# ------------------------------------------------------------------ F2: R11 on what the two endpoints now return
def _strings(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _hits(obj):
    out = []
    for path, s in _strings(obj):
        out += [(path, m.group(0)) for m in S03_BANNED.finditer(s)] + [(path, t) for t in ra.internal_terms(s)]
    return out


@needs_git
def test_r11_on_the_full_json_of_both_endpoints_on_the_image(trees, app_module, monkeypatch):
    log, nums = _endpoints(app_module, trees["image"], monkeypatch)
    assert _hits(log) == [] and _hits(nums) == []
    assert "files" not in nums and all(not re.search(r"_|\.", n) for n in nums["added_layers"])   # S03 finding D stays fixed


@needs_git
def test_r11_on_the_agent_tabs_strip_on_the_image(trees):
    """The agent tab also prints /api/agent-loop's strip, which reads the same records; its `records` map (title -> file)
    is printed only inside the "For developers" fold (test_page_pk7b), so it is left out here as there."""
    import usability
    story = usability.agent_story(trees["image"])
    full = usability.agent_story(trees["full"])
    assert story["available"] and story["scientist_decisions"] == full["scientist_decisions"] > 0
    assert _hits({k: v for k, v in story.items() if k != "records"}) == []


def _render_tree(base):
    """The image tree for the page runner: the app and the harness are real copies, so both find this tree as their root."""
    return materialize(image_files(), base, copy_prefixes=("pipelines/", "docs/ux/harness_v3/"))


@needs_git
def test_r11_rendered_first_screen_agent_tab_and_numbers_on_the_image(tmp_path):
    import test_page_pk6 as pk6
    if not pk6.NODE or not (pk6.JSDOM_DIR / "node_modules" / "jsdom").exists():
        pytest.skip("node + jsdom not available")
    tree = _render_tree(tmp_path / "image")
    fx = pk6.extended_fixture(tmp_path / "fx")
    J = lambda e: {"eval": e}                                       # noqa: E731
    acts = [pk6.CHOOSE, J("[...document.querySelectorAll('[data-how]')].map(e => e.textContent).join(' | ')"), pk6.R11,
            {"click": "#tabJudges", "waitFor": "#alog li", "wait": 400}, {"click": "#alogmore", "wait": 200},
            J("document.querySelectorAll('#alog li').length"),
            J("[...document.querySelectorAll('#alog .adate')].filter(e => e.textContent === 'undated').length"),
            J("document.querySelectorAll('#agent-log .banner').length"), pk6.R11,
            {"click": '#agent-log [data-who="scientist"]', "wait": 200}, J("document.querySelectorAll('#alog li.decided').length"),
            pk6.R11, {"click": "#tabNumbers", "wait": 2500}, J("document.querySelector('#numbers').textContent.length"), pk6.R11]
    env = dict(os.environ, NODE_USE_ENV_PROXY="1", NODE_PATH=str(pk6.JSDOM_DIR / "node_modules"), TMKG_LLM_BACKEND="offline",
               TMKG_PHAROS_ROOT=str(fx), TMKG_PHAROS_INDEX="off", TMKG_NIM_PREFLIGHT="off", TMKG_WARM="off")
    H = tree / "docs" / "ux" / "harness_v3"
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "acts.json"
        f.write_text(json.dumps(acts), encoding="utf-8")
        r = subprocess.run([pk6.NODE, str(H / "run_page_v3.js"), pk6.PY, str(H / "bridge.py"), "/", "", "@" + str(f)],
                           capture_output=True, text=True, timeout=400, cwd=str(pk6.JSDOM_DIR), env=env)
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"page runner printed no JSON (exit {r.returncode}): {r.stderr[-2000:]}")
    assert "harness_error" not in d, d.get("harness_error")
    how, r11_first, n_all, n_undated, n_banner, r11_log, n_dec, r11_dec, n_numbers, r11_numbers = d["evals"]
    full = ra.agent_log(materialize({f: f for f in TRACKED}, tmp_path / "full"))
    nums = routes_answers.graph_numbers(tree)
    assert f"{nums['all_layers']['links']:,} links after adding" in how
    assert int(n_all) == full["counts"]["entries"] and int(n_dec) == full["counts"]["scientist_decisions"]
    assert int(n_undated) == 0 and int(n_banner) == 0 and int(n_numbers) > 0
    for hits in (r11_first, r11_log, r11_dec, r11_numbers):
        assert json.loads(hits)[0] == 0, hits
