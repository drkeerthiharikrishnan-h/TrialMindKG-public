"""CC-R11 reproducibility pack: the `make demo` fixture and orchestrator (scripts/make_demo_fixture.py, scripts/demo.py),
schema_card.py --check, hosted_check.py --get-only, `python -m pipelines.linkpred.compare` from a fresh checkout, the
Makefile targets and scripts/fresh_clone_check.sh."""
import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "pipelines"))
import demo  # noqa: E402
import make_demo_fixture as mdf  # noqa: E402
import schema_card  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "demo"


# ---- the fixture
def test_fixture_tables_cover_every_table_the_two_cards_read():
    read = set()
    for card in ("pharos", "dto"):
        c = yaml.safe_load((ROOT / "sources" / f"{card}.yaml").read_text())
        for section in ("nodes", "edges"):
            read |= {spec["table"] for spec in c.get(section) or [] if "table" in spec}
        read |= {lk["table"] for lk in (c.get("lookups") or {}).values() if "table" in lk}
    assert read <= set(mdf.TABLES)
    assert {p.name.replace(".tsv.gz", "") for p in (FIX / "pharos").glob("*.tsv.gz")} == set(mdf.TABLES)


def test_fixture_is_small_and_matches_its_manifest():
    import hashlib
    size = sum(p.stat().st_size for p in FIX.rglob("*") if p.is_file())
    assert size <= 20 * 1024 * 1024
    rows = {r.split("\t")[0]: r.split("\t") for r in (FIX / "MANIFEST.tsv").read_text().splitlines()[1:]}
    for g in mdf.TABLES:
        text = gzip.open(FIX / "pharos" / f"{g}.tsv.gz", "rb").read()
        assert hashlib.sha256(text).hexdigest() == rows[g][5], g
        assert rows[g][6] == "pharos319"
    import csv
    symbols = {r["Symbol"] for r in csv.DictReader(gzip.open(FIX / "pharos" / "targets.tsv.gz", "rt"), delimiter="\t")}
    assert symbols == set(mdf.FAMILY)


class _FakeSnap:
    def post(self, source, url, body):
        return type("R", (), {"fetched_at": "2026-11-10T00:00:00Z"})()


def test_family_rows_and_main_write_tables_and_manifest(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(mdf.pharos, "group_fields", lambda snap, g: ["UniProt", "Symbol"])
    monkeypatch.setattr(mdf.pharos, "db_version", lambda snap: "pharos319")

    def fake_download(snap, model, fields, top, batch, log):
        calls.append(batch)
        return [{"id": 1, "UniProt": f"U{batch}", "Symbol": batch}, {"id": 1, "UniProt": f"U{batch}", "Symbol": batch}]
    monkeypatch.setattr(mdf.pharos, "download", fake_download)
    fields, rows = mdf.family_rows(_FakeSnap(), "targets", ["A", "B"], log=lambda *_: None)
    assert fields == ["UniProt", "Symbol"] and [r["Symbol"] for r in rows] == ["A", "B"]     # duplicates removed
    entries = mdf.main(out=tmp_path, symbols=["A"], snap=_FakeSnap(), log=lambda *_: None)
    assert [e["group"] for e in entries] == mdf.TABLES
    assert (tmp_path / "MANIFEST.tsv").read_text().count("pharos319") == len(mdf.TABLES)


def test_family_rows_refuses_a_symbol_pharos_does_not_return(monkeypatch):
    monkeypatch.setattr(mdf.pharos, "group_fields", lambda snap, g: ["Symbol"])
    monkeypatch.setattr(mdf.pharos, "download", lambda *a, **k: [{"id": 1, "Symbol": "OTHER"}])
    with pytest.raises(mdf.pharos.PharosError):
        mdf.family_rows(_FakeSnap(), "targets", ["EGFR"], log=lambda *_: None)


# ---- the orchestrator
def test_make_workdir_copies_code_and_fixture_and_links_reference(tmp_path):
    work = demo.make_workdir(tmp_path / "root")
    assert (work / "pipelines" / "linkpred" / "run.py").is_file() and not (work / "pipelines").is_symlink()
    assert (work / "sources" / "pharos.yaml").is_file() and (work / "sources" / "dto.yaml").is_file()
    assert (work / "data" / "reference").is_symlink()
    assert (work / "data" / "pharos" / "diseases.tsv.gz").is_file()
    demo.make_workdir(tmp_path / "root")          # a second run starts clean, no error


def test_commands_use_the_frozen_flags_with_reduced_epochs_and_the_demo_tag():
    steps = dict(demo.commands("py", epochs=7))
    bench = steps["benchmark (demo)"]
    assert bench[bench.index("--epochs") + 1] == "7" and bench[-2:] == ["--tag", "demo"]
    for flag in ("--drop-levels", "--test-levels", "--positives", "--neg-match", "--metapaths", "--reverse-edges"):
        assert flag in bench
    assert steps["graphs G0,G2"][-2:] == ["G0,G2", "--allow-missing"]
    assert steps["compare"][-1] == "G0@demo"
    # the demo tag is never one the report treats as authoritative
    from linkpred import compare
    assert not compare.AUTHORITATIVE_TAG.match(demo.TAG)


def _res(path, n_test, auroc=0.5):
    path.write_text(json.dumps({"summary": {"auroc": {"mean": auroc, "ci95": 0.0}, "auroc_metapath": {"mean": 0.7},
                                            "metapath_coverage": {"mean": 0.5}, "n_test": {"mean": n_test}}}))


def test_summary_lines_read_the_numbers_and_say_demo(tmp_path):
    _res(tmp_path / "G0_B_kfold@demo.json", 24, 0.43)
    _res(tmp_path / "G2_B_kfold@demo.json", 24, 0.5)
    lines = demo.summary_lines(tmp_path, {"build": 5.0, "benchmark": 55.0})
    assert lines[0] == lines[-1] == demo.LABEL and "demo, not the reported numbers" in demo.LABEL
    assert "GNN AUROC 0.4300" in lines[1] and "coverage 50%" in lines[1] and "test pairs per fold 24" in lines[1]
    assert "too noisy" in lines[3] and "total: 1.0 min" in lines[5]


def test_size_note_is_conditional_on_the_test_pair_count(tmp_path):
    assert demo.size_note(tmp_path) == "no test pairs reported"
    _res(tmp_path / "G0_B_kfold@demo.json", 500)
    assert demo.size_note(tmp_path) == "500 test pairs per fold"
    _res(tmp_path / "G2_B_kfold@demo.json", 30)
    assert demo.size_note(tmp_path).startswith("only 30 test pairs per fold")


def test_collect_copies_only_what_exists(tmp_path):
    work, out = tmp_path / "w", tmp_path / "o"
    (work / "outputs" / "linkpred").mkdir(parents=True)
    out.mkdir()
    (work / "outputs" / "linkpred" / "compare.md").write_text("x")
    assert demo.collect(work, out) == ["compare.md"] and (out / "compare.md").read_text() == "x"


# ---- schema check, hosted check, compare from a fresh checkout
def test_schema_check_writes_nothing_and_fails_only_on_non_biolink_terms(monkeypatch, tmp_path):
    before = (ROOT / "docs" / "SCHEMA_CARD.md").read_text()
    out = []
    assert schema_card.check(ROOT, log=out.append) == 0
    assert (ROOT / "docs" / "SCHEMA_CARD.md").read_text() == before
    assert out[-1].startswith("schema check:")
    real = schema_card.build_card
    monkeypatch.setattr(schema_card, "build_card", lambda root: dict(real(root), problems=["class X is not BioLink"]))
    out = []
    assert schema_card.check(ROOT, log=out.append) == 1 and out[0] == "FAIL schema: class X is not BioLink"


def _hosted_check():
    spec = importlib.util.spec_from_file_location("hosted_check_r11",
                                                  ROOT / "scripts/hosted_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_hosted_check_parse_args():
    hc = _hosted_check()
    assert hc.parse_args([]) == (None, False, False)                    # PK15: + --neptune
    assert hc.parse_args(["--get-only"]) == (None, True, False)
    assert hc.parse_args(["https://x.example/", "--get-only"]) == ("https://x.example", True, False)


def test_hosted_check_get_only_never_calls_the_ask_endpoints(monkeypatch):
    hc = _hosted_check()
    hc.GET_ONLY = True
    posted = []
    monkeypatch.setattr(hc, "ask_smoke", lambda qs: posted.append(qs))
    monkeypatch.setattr(hc, "smoke_pages", lambda: None)
    monkeypatch.setattr(hc, "call", lambda path, data=None, timeout=90: (200, 0.1, json.dumps({"passed": True}).encode()))
    res = {"/api/landing": (200, 0, b'{"chips": []}'), "/api/pharos/status": (200, 0, b"{}"),
           "/api/pharos/demo_questions": (200, 0, b'[{"id": "d1", "question": "q"}]'),
           "/api/pharos/card/NCBIGene:1956": (200, 0, b'{"predicted": []}')}
    hc.smoke(res)
    assert posted == []
    hc.GET_ONLY = False
    hc.smoke(res)
    assert len(posted) == 1


def test_compare_runs_as_a_module_from_the_checkout(tmp_path):
    folds = [{"auroc": v, "auroc_metapath": v, "metapath_coverage": 0.9} for v in (0.6, 0.61, 0.6, 0.59, 0.6)]
    for g in ("G0", "G2"):
        (tmp_path / f"{g}_B_kfold@demo.json").write_text(json.dumps(
            {"settings": {"test_set": {"digest": "d"}}, "per_seed": [{"folds": folds}], "summary": {}}))
    r = subprocess.run([sys.executable, "-m", "pipelines.linkpred.compare", "--base", "G0@demo", "--out-dir",
                        str(tmp_path)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    assert (tmp_path / "compare.md").exists() and (tmp_path / "report.md").exists()


# ---- Makefile and the fresh-clone script
def test_makefile_has_the_five_targets_and_check_runs_tests_harness_schema():
    text = (ROOT / "Makefile").read_text()
    for t in ("check:", "demo:", "harness:", "hosted-check:", "clean-demo:"):
        assert f"\n{t}" in text
    assert "check: tests harness schema" in text and "--get-only" in text
    for ignored in ("test_prepublic_check.py", "test_controller_backends.py", "test_dev_controller.py"):
        assert ignored in text


def test_fresh_clone_check_help_and_bad_arguments():
    script = ROOT / "scripts" / "fresh_clone_check.sh"
    r = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--no-clone" in r.stdout
    r = subprocess.run(["bash", str(script), "--no-clone", str(ROOT / "no_such_dir")], capture_output=True, text=True)
    assert r.returncode == 2
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True)      # FIX6 S6: the repository is required
    assert r.returncode == 2 and "public URL" in r.stderr
    text = script.read_text()
    assert "github.com" not in text                                                   # no default (private) repository
    assert text.index('pip install -q -e "$REPO"') < text.index("step make_check")     # FIX6 M7: pip install -e . first


# ---- the frozen flags, in one file
def _protocol_flags():
    """Run A's command in docs/STRATEGY_REVIEW_RESPONSE_2026-11-02.md section 3, minus --graphs and --tag."""
    text = (ROOT / "docs" / "STRATEGY_REVIEW_RESPONSE_2026-11-02.md").read_text()
    cmd = text[text.index("--graphs G0,G2,G3 --tasks B"):]
    cmd = cmd[:cmd.index("2>&1")].replace("\\\n", " ").split()
    for flag in ("--graphs", "--tag"):
        i = cmd.index(flag)
        del cmd[i:i + 2]
    return cmd


def test_frozen_flags_file_matches_the_protocol_text():
    assert demo.frozen_flags() == _protocol_flags()


def test_demo_flags_change_only_the_epochs(tmp_path):
    f = tmp_path / "flags.txt"
    f.write_text("# comment\n--tasks B --epochs 300\n--strata\n")
    assert demo.frozen_flags(f) == ["--tasks", "B", "--epochs", "300", "--strata"]
    assert demo.demo_flags(7, f) == ["--tasks", "B", "--epochs", "7", "--strata"]


def test_benchmark_frozen_script_needs_graphs_and_tag():
    r = subprocess.run(["bash", str(ROOT / "scripts" / "benchmark_frozen.sh")], capture_output=True, text=True)
    assert r.returncode == 2 and "benchmark_frozen.sh <graphs" in r.stderr


def test_schema_card_check_from_the_command_line():
    r = subprocess.run([sys.executable, "pipelines/schema_card.py", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "0 non-BioLink terms" in r.stdout
    assert not r.stdout.startswith("schema card:")      # --check never writes the card


def test_hosted_check_ask_smoke_posts_the_two_asks_and_smoke_pages_only_gets(monkeypatch, capsys):
    hc = _hosted_check()
    calls = []

    def call(path, data=None, timeout=90):
        calls.append((path, data is not None))
        if path == "/api/downloads":
            return 200, 0.1, json.dumps({"available": True, "files": []}).encode()
        return 200, 0.1, b'{"steps": []}'
    monkeypatch.setattr(hc, "call", call)
    hc.ask_smoke([{"question": "q"}])
    assert calls == [("/api/pharos/ask", True), ("/api/ask", True)]
    calls.clear()

    class R:
        status, headers = 206, {"Content-Range": "bytes 0-1/9"}
        def read(self): return b"\x1f\x8b"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(hc.urllib.request, "urlopen", lambda req, timeout=60: R())
    hc.smoke_pages()
    assert calls and not any(posted for _, posted in calls)
    assert "gzip magic: True" in capsys.readouterr().out


def test_demo_main_end_to_end(tmp_path):
    """The whole `make demo` chain with 2 epochs (about a minute). Needs torch (requirements-ml.txt)."""
    pytest.importorskip("torch")
    pytest.importorskip("torch_geometric")
    assert demo.main(["--epochs", "2", "--out", str(tmp_path)]) == 0
    text = (tmp_path / "SUMMARY.txt").read_text().splitlines()
    assert text[0] == text[-1] == demo.LABEL
    assert (tmp_path / "compare.md").exists() and (tmp_path / "G2_B_kfold@demo.json").exists()
    s = json.loads((tmp_path / "G0_B_kfold@demo.json").read_text())["settings"]
    assert s["tag"] == "demo" and s["test_set"]["digest"]


# ---- isolation of the demo's subprocesses from the caller's environment (session 27 pitfall)
def test_child_env_points_pythonpath_at_the_private_root_only(tmp_path):
    env = demo.child_env(tmp_path, base={"PYTHONSAFEPATH": "1", "PYTHONPATH": str(ROOT), "HOME": "/h"})
    assert env["PYTHONPATH"] == str(tmp_path.resolve()) and "PYTHONSAFEPATH" not in env
    assert env["TMKG_SNAPSHOT_MODE"] == "offline" and env["HOME"] == "/h"


def test_import_check_loads_the_private_copy_even_with_the_sandbox_settings(tmp_path):
    work = demo.make_workdir(tmp_path / "root")
    hostile = dict(__import__("os").environ, PYTHONSAFEPATH="1", PYTHONPATH=str(ROOT))
    # without child_env the repo's pipelines/ would win (the session 27 failure)
    assert demo.import_check(sys.executable, work, hostile) is not None
    assert demo.import_check(sys.executable, work, demo.child_env(work, base=hostile)) is None


def test_main_refuses_to_run_when_the_import_check_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "import_check", lambda *a: "would load from the repo")
    monkeypatch.setattr(demo.subprocess, "run", lambda *a, **k: pytest.fail("a step ran"))
    assert demo.main(["--out", str(tmp_path)]) == 1
