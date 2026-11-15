"""scripts/review_pack.py (CC-R0, FINAL_SPRINT_PLAN section 6.1): the judge-review evidence pack."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import review_pack as rp  # noqa: E402


def fake_runner(outputs):
    """outputs: {first word(s) of the command: (code, text)}; records every call."""
    calls = []

    def run(argv, root=None, timeout=None):
        calls.append(argv)
        for key, val in outputs.items():
            if key in " ".join(argv):
                return val
        return 0, ""
    run.calls = calls
    return run


def make_root(tmp):
    """A small checkout with every file the pack reads."""
    (tmp / "outputs" / "pharos").mkdir(parents=True)
    (tmp / "outputs" / "g2").mkdir()
    (tmp / "outputs" / "g3").mkdir()
    (tmp / "outputs" / "linkpred").mkdir()
    (tmp / "outputs" / "schema_card.json").write_text(json.dumps({"classes": [1, 2], "predicates": [1], "problems": [],
        "layers": [{"layer": "pharos", "file": "outputs/pharos/summary.md", "source_version": "v1", "n_nodes": 10, "n_edges": 2000}]}))
    for rel in rp.REPORTS:
        (tmp / rel).write_text("# title\nline 2\n")
    for rel in rp.COMPARES:
        (tmp / rel).write_text("| G2 | +0.1 |\n")
    (tmp / "loop").mkdir()
    (tmp / "loop" / "ledger.tsv").write_text("date\tid\thypothesis\tpreregistered_threshold\tresult_file\tdecision\tjudged_by\n"
                                             "2026-11-01\tE1\th\tt\tf\tkept | yes\tScience\n2026-11-02\tE2\tshort\n")
    (tmp / "downloads.json").write_text(json.dumps({"base_url": "https://x/", "files": [{"path": "kgx/", "bytes": 5, "title": "KGX"}]}))
    (tmp / "pipelines").mkdir()
    (tmp / "pipelines" / "app.py").write_text('@app.get("/", response_class=X)\ndef a(): pass\n@app.post("/api/ask")\n'
                                               '@app.get("/api/status")\n')
    h = tmp / rp.HARNESS
    h.mkdir(parents=True)
    (h / "click_gate.json").write_text(json.dumps({"when": "t", "strict": False, "total": 3, "passed": 3, "failed": 0,
                                                   "error_answers": 0}))
    (h / "J1_x.json").write_text(json.dumps({"script_errors": 0, "errors": []}))
    (h / "J2_y.json").write_text(json.dumps({"script_errors": 1, "errors": ["timeout waiting for #pcardsec"]}))
    (h / "R1_z.json").write_text(json.dumps({"harness_error": "boom"}))
    return tmp


def test_run_returns_code_and_text_or_the_reason(tmp_path):
    code, text = rp.run([sys.executable, "-c", "print('hi')"], tmp_path)
    assert code == 0 and text.strip() == "hi"
    code, text = rp.run(["no_such_command_xyz"], tmp_path)
    assert code is None and "Error" in text


def test_git_section_lists_head_branch_and_dirty_files():
    r = fake_runner({"log -1": (0, "abc 2026 msg\n"), "rev-parse": (0, "b1\n"), "status": (0, " M a.py\n?? b.py\n")})
    lines = rp.git_section(runner=r)
    assert lines[0] == "- HEAD: `abc 2026 msg`" and lines[1] == "- branch: `b1`" and "2 dirty file(s)" in lines[2]
    r = fake_runner({"status": (0, "")})
    assert rp.git_section(runner=r)[2] == "- working tree: clean"


def test_pytest_summary_takes_the_last_count_line():
    out = "....\nFAILED tests/a.py::t\n=== 1 failed, 20 passed, 2 skipped in 3.1s ===\n"
    assert rp.pytest_summary(out) == "1 failed, 20 passed, 2 skipped in 3.1s"
    assert rp.pytest_summary("nothing") is None


def test_tests_section_skip_and_run():
    assert rp.tests_section(skip=True) == ["- not run (--skip-tests)"]
    r = fake_runner({"pytest": (1, "FAILED tests/a.py::t - x\n1 failed, 2 passed in 1s\n")})
    lines = rp.tests_section(runner=r)
    assert lines[0] == "- `pytest -q` exit 1: 1 failed, 2 passed in 1s" and "FAILED tests/a.py::t" in lines[1]
    assert "--ignore=tests/test_dev_controller.py" in r.calls[0]


def test_hosted_section_is_get_only_and_counts_verdicts():
    assert rp.hosted_section(skip=True) == ["- not run (--skip-hosted)"]
    r = fake_runner({"hosted_check": (1, "PASS a: x\nFAIL b: y\nEXPECTED-FAIL(CS-9) c: z\n")})
    lines = rp.hosted_section(runner=r)
    assert "--get-only" in r.calls[0] and lines[0] == "- exit 1; PASS: 1, FAIL: 1, EXPECTED-FAIL: 1"


def test_file_head_and_missing(tmp_path):
    (tmp_path / "a.md").write_text("\n".join(str(i) for i in range(20)))
    lines = rp.file_head(tmp_path / "a.md", n=3)
    assert lines[-4:] == ["0", "1", "2", "```"]
    assert rp.file_head(tmp_path / "none.md")[0].startswith("- MISSING")


def test_counts_ledger_compare_downloads_routes_gate_journeys(tmp_path):
    root = make_root(tmp_path)
    c = rp.counts_section(root)
    assert c[0] == "- schema card (`outputs/schema_card.json`): 2 classes, 1 predicates, 0 non-BioLink terms"
    assert "layer pharos: 10 nodes, 2,000 edges (v1;" in c[1] and "# title" in c
    led = rp.ledger_section(root)
    assert "| 2026-11-01 | E1 | kept / yes | Science |" in led and led[-1].endswith("header = 1): [3]")
    assert "| G2 | +0.1 |" in rp.compare_section(root)
    assert "| kgx/ | 5 | KGX |" in rp.downloads_section(root)
    assert rp.routes(root / "pipelines" / "app.py") == [("GET", "/"), ("POST", "/api/ask"), ("GET", "/api/status")]
    assert rp.routes_section(root)[0] == "- 3 routes: 1 pages/files, 2 API endpoints"
    assert "3 of 3 passed, 0 failed" in rp.click_gate_section(root)[0]
    j = rp.journeys_section(root)
    assert "| J2_y | 1 | timeout waiting for #pcardsec |" in j and "| R1_z | harness error | boom |" in j
    assert j[-1] == "- 1 of 3 result files with 0 script errors"


def test_missing_files_are_reported_never_filled(tmp_path):
    for fn in (rp.ledger_section, rp.downloads_section, rp.click_gate_section, rp.journeys_section):
        assert "MISSING" in fn(tmp_path)[0]
    assert "MISSING" in rp.counts_section(tmp_path)[0]


def test_build_pack_has_the_ten_sections_in_order(tmp_path):
    root = make_root(tmp_path)
    r = fake_runner({})
    text = rp.build_pack(root, skip_tests=True, skip_hosted=True, run_harness=True, runner=r, today="2026-11-11")
    heads = [ln for ln in text.splitlines() if ln.startswith("## ")]
    assert len(heads) == 10 and heads[0] == "## 1. Repository" and heads[-1].startswith("## 10. Journeys")
    assert text.startswith("# Review evidence pack, 2026-11-11") and ["make", "harness"] in r.calls


def section_headings(text):
    """Takes markdown text. Returns its '## ' heading lines outside ``` code fences (quoted file heads carry their
    own headings inside a fence; those are not sections of the pack)."""
    heads, fenced = [], False
    for ln in text.splitlines():
        if ln.startswith("```"):
            fenced = not fenced
        elif not fenced and ln.startswith("## "):
            heads.append(ln)
    return heads


def test_section_headings_skip_code_fences():
    text = "## 1. A\n\n```\n## quoted heading inside a file head\n```\n\n## 2. B\n"
    assert section_headings(text) == ["## 1. A", "## 2. B"]


def test_main_writes_the_pack(tmp_path):
    out = tmp_path / "pack.md"
    assert rp.main(["--skip-tests", "--skip-hosted", "--out", str(out)]) == 0
    heads = section_headings(out.read_text())
    assert len(heads) == 10 and heads[0] == "## 1. Repository" and heads[-1].startswith("## 10. Journeys")
