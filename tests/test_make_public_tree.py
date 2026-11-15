"""PK5 (finding SEC-13): scripts/make_public_tree.sh on a fixture git repository. The public tree holds the committed files
minus the exclude list, keeps only the full texts raw/index.csv lists as open access, is checked by prepublic_check.py, and the
source repository is left untouched."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_public_tree.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git")


def sh(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q")
    sh(r, "config", "user.name", "Test")
    sh(r, "config", "user.email", "noreply@example.org")
    write(r, "README.md", "# T\n\nSee [limits](docs/LIMITATIONS.md) and [code](pipelines/a.py).\n")
    write(r, "HOW_TO_RUN.md", "run it\n")
    write(r, "pipelines/a.py", "x = 1\n")
    write(r, "docs/LIMITATIONS.md", "limits; history in [the old plan](archive/OLD.md)\n")
    for rel in ("docs/archive/OLD.md", "docs/sessions/2026-11-01_s.md", "docs/handoff/H.md", "docs/packets_2026-11-11/PK5.md",
                "docs/claude_science/skill/SKILL.md", "docs/organizers/Brief.docx", "docs/REVIEW_JUDGE_2026-11-11.md",
                "docs/SPRINT_PLAN_2026-11-11.md", "docs/HANDOFF.md", "logs/run.log", "loop/state.yaml"):
        write(r, rel, "internal\n")
    write(r, "docs/organizers/BRIEF.md", "the brief\n")
    write(r, "loop/ledger.tsv", "date\tid\n")
    write(r, "raw/index.csv", "program,doc_type,local_path\n"
                              "P01,open-access full text,P01/publications/PMC_1_fulltext.md\n"
                              "P01,publication record + abstract,P01/publications/PMC_2_fulltext.md\n")
    write(r, "raw/P01/publications/PMC_1_fulltext.md", "open access\n")
    write(r, "raw/P01/publications/PMC_2_fulltext.md", "listed, but not as open access\n")
    write(r, "raw/P01/publications/PMC_3_fulltext.md", "not listed\n")
    write(r, "raw/P01/publications/PMC_1.md", "abstract\n")
    sh(r, "add", "-A")
    sh(r, "commit", "-qm", "init")
    write(r, "untracked_secret.txt", "never committed\n")
    return r


def run(repo, out, *args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run(["bash", str(SCRIPT), "--repo", str(repo), *args, str(out)], capture_output=True, text=True, env=e)


def files(d):
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and ".git" not in p.relative_to(d).parts)


def test_public_tree_is_the_commit_minus_the_exclude_list(repo, tmp_path):
    out = tmp_path / "public"
    before = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    r = run(repo, out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(out) == ["HOW_TO_RUN.md", "README.md", "docs/LIMITATIONS.md", "docs/organizers/BRIEF.md", "loop/ledger.tsv",
                          "pipelines/a.py", "raw/P01/publications/PMC_1.md", "raw/P01/publications/PMC_1_fulltext.md",
                          "raw/index.csv"]
    assert "raw/**/*fulltext*: 1 kept (listed in raw/index.csv as open-access full text), 2 left out" in r.stdout
    assert "internal: 0   (must be 0)" in r.stdout and "RESULT: PASS" in r.stdout
    assert "1 dead relative link(s), 0 of them in README.md / HOW_TO_RUN.md" in r.stdout and "docs/LIMITATIONS.md -> archive/OLD.md" in r.stdout
    assert json.loads(Path(str(out) + ".prepublic.json").read_text()) == []
    assert not (out / ".git").exists()                                    # no history, and not a repository unless --commit
    # the source repository is untouched
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == before
    assert (repo / "docs" / "archive" / "OLD.md").is_file() and (repo / "logs" / "run.log").is_file()


def test_no_fulltext_and_extra_excludes(repo, tmp_path):
    out = tmp_path / "public"
    r = run(repo, out, "--no-fulltext", env={"EXTRA_EXCLUDES": "loop/ledger.tsv docs/organizers"})
    assert r.returncode == 0, r.stdout + r.stderr
    got = files(out)
    assert not [f for f in got if "fulltext" in f] and "loop/ledger.tsv" not in got and "docs/organizers/BRIEF.md" not in got
    assert "0 kept (--no-fulltext), 3 left out" in r.stdout


def _raw_text_fixture(repo):
    """raw_text/ files: one extracted from the open-access full text, one from a not-listed full text, one from a protocol PDF."""
    write(repo, "raw_text/index.csv", "pdf,pdf_sha256,pages,chars,text_file,status\n"
                                      "P01/publications/PMC_1_fulltext.md,x,1,1,P01/publications/PMC_1_fulltext.md.txt.gz,ok\n"
                                      "P01/publications/PMC_3_fulltext.md,x,1,1,P01/publications/from_pmc3.txt.gz,ok\n"
                                      "P01/trials/protocol.pdf,x,1,1,P01/trials/protocol.pdf.txt.gz,ok\n")
    for rel in ("P01/publications/PMC_1_fulltext.md.txt.gz", "P01/publications/from_pmc3.txt.gz", "P01/trials/protocol.pdf.txt.gz"):
        write(repo, "raw_text/" + rel, "text\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "raw_text")


def test_raw_text_from_full_texts_follows_the_full_text_rule(repo, tmp_path):
    """Owner decision O06: text extracted from a full text leaves with it; kept only when its source is kept (open access)."""
    _raw_text_fixture(repo)
    r = run(repo, tmp_path / "p1")
    assert r.returncode == 0, r.stdout + r.stderr
    got = files(tmp_path / "p1")
    assert "raw_text/P01/publications/PMC_1_fulltext.md.txt.gz" in got and "raw_text/P01/trials/protocol.pdf.txt.gz" in got
    assert "raw_text/P01/publications/from_pmc3.txt.gz" not in got
    assert "raw_text from full texts: 1 kept (listed in raw/index.csv as open-access full text), 1 left out" in r.stdout
    r = run(repo, tmp_path / "p2", "--no-fulltext")
    assert r.returncode == 0 and "RESULT: PASS" in r.stdout, r.stdout + r.stderr
    got = files(tmp_path / "p2")
    assert not [f for f in got if f.startswith("raw_text/P01/publications/")] and "raw_text/P01/trials/protocol.pdf.txt.gz" in got
    assert "raw_text from full texts: 0 kept (--no-fulltext), 2 left out" in r.stdout
    assert "full-text files remaining after --no-fulltext: 0 (must be 0)" in r.stdout


def test_the_full_text_gate_is_part_of_the_result():
    text = SCRIPT.read_text()
    assert "raw_text from full texts:" in text and "full-text files remaining after --no-fulltext:" in text
    assert '[ "$FTGATE" = 0 ]' in text and '[ "$FTGATE" != 0 ]' in text


def test_no_committed_raw_text_file_comes_from_a_full_text():
    """The real raw_text/index.csv: pipelines/pdf_text.py skips .md files and every raw/**/*fulltext* file is .md, so no
    raw_text file is extracted from a full text (if one ever is, --no-fulltext leaves it out; this test says so first)."""
    import csv
    idx = ROOT / "raw_text" / "index.csv"
    if not idx.is_file():
        pytest.skip("no raw_text/index.csv")
    rows = list(csv.DictReader(open(idx, encoding="utf-8")))
    assert rows and not [r["pdf"] for r in rows if "fulltext" in Path(r["pdf"]).name.lower() or r["pdf"].endswith(".md")]


def test_the_gate_fails_on_an_identifier_and_on_a_dead_readme_link(repo, tmp_path):
    write(repo, "docs/deploy.md", "runs on " + "i-" + "0a1b2c3d4e5f67890" + "\n")
    write(repo, "README.md", "# T\n\nSee [the plan](docs/archive/OLD.md).\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "problems")
    r = run(repo, tmp_path / "public")
    assert r.returncode == 1 and "RESULT: FAIL" in r.stdout
    assert "infra: 1   (must be 0)" in r.stdout and "infra: docs/deploy.md:1" in r.stdout
    assert "1 of them in README.md / HOW_TO_RUN.md" in r.stdout


def test_refuses_a_used_directory_and_a_directory_inside_the_repository(repo, tmp_path):
    used = tmp_path / "used"
    used.mkdir()
    (used / "x").write_text("x")
    assert run(repo, used).returncode == 2 and (used / "x").read_text() == "x"
    assert run(repo, repo / "public").returncode == 2


def test_commit_needs_a_noreply_author_and_makes_one_commit(repo, tmp_path):
    bad = run(repo, tmp_path / "p1", "--commit", env={"PUBLIC_AUTHOR_NAME": "T", "PUBLIC_AUTHOR_EMAIL": "t@university.edu"})
    assert bad.returncode == 2 and "noreply" in bad.stderr
    out = tmp_path / "p2"
    ok = run(repo, out, "--commit", env={"PUBLIC_AUTHOR_NAME": "T", "PUBLIC_AUTHOR_EMAIL": "1+t@users.noreply.github.com"})
    assert ok.returncode == 0, ok.stdout + ok.stderr
    log = subprocess.run(["git", "-C", str(out), "log", "--format=%ae"], capture_output=True, text=True).stdout.split()
    assert log == ["1+t@users.noreply.github.com"]
    assert subprocess.run(["git", "-C", str(out), "remote"], capture_output=True, text=True).stdout == ""     # nothing pushed


def test_the_script_names_the_packets_exclude_list():
    text = SCRIPT.read_text()
    for pat in ("docs/archive", "docs/sessions", "docs/handoff", '"docs/packets_*"', '"docs/organizers/*.docx"', "logs",
                "loop/state.yaml"):
        assert f"  {pat}\n" in text, pat
    assert "git push" not in text and "git rm" not in text and "filter-repo" not in text


def test_fix6_exclusions_and_the_documents_a_test_still_reads():
    """FIX6 S1/S2: mock-ups, dated screenshots and internal working documents leave the public tree; the three documents a
    test reads in the public tree (and the frozen protocol among them) stay, as does hosted_check.py (FIX6 M1)."""
    import fnmatch
    block = SCRIPT.read_text().split("EXCLUDES=(", 1)[1].split("\n)", 1)[0]
    pats = [l.strip().strip('"') for l in block.splitlines() if l.strip() and not l.strip().startswith("#")]
    for p in ("docs/ux/mockups_2026-11-11", "docs/ux/pk3_shots", "docs/ux/pk12_shots", "docs/ui_before", "docs/ui_after",
              "docs/OWNER_CHECKLIST.md", "docs/GAP_ANALYSIS.md", "docs/PREPUBLIC_MOVES.md", "docs/DEVELOPMENT_CONTROLLER.md",
              # owner decision 15 Nov (S22): the stranger-test kit and the test that reads it do not ship
              "docs/STRANGER_TEST.md", "docs/STRANGER_TEST_RESULTS_TEMPLATE.md", "tests/test_stranger_test.py"):
        assert p in pats
    covered = lambda rel: any(fnmatch.fnmatch(rel, p) or rel.startswith(p.rstrip("/") + "/") for p in pats)
    for kept in ("docs/USABILITY_PLAN.md", "docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md",
                 "docs/VIDEO_SCRIPT.md", "docs/ux/harness_v3/journeys.sh", "scripts/hosted_check.py", "CLAUDE.md", "raw/index.csv",
                 "raw_text/index.csv", "pyproject.toml"):
        assert not covered(kept), kept
