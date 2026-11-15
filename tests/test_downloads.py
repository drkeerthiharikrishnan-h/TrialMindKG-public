"""Downloads section (the hosted app's For-judges page and README) read from downloads.json; the Neptune README's reason wording.
Fixtures and tmp files only."""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, "pipelines")
import app as appmod  # noqa: E402
import neptune_export as nx  # noqa: E402
import usability  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_downloads", ROOT / "scripts" / "render_downloads.py")
rd = importlib.util.module_from_spec(spec)
sys.modules["render_downloads"] = rd
spec.loader.exec_module(rd)


def manifest(tmp_path, base="https://d1.example/downloads/", files=None, name="downloads.json"):
    files = [{"title": "Neptune files", "path": "neptune/", "what": "load files", "bytes": 5_000_000, "sha256": "ab" * 32},
             {"title": "Verdicts", "path": "/verdicts/pharos_verdicts.tsv.gz", "what": "one row per source row"}] if files is None else files
    (tmp_path / name).write_text(json.dumps({"base_url": base, "files": files}))
    return tmp_path


def test_the_committed_manifest_is_valid_and_hidden_until_the_base_url_is_set():
    j = json.loads((ROOT / "downloads.json").read_text())
    base = j["base_url"]                                               # "" before deploy; the hosted /downloads/ folder after (8 Nov)
    assert base == "" or (base.startswith("https://") and base.endswith("/downloads/"))
    # 8 Nov: + PyG; S05 (13 Nov): a small sample to open first (FIX1 F2: the test follows the committed manifest)
    assert [f["path"] for f in j["files"]] == ["sample/", "neptune/", "kgx/", "verdicts/", "pyg/"]
    assert all(f["bytes"] > 0 and "SHA256SUMS" in f["what"] for f in j["files"])
    for f in j["files"]:
        assert f["title"] and f["what"] and not f["path"].startswith(("/", "http")) and ".." not in f["path"]
    d = usability.downloads(ROOT, env={})
    assert d["available"] is bool(base) and d["base_url"] == base and len(d["files"]) == 5
    assert all(f["url"] == (base + f["path"] if base else "") for f in d["files"])


def test_urls_are_the_base_plus_the_path_and_folders_are_marked(tmp_path):
    d = usability.downloads(manifest(tmp_path), env={})
    assert d["available"] and d["base_url"] == "https://d1.example/downloads/"
    a, b = d["files"]
    assert a["url"] == "https://d1.example/downloads/neptune/" and a["folder"] is True and a["bytes"] == 5_000_000 and a["sha256"] == "ab" * 32
    assert b["url"] == "https://d1.example/downloads/verdicts/pharos_verdicts.tsv.gz" and b["folder"] is False and b["bytes"] is None


def test_a_base_without_a_slash_gets_one_and_the_environment_overrides_the_file(tmp_path):
    root = manifest(tmp_path, base="https://d1.example/downloads")
    assert usability.downloads(root, env={})["files"][0]["url"] == "https://d1.example/downloads/neptune/"
    d = usability.downloads(root, env={"TMKG_DOWNLOADS_BASE": "https://other.example/x/"})
    assert d["files"][0]["url"] == "https://other.example/x/neptune/"
    empty = manifest(tmp_path / "e" if (tmp_path / "e").mkdir() is None else tmp_path, base="")
    assert usability.downloads(empty, env={"TMKG_DOWNLOADS_BASE": "https://env.example/d"})["available"] is True


def test_nothing_is_shown_without_a_base_a_file_or_entries(tmp_path):
    assert usability.downloads(manifest(tmp_path, base=""), env={})["available"] is False
    assert usability.downloads(tmp_path / "nowhere", env={}) == dict(available=False, base_url="", files=[])
    assert usability.downloads(manifest(tmp_path, files=[]), env={})["available"] is False
    (tmp_path / "downloads.json").write_text("{not json")
    assert usability.downloads(tmp_path, env={})["available"] is False
    skipped = usability.downloads(manifest(tmp_path, files=[{"title": "no path"}, {"path": "x/"}, {"title": "ok", "path": "x/"}]), env={})
    assert [f["title"] for f in skipped["files"]] == ["ok"]


def test_the_endpoint_and_the_page(monkeypatch):
    monkeypatch.delenv("TMKG_DOWNLOADS_BASE", raising=False)
    c = TestClient(appmod.app)
    committed = usability.downloads(ROOT, env={})["available"]
    assert c.get("/api/downloads").json()["available"] is committed                              # as downloads.json says
    monkeypatch.setenv("TMKG_DOWNLOADS_BASE", "https://cdn.example/downloads/")
    j = c.get("/api/downloads").json()
    assert j["available"] and j["files"][0]["url"] == "https://cdn.example/downloads/sample/"   # S05: the sample first
    page = c.get("/").text
    assert "/api/downloads" in page and 'id="downloads"' in page and "<h2>Downloads</h2>" in page


# ------------------------------------------------------------------------------------------------ README
def test_render_is_empty_when_nothing_is_published_and_a_table_when_it_is(tmp_path):
    (tmp_path / "h").mkdir()
    assert rd.render(usability.downloads(manifest(tmp_path / "h", base=""), env={})) == ""
    text = rd.render(usability.downloads(manifest(tmp_path), env={}))
    assert text.startswith("## Downloads") and "| [Neptune files](https://d1.example/downloads/neptune/) (folder) | load files (5 MiB)" in text
    assert "[Verdicts](https://d1.example/downloads/verdicts/pharos_verdicts.tsv.gz) | one row per source row |" in text
    assert "sha256 `abababababab...`" in text


def test_update_replaces_only_the_marked_block_and_is_idempotent(tmp_path):
    d = usability.downloads(manifest(tmp_path), env={})
    readme = "# T\n\nintro\n\n<!-- downloads:start -->\nold stuff\n<!-- downloads:end -->\n\n## Next\n"
    new = rd.update(readme, d)
    assert "old stuff" not in new and "## Downloads" in new and new.startswith("# T\n\nintro\n") and new.endswith("\n\n## Next\n")
    assert rd.update(new, d) == new
    (tmp_path / "h").mkdir()
    hidden = rd.update(new, usability.downloads(manifest(tmp_path / "h", base=""), env={}))
    assert "## Downloads" not in hidden and "<!-- downloads:start -->\n<!-- downloads:end -->" in hidden
    with pytest.raises(ValueError):
        rd.update("# no markers\n", d)


def test_the_committed_readme_matches_the_committed_manifest():
    assert rd.main(["--check"]) == 0                                    # README block matches downloads.json (its base_url, or empty)
    assert "<!-- downloads:start -->" in (ROOT / "README.md").read_text()


def test_the_script_writes_and_checks(tmp_path, monkeypatch):
    root = manifest(tmp_path)
    (root / "README.md").write_text("x\n<!-- downloads:start -->\n<!-- downloads:end -->\ny\n")
    monkeypatch.delenv("TMKG_DOWNLOADS_BASE", raising=False)
    assert rd.main(["--root", str(root), "--check"]) == 1               # stale
    assert rd.main(["--root", str(root)]) == 0
    assert "https://d1.example/downloads/neptune/" in (root / "README.md").read_text() and rd.main(["--root", str(root), "--check"]) == 0


# ------------------------------------------------------------------------------------------------ the Neptune README's reason
def test_the_not_hosted_reason_is_the_wording_science_gave():
    assert nx.NOT_HOSTED_REASON == (
        "The AWS account used for this project was created with AWS's 'Sign up for AWS (new)' option. Its AWS-managed organization "
        "policy does not include Amazon Neptune, and AWS Support confirmed that Neptune is out of scope for this account type, so no "
        "graph database is hosted. The hosted app answers from the same graph through its local card index; these files load the pruned "
        "graph with the trial layer (G4) into any Amazon Neptune Database cluster.")      # CC-33 (review m7): not the whole graph
    man = dict(graph="G4", counts=dict(nodes=1, edges=1), files=dict(nodes=[dict(file="nodes_000.csv.gz", rows=1, sha256="a" * 64)],
               edges=[dict(file="edges_000.csv.gz", rows=1, sha256="b" * 64)]), rows_per_file=1000000,
               totals=dict(labels={"Gene": 1}, types={"x": 1}), layers=dict(used=["pharos"], missing=[]),
               dropped=dict(pruned=0, duplicate_edge_ids=0, edge_end_not_a_node=0), parity={})
    text = nx.readme_markdown(man)
    assert nx.NOT_HOSTED_REASON in text and text.count("load the pruned graph with the trial layer (G4)") == 1
