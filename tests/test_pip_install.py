"""FIX6 M7 (owner, 15 Nov): the repository installs with pip. pyproject.toml reads its dependencies from requirements.txt and
the extra `ml` from requirements-ml.txt; the wheel carries the import packages as they are (`pipelines`, `harness`), the
`trialmindkg` command and the small run-time folders, and nothing else (no .env, no data folder, nothing the public tree
leaves out). The install test builds the wheel from a copy of the source in a temp directory and installs it into a fresh
virtual environment there; it skips, with the reason, when venv creation or the network (setuptools for the build) is
unavailable. TMKG_PIP_FULL=1 also installs the dependencies from PyPI (slow); by default the wheel goes in with --no-deps."""
import fnmatch
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import trialmindkg_cli as cli   # noqa: E402

SOURCE = ["pyproject.toml", "README.md", "LICENSE", "requirements.txt", "requirements-ml.txt", "trialmindkg_cli.py",
          "pipelines", "harness", "personas", "static", "config"]
TOP_LEVEL = {"pipelines", "harness", "personas", "static", "config", "trialmindkg_cli.py"}


def excludes():
    """The EXCLUDES patterns of scripts/make_public_tree.sh."""
    block = (ROOT / "scripts/make_public_tree.sh").read_text().split("EXCLUDES=(", 1)[1].split("\n)", 1)[0]
    return [l.strip().strip('"') for l in block.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_pyproject_reads_requirements_and_names_the_command():
    p = tomllib.loads((ROOT / "pyproject.toml").read_text())
    proj, st = p["project"], p["tool"]["setuptools"]
    assert proj["name"] == "trialmindkg" and proj["version"] and proj["requires-python"] == ">=3.11"
    assert set(proj["dynamic"]) == {"dependencies", "optional-dependencies"} and "dependencies" not in proj
    assert st["dynamic"]["dependencies"] == {"file": ["requirements.txt"]}
    assert st["dynamic"]["optional-dependencies"] == {"ml": {"file": ["requirements-ml.txt"]}}
    assert proj["scripts"] == {"trialmindkg": "trialmindkg_cli:main"}
    assert proj["license"] == "MIT" and proj["readme"] == "README.md"


def test_cli_help_lists_every_command_and_each_target_exists(capsys):
    assert cli.main(["--help"]) == 0 and cli.main([]) == 0
    out = capsys.readouterr().out
    for cmd, (kind, target, _) in cli.COMMANDS.items():
        assert cmd in out
        if kind == "file":
            assert (ROOT / "pipelines" / target).is_file()
        else:
            assert (ROOT / (target.replace(".", "/") + ".py")).is_file()
    assert cli.main(["no-such-command"]) == 2


def test_cli_runs_the_script_with_the_same_arguments(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.runpy, "run_path", lambda path, run_name: seen.update(path=path, argv=list(sys.argv), name=run_name))
    assert cli.main(["query", "program_trials", "pid=P19"]) == 0
    assert seen["path"] == str(ROOT / "pipelines" / "kg_query.py") and seen["name"] == "__main__"
    assert seen["argv"][1:] == ["program_trials", "pid=P19"]

    def exits(path, run_name):
        raise SystemExit(3)
    monkeypatch.setattr(cli.runpy, "run_path", exits)
    assert cli.main(["seed"]) == 3


def _run(cmd, cwd, timeout=900):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def test_wheel_installs_into_a_fresh_venv(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in SOURCE:
        s = ROOT / name
        if s.is_dir():
            shutil.copytree(s, src / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(s, src / name)
    venv = tmp_path / "venv"
    r = _run([sys.executable, "-m", "venv", str(venv)], tmp_path, 300)
    if r.returncode != 0:
        pytest.skip(f"venv creation unavailable: {r.stderr.strip()[-200:]}")
    py = venv / "bin" / "python"
    r = _run([str(py), "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(tmp_path / "dist"), str(src)], tmp_path)
    if r.returncode != 0:
        pytest.skip(f"the wheel could not be built here (network for setuptools?): {(r.stderr or r.stdout).strip()[-300:]}")
    wheel = next((tmp_path / "dist").glob("trialmindkg-*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    payload = [n for n in names if ".dist-info/" not in n]
    assert {n.split("/")[0] for n in payload} == TOP_LEVEL
    assert not [n for n in names if Path(n).name.startswith(".env")]
    assert not [n for n in payload if n.split("/")[0] in ("data", "outputs", "raw", "raw_text", "logs", "docs", "tests")]
    pats = excludes()
    assert not [n for n in payload for p in pats if fnmatch.fnmatch(n, p) or n.startswith(p.rstrip("/") + "/")]
    for need in ("pipelines/app_page_v3.html", "pipelines/linkpred/compare.py", "personas/clinician.yaml",
                 "config/nim_models.yaml", "static/fonts/fonts.css"):
        assert need in names
    full = os.environ.get("TMKG_PIP_FULL") == "1"
    r = _run([str(py), "-m", "pip", "install", "-q", *([] if full else ["--no-deps"]), str(wheel)], tmp_path, 1800)
    assert r.returncode == 0, r.stderr[-2000:]
    r = _run([str(py), "-c", "import pipelines, harness; print(list(pipelines.__path__)[0])"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "site-packages" in r.stdout
    r = _run([str(venv / "bin" / "trialmindkg"), "--help"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "trialmindkg" in r.stdout and "query" in r.stdout


def test_citation_file_matches_the_licence_and_the_package():
    """FIX6 S4: CITATION.cff names the author as LICENSE does, the MIT licence and the pyproject version; no e-mail address."""
    import yaml
    text = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    c = yaml.safe_load(text)
    assert c["cff-version"] == "1.2.0" and c["title"].startswith("TrialMindKG") and c["license"] == "MIT"
    a = c["authors"][0]
    assert f"Copyright (c) 2026 {a['given-names']} {a['family-names']}" in (ROOT / "LICENSE").read_text()
    assert str(c["version"]) == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert "@" not in text and "email" not in text
