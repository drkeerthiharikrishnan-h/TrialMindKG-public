"""Tests for loop/loop.py: scorecard checks, scoring, next actions, ledger. Fixtures only; no data needed."""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loop_mod", ROOT / "loop" / "loop.py")
loop = importlib.util.module_from_spec(spec)
sys.modules["loop_mod"] = loop
spec.loader.exec_module(loop)


def write_card(tmp_path, checks):
    card = {"criteria": {"a": {"weight": 60, "checks": checks[:2]}, "b": {"weight": 40, "checks": checks[2:]}}}
    (tmp_path / "loop").mkdir(exist_ok=True)
    p = tmp_path / "loop" / "scorecard.yaml"
    p.write_text(yaml.safe_dump(card))
    return p


def test_file_exists_and_grep(tmp_path):
    (tmp_path / "f.md").write_text("verdicts keyed by HGNC and UniProt")
    assert loop.run_check({"kind": "file_exists", "path": "f.md"}, tmp_path)["status"] == "pass"
    assert loop.run_check({"kind": "file_exists", "path": "nope.md"}, tmp_path)["status"] == "fail"
    assert loop.run_check({"kind": "grep", "file": "f.md", "pattern": "HGNC"}, tmp_path)["status"] == "pass"
    assert loop.run_check({"kind": "grep", "file": "f.md", "pattern": "MONDO"}, tmp_path)["status"] == "fail"
    assert loop.run_check({"kind": "grep", "file": "missing.md", "pattern": "x"}, tmp_path)["status"] == "fail"


def test_command_and_unknown_kind(tmp_path):
    assert loop.run_check({"kind": "command", "argv": [sys.executable, "-c", "pass"]}, tmp_path)["status"] == "pass"
    assert loop.run_check({"kind": "command", "argv": [sys.executable, "-c", "raise SystemExit(3)"]}, tmp_path)["detail"] == "exit 3"
    assert loop.run_check({"kind": "command", "argv": ["no-such-binary-xyz"]}, tmp_path)["status"] == "fail"
    assert loop.run_check({"kind": "telepathy"}, tmp_path)["status"] == "fail"


def test_url_without_state_fails(tmp_path):
    r = loop.run_check({"kind": "url", "state_key": "hosted_url"}, tmp_path, {"hosted_url": None})
    assert r["status"] == "fail" and "not set" in r["detail"]


def test_score_counts_only_passes(tmp_path):
    (tmp_path / "ok.txt").write_text("x")
    checks = [dict(id="c1", weight=30, kind="file_exists", path="ok.txt"),
              dict(id="c2", weight=30, kind="file_exists", path="missing.txt"),
              dict(id="c3", weight=40, kind="file_exists", path="ok.txt")]
    card = write_card(tmp_path, checks)
    res = loop.score(tmp_path, card)
    assert res["criteria"]["a"]["earned"] == 30 and res["criteria"]["b"]["earned"] == 40
    assert res["total"] == 70
    assert [r["status"] for r in res["results"]] == ["pass", "fail", "pass"]


def test_next_actions_skips_checks_with_open_task(tmp_path):
    res = {"results": [dict(id="x", weight=5, status="fail", criterion="a", detail=""),
                       dict(id="y", weight=9, status="fail", criterion="a", detail=""),
                       dict(id="z", weight=3, status="pass", criterion="a", detail="")]}
    tasks = [dict(id="T-1", status="open", closes=["y"]), dict(id="T-2", status="done", closes=["x"])]
    open_tasks, gaps = loop.next_actions(res, tasks)
    assert [t["id"] for t in open_tasks] == ["T-1"]
    assert [g["id"] for g in gaps] == ["x"]          # y is covered by an open task; T-2 is done so x is a gap


def test_ledger_append_writes_header_once_and_strips_tabs(tmp_path):
    p = tmp_path / "ledger.tsv"
    loop.ledger_append(p, dict(id="E1", hypothesis="a\tb\nc", preregistered_threshold="t", result_file="f",
                               decision="accept", judged_by="me", date="2026-11-04"))
    loop.ledger_append(p, dict(id="E2", date="2026-11-05"))
    lines = p.read_text().splitlines()
    assert lines[0].split("\t") == loop.LEDGER_COLUMNS
    assert len(lines) == 3 and lines[1].split("\t")[2] == "a b c"
    assert all(len(l.split("\t")) == len(loop.LEDGER_COLUMNS) for l in lines)


def test_real_scorecard_and_packets_are_consistent():
    criteria, checks = loop.load_scorecard()
    assert sum(criteria.values()) == 100
    assert {c["criterion"] for c in checks} == set(criteria)
    for name, w in criteria.items():
        assert sum(c["weight"] for c in checks if c["criterion"] == name) == w, name
    ids = [c["id"] for c in checks]
    assert len(ids) == len(set(ids))
    tasks = loop.load_tasks()
    assert tasks and len({t["id"] for t in tasks}) == len(tasks)
    for t in tasks:
        for cid in t.get("closes", []):
            assert cid in ids, (t["id"], cid)
        for key in ("title", "owner", "objective", "acceptance", "stop_rule", "editable_paths"):
            assert key in t, (t["id"], key)


def test_every_ledger_row_has_the_header_column_count():
    """loop/ledger.tsv: every row has exactly as many tab-separated columns as the header (CC-25)."""
    lines = [l for l in (ROOT / "loop" / "ledger.tsv").read_text().split("\n") if l]
    n = len(lines[0].split("\t"))
    assert n == 7 and lines[0].split("\t") == loop.LEDGER_COLUMNS
    bad = [(i + 1, len(l.split("\t"))) for i, l in enumerate(lines) if len(l.split("\t")) != n]
    assert bad == [], f"rows with the wrong number of columns (row, columns): {bad}"
