"""CC-13: scripts/prepublic_check.py on a fixture git repository with planted problems: every planted item is flagged under its
category, harmless look-alikes are not, history is read on request, and nothing in the repository is changed."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepublic", ROOT / "scripts" / "prepublic_check.py")
pp = importlib.util.module_from_spec(spec)
sys.modules["prepublic"] = pp
spec.loader.exec_module(pp)


def sh(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def write(repo, rel, text, binary=False):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if binary else text.encode())


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    sh(r, "init", "-q")
    sh(r, "config", "user.name", "Ada Lovelace")
    sh(r, "config", "user.email", "ada@example.org")
    # planted problems
    write(r, "README.md", "Contact ada.lovelace@university.edu for access. Built by Ada.\n")
    write(r, "docs/notes.md", "The server is at 8.8.4.4 and the backup at 203.0.114.7.\n")
    write(r, "docs/reviews/2026-11-04/audit.md", "internal review\n")
    write(r, "docs/handoff/CLAUDE_CODE.md", "hand-off\n")
    write(r, "docs/HANDOFF.md", "hand-off\n")
    write(r, "sources/pharos.yaml.save", "old copy\n")
    write(r, "config.py", 'nvapi_key = "nvapi-' + "A" * 30 + '"\n')
    write(r, ".env", "NVIDIA_API_KEY=x\n")
    write(r, "data/internal/trial.csv", "a,b\n")
    write(r, "data/big.bin", b"\0" * 2_100_000, binary=True)
    # harmless look-alikes
    write(r, "ok.md", "Mail noreply@anthropic.com or me@example.com; local 127.0.0.1 and 0.0.0.0; doc range 192.0.2.5; "
                      "file G0_B_kfold@dm.json; version 1.2.3.4 is not needed.\n")
    write(r, ".env.example", "NVIDIA_API_KEY=\n")
    write(r, "data/internal/README.md", "never commit data here\n")
    sh(r, "add", "-f", "-A")
    sh(r, "commit", "-qm", "init")
    return r


def by(findings, cat):
    return [f for f in findings if f["category"] == cat]


def paths(findings, cat):
    return {f["path"] for f in by(findings, cat)}


def test_every_planted_item_is_flagged_under_its_category(repo):
    f = pp.scan(repo, max_mb=2.0)
    assert any(x["detail"] == "ada.lovelace@university.edu" and x["path"] == "README.md" and x["line"] == 1 for x in by(f, "emails"))
    ips = {x["detail"] for x in by(f, "ips")}
    assert {"8.8.4.4", "203.0.114.7"} <= ips
    assert paths(f, "internal") == {"docs/reviews/2026-11-04/audit.md", "docs/handoff/CLAUDE_CODE.md", "docs/HANDOFF.md"}
    assert paths(f, "junk") == {"sources/pharos.yaml.save"}
    assert paths(f, "env_files") == {".env", "data/internal/trial.csv"}
    assert paths(f, "large") == {"data/big.bin"} and "2.1 MB" in by(f, "large")[0]["detail"]
    assert paths(f, "secrets") == {"config.py"} and "NVIDIA key" in by(f, "secrets")[0]["detail"]
    names = by(f, "names")
    assert names and all(n["detail"].lower() in ("ada", "lovelace") for n in names) and "README.md" in paths(f, "names")


def test_harmless_lookalikes_are_not_flagged(repo):
    f = pp.scan(repo, max_mb=2.0)
    flagged_in_ok = [x for x in f if x["path"] == "ok.md" and x["category"] != "names"]
    assert flagged_in_ok == []
    assert ".env.example" not in {x["path"] for x in f} and "data/internal/README.md" not in {x["path"] for x in f}


def test_names_come_from_the_option_and_git_config_not_from_the_script(repo):
    write(repo, "extra.md", "Written with help from Grace Hopper.\n")
    sh(repo, "add", "extra.md")
    sh(repo, "commit", "-qm", "extra")
    assert "extra.md" not in paths(pp.scan(repo), "names")
    assert "extra.md" in paths(pp.scan(repo, names=["Hopper"]), "names")
    assert pp.default_names(repo) == ["Lovelace"] or set(pp.default_names(repo)) <= {"Ada", "Lovelace"}
    assert "Ada" not in Path(pp.__file__).read_text() and "Lovelace" not in Path(pp.__file__).read_text()


def test_history_finds_a_deleted_env_file_and_a_big_blob(repo):
    sh(repo, "rm", "-q", "--cached", ".env")
    sh(repo, "commit", "-qm", "remove env")
    now = pp.scan(repo, max_mb=50)
    assert ".env" not in paths(now, "env_files")                                  # gone from the tree...
    hist = pp.scan(repo, max_mb=50, history=True, history_mb=1.0)
    assert ".env" in paths(hist, "history") and "data/internal/trial.csv" in paths(hist, "history")
    assert "data/internal/README.md" not in paths(hist, "history") and ".env.example" not in paths(hist, "history")
    assert any(x["path"] == "data/big.bin" and "MB blob" in x["detail"] for x in by(hist, "history"))
    assert by(pp.scan(repo, max_mb=50), "history") == []                           # history is read only on request


def test_a_clean_repository_has_nothing_to_report(tmp_path):
    r = tmp_path / "clean"
    r.mkdir()
    sh(r, "init", "-q")
    write(r, "README.md", "A tool. Mail noreply@example.com.\n")
    sh(r, "add", "-A")
    assert pp.scan(r) == [] and "Nothing to look at" in pp.render([])


def test_it_changes_nothing(repo):
    before = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--ignored"], capture_output=True, text=True).stdout
    tree = sorted((str(p.relative_to(repo)), p.stat().st_mtime_ns, p.stat().st_size) for p in repo.rglob("*") if ".git" not in p.parts)
    pp.scan(repo, history=True, max_mb=2.0)
    after = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--ignored"], capture_output=True, text=True).stdout
    assert before == after
    assert tree == sorted((str(p.relative_to(repo)), p.stat().st_mtime_ns, p.stat().st_size) for p in repo.rglob("*") if ".git" not in p.parts)


def test_render_is_a_todo_list_grouped_by_category_with_actions(repo):
    text = pp.render(pp.scan(repo, max_mb=2.0))
    assert "nothing was changed" in text
    for cat in ("secrets", "env_files", "emails", "ips", "names", "internal", "junk", "large"):
        assert f"## {cat}:" in text, cat
    assert text.index("## secrets") < text.index("## emails") < text.index("## large")
    assert "git rm" in text and "docs/HANDOFF.md" in text


def test_large_groups_are_summarised_by_directory():
    findings = [dict(category="emails", path=f"raw/P{i % 3}/x/{i}.md", line=1, detail=f"a{i}@lab.org", action="remove") for i in range(90)]
    text = pp.render(findings)
    assert "raw/P0/  30 item(s) in 30 file(s)" in text and text.count("\n- ") <= 5


def test_cli_exit_code_and_json(repo, capsys):
    assert pp.main(["--root", str(repo), "--json", "--max-mb", "2"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert {"category", "path", "line", "detail", "action"} <= set(data[0])
    clean = repo.parent / "clean2"
    clean.mkdir()
    sh(clean, "init", "-q")
    assert pp.main(["--root", str(clean)]) == 0


def test_names_file_organiser_docx_and_logs(repo, tmp_path, capsys):
    """CC-37: names can come from a file kept outside the repository (the other teams' names); the organisers' .docx
    files and logs/ are internal; the redacted organiser Q&A (.md) is not flagged as internal by its path."""
    write(repo, "docs/organizers/Questions_from_teams.docx", b"PK\x03\x04", binary=True)
    write(repo, "docs/organizers/QUESTIONS_FROM_TEAMS.md", "General comments. Answers to this team.\n")
    write(repo, "logs/RUNNING_x.txt", "running\n")
    write(repo, "plan.md", "A look-alike team, Teamalpha, builds the same thing.\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "organiser files")
    nf = tmp_path / "private_names.txt"
    nf.write_text("# other teams (never committed)\nTeamalpha\n\n")
    assert pp.read_names(nf) == ["Teamalpha"] and pp.read_names("") == [] and pp.read_names(tmp_path / "none") == []
    f = pp.scan(repo, names=pp.read_names(nf))
    internal = paths(f, "internal")
    assert {"docs/organizers/Questions_from_teams.docx", "logs/RUNNING_x.txt"} <= internal
    assert "docs/organizers/QUESTIONS_FROM_TEAMS.md" not in internal and "plan.md" in paths(f, "names")
    assert "Teamalpha" not in Path(pp.__file__).read_text()
    assert pp.main(["--root", str(repo), "--names-file", str(nf), "--json"]) == 1
    assert any(x["detail"] == "Teamalpha" for x in json.loads(capsys.readouterr().out))


def test_infrastructure_identifiers_and_home_paths_are_flagged(repo):
    """PK5 (finding SEC-13): an AWS account id, an ARN, EC2 / VPC resource ids and a personal home path are listed under
    'infra'; placeholders are not. The strings are built from parts so this test file itself stays clean."""
    acct = "2109" + "8765" + "4321"
    inst, sg, eip = "i-" + "0a1b2c3d4e5f67890", "sg-" + "0123456789abcdef0", "eipalloc-" + "0fedcba9876543210"
    home = "/Us" + "ers/ada/projects/x"
    write(repo, "docs/deploy.md", f"role arn:aws:iam::{acct}:role/app on {inst} ({sg}, {eip})\n"
                                  f"bucket tmkg-app-{acct}-apse2; AWS account id: {acct}\ncd {home}\n")
    write(repo, "docs/placeholders.md", "arn:aws:iam::123456789012:role/x and arn:aws:iam::ACCOUNT_ID:role/x on i-0example\n"
                                        "eipalloc-0example; cd /Users/you/aact; /home/user/project; /home/tmkg/.cache; ~/projects/x; "
                                        "bytes 20286320640 and 568155956278e94216024fa30822ae34 are not ids; sig-12345678\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "infra")
    f = pp.scan(repo, max_mb=2.0)
    infra = by(f, "infra")
    assert paths(f, "infra") == {"docs/deploy.md"}, [(x["path"], x["detail"]) for x in infra]
    details = " | ".join(x["detail"] for x in infra)
    for want in (f"AWS account id in an ARN: {acct}", f"EC2 / VPC resource id: {inst}", f"EC2 / VPC resource id: {sg}",
                 f"EC2 / VPC resource id: {eip}", f"name embedding an AWS account id: {acct}", f"AWS account id: {acct}",
                 "personal home path: /Us" + "ers/ada"):
        assert want in details, want
    assert {x["line"] for x in infra} == {1, 2, 3}
    text = pp.render(f)
    assert "## infra:" in text and text.index("## secrets") < text.index("## infra") < text.index("## emails")


def test_history_lists_personal_commit_addresses(repo):
    """SEC-13: with --history, an author or committer e-mail that is not a noreply address is listed with its commit count."""
    assert [x for x in pp.scan_history(repo) if x["path"] == "(commit metadata)"] == []     # example.org is an allowed address
    sh(repo, "-c", "user.email=ada.lovelace@university.edu", "commit", "--allow-empty", "-qm", "personal address")
    sh(repo, "-c", "user.email=1+ada@users.noreply.github.com", "commit", "--allow-empty", "-qm", "noreply address")
    mine = [x for x in pp.scan_history(repo) if x["path"] == "(commit metadata)"]
    assert [x["detail"].split(" ")[0] for x in mine] == ["ada.lovelace@university.edu"] and "single-commit" in mine[0]["action"]


def test_a_plain_directory_is_scanned_like_a_repository(repo, tmp_path):
    """The exported public tree is not a git repository: every file in it is looked at (scripts/make_public_tree.sh)."""
    plain = tmp_path / "plain"
    (plain / "docs" / "handoff").mkdir(parents=True)
    (plain / "docs" / "handoff" / "X.md").write_text("hand-off\n")
    (plain / "notes.md").write_text("see 8.8.4.4\n")
    assert not pp.is_git_root(plain) and pp.is_git_root(repo)
    assert pp.tracked(plain) == ["docs/handoff/X.md", "notes.md"]
    f = pp.scan(plain)
    assert paths(f, "internal") == {"docs/handoff/X.md"} and paths(f, "ips") == {"notes.md"}
    inside = repo / "sub"                                   # a folder inside a work tree is not that repository's root
    inside.mkdir()
    (inside / "a.md").write_text("x\n")
    assert not pp.is_git_root(inside) and pp.tracked(inside) == ["a.md"]


def test_archive_packets_reviews_and_state_count_as_internal(repo):
    for rel in ("docs/archive/OLD_PLAN.md", "docs/packets_2026-11-11/PK5.md", "docs/REVIEW_JUDGE_2026-11-11.md",
                "docs/SPRINT_PLAN_2026-11-11.md", "loop/state.yaml"):
        write(repo, rel, "internal\n")
    write(repo, "docs/LIMITATIONS.md", "public\n")
    write(repo, "loop/ledger.tsv", "date\tid\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "internal files")
    internal = paths(pp.scan(repo, max_mb=2.0), "internal")
    assert {"docs/archive/OLD_PLAN.md", "docs/packets_2026-11-11/PK5.md", "docs/REVIEW_JUDGE_2026-11-11.md",
            "docs/SPRINT_PLAN_2026-11-11.md", "loop/state.yaml"} <= internal
    assert "docs/LIMITATIONS.md" not in internal and "loop/ledger.tsv" not in internal
