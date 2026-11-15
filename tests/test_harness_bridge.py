"""PK5: the journey harness's bridge takes its default card index from pharos_index.index_path (the same default as the
app), never from a fixed file name."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import pharos_index  # noqa: E402

HARNESS = ROOT / "docs" / "ux" / "harness_v3"


def test_bridge_default_index_comes_from_index_path():
    src = (HARNESS / "bridge.py").read_text(encoding="utf-8")
    assert re.search(r'setdefault\("TMKG_PHAROS_INDEX",\s*str\(_pharos_index\.index_path\(ROOT\)\)\)', src)
    assert "import pharos_index as _pharos_index" in src


def test_no_fixed_older_index_name_remains_in_the_harness():
    for p in HARNESS.iterdir():
        if p.suffix in {".py", ".js", ".sh", ".json"}:
            assert "card_index_v2.sqlite" not in p.read_text(encoding="utf-8"), p.name


def test_index_path_prefers_the_newest_file_that_exists(tmp_path):
    d = tmp_path / "data" / "kg" / "pharos_index"
    d.mkdir(parents=True)
    assert pharos_index.index_path(tmp_path).name == pharos_index.INDEX_NAMES[-1]      # none exists: the fresh-build name
    (d / "card_index_v2.sqlite").write_bytes(b"")
    assert pharos_index.index_path(tmp_path).name == "card_index_v2.sqlite"
    (d / "card_index_v3.sqlite").write_bytes(b"")
    assert pharos_index.index_path(tmp_path).name == "card_index_v3.sqlite"
