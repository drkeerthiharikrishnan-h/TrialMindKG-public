"""FIX1 F2 (gate 1, G1-SEC-02): every script and pipeline module parses on the Python that runs the suite.

scripts/make_gap_example.py nested double quotes inside an f-string (valid only from Python 3.12), so on the venv's 3.11 the
PK7A test file errored at collection. `make check` runs the same compile pass."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "pipelines").rglob("*.py"))


def test_there_are_files_to_compile():
    assert len(FILES) > 50


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_compiles(path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_importing_the_gap_example_script_leaves_the_snapshot_mode_alone(monkeypatch):
    """FIX1 F2: once the PK7A test file could be collected again, its import of scripts/make_gap_example.py set
    TMKG_SNAPSHOT_MODE=offline for the whole test session, and later tests of live Snapshot calls failed."""
    import importlib.util
    import os
    monkeypatch.delenv("TMKG_SNAPSHOT_MODE", raising=False)
    spec = importlib.util.spec_from_file_location("mge_import_check", ROOT / "scripts" / "make_gap_example.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "TMKG_SNAPSHOT_MODE" not in os.environ and mod.SNAPSHOT_MODE() == "offline"
