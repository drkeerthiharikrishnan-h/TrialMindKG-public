"""scripts/download_hashes.py: size + SHA256 of every hosted download in HOW_TO_RUN.md."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import download_hashes as dh  # noqa: E402

DL = {"base_url": "https://h/downloads/", "files": [{"path": "kgx/", "bytes": 10, "title": "KGX"},
                                                     {"path": "one.txt", "bytes": 3, "title": "one file"}]}


class FakeSnap:
    def __init__(self, texts):
        self.texts, self.urls = texts, []

    def get_text(self, source, url):
        self.urls.append(url)
        t = self.texts.get(url)
        if isinstance(t, Exception):
            raise t
        return type("R", (), {"body": None if t is None else {"text": t}})()


def test_fetch_sums_asks_only_folders_and_keeps_failures():
    snap = FakeSnap({"https://h/downloads/kgx/SHA256SUMS": "ab12  nodes.tsv.gz\n"})
    assert dh.fetch_sums(DL["base_url"], DL["files"], snap) == {"kgx/": "ab12  nodes.tsv.gz\n"}
    assert snap.urls == ["https://h/downloads/kgx/SHA256SUMS"]
    got = dh.fetch_sums(DL["base_url"], DL["files"], FakeSnap({"https://h/downloads/kgx/SHA256SUMS": RuntimeError("x")}))
    assert isinstance(got["kgx/"], RuntimeError)
    assert isinstance(dh.fetch_sums(DL["base_url"], DL["files"], FakeSnap({}))["kgx/"], RuntimeError)   # 404


def test_render_lists_sizes_and_hash_lines_or_why_not():
    block = dh.render(DL, {"kgx/": "ab12  nodes.tsv.gz\n"})
    assert block.startswith(dh.START) and block.endswith(dh.END)
    assert "| KGX | https://h/downloads/kgx/ | 10 |" in block and "ab12  nodes.tsv.gz" in block
    block = dh.render(DL, {"kgx/": RuntimeError("GET x failed after retries: ProxyError")})
    assert "SHA256 not fetched yet (the site did not answer from where this ran)" in block
    assert "`one.txt`: a single file, no SHA256SUMS folder" in block and "one.txtSHA256SUMS" not in block


def test_replace_block_and_check():
    doc = f"intro\n{dh.START}\nOLDTEXT\n{dh.END}\nend\n"
    new = dh.replace_block(doc, dh.render(DL, {}))
    assert "OLDTEXT" not in new and new.startswith("intro\n") and new.endswith("end\n")
    assert dh.check(new, DL) == []
    changed = dict(DL, files=[dict(DL["files"][0], bytes=11)] + DL["files"][1:])
    assert dh.check(new, changed) == ["kgx/: size in HOW_TO_RUN.md differs from downloads.json (11)"]
    assert dh.check("no block", DL) == ["no download-hashes block"]
    assert dh.replace_block("text", "B").endswith("\n\nB\n")


def test_main_writes_and_checks(tmp_path):
    (tmp_path / "downloads.json").write_text(json.dumps(DL))
    (tmp_path / "HOW_TO_RUN.md").write_text(f"x\n{dh.START}\n{dh.END}\n")
    assert dh.main([], root=tmp_path, snap=FakeSnap({"https://h/downloads/kgx/SHA256SUMS": "ff  a\n"})) == 0
    assert "ff  a" in (tmp_path / "HOW_TO_RUN.md").read_text()
    assert dh.main(["--check"], root=tmp_path) == 0


def test_committed_how_to_run_matches_downloads_json():
    doc = (ROOT / "HOW_TO_RUN.md").read_text()
    assert dh.check(doc, json.loads((ROOT / "downloads.json").read_text())) == [], \
        "run: python scripts/download_hashes.py"


def test_an_unreachable_site_keeps_the_checksums_of_unchanged_folders_only(tmp_path):
    """A folder whose size is unchanged keeps its committed SHA256SUMS when the site cannot be reached; a re-uploaded or
    new folder gets "not fetched yet", never its old (wrong) checksums."""
    old = dict(base_url="https://x.example/downloads/", files=[
        dict(title="A", path="a/", bytes=10, what="SHA256SUMS"), dict(title="B", path="b/", bytes=20, what="SHA256SUMS")])
    doc = dh.render(old, {"a/": "aa  file1", "b/": "bb  file2"})
    new = dict(old, files=[dict(old["files"][0]), dict(old["files"][1], bytes=21),
                           dict(title="C", path="c/", bytes=5, what="SHA256SUMS")])
    assert dh.kept_sums(doc, new) == {"a/": "aa  file1"}
    out = dh.render(new, {**{k: RuntimeError("failed after retries") for k in ("a/", "b/", "c/")}, **dh.kept_sums(doc, new)})
    assert "aa  file1" in out and "bb  file2" not in out and out.count("SHA256 not fetched yet") == 2
    assert dh.check(out, new) == []
