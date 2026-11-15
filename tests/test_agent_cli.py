"""Tests for bin/agent-cli (the agent's one-place lookup tool). Pure functions on tiny fixtures; the git / process
subcommands are exercised only through their message builders."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def ac():
    loader = importlib.machinery.SourceFileLoader("agent_cli", str(ROOT / "bin" / "agent-cli"))
    spec = importlib.util.spec_from_loader("agent_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


NEXT = """# Next steps
## A. Week 1
1. [x] MAC done thing.
7e. [ ] MAC now two blocks.
       ```bash
       python3 -m pipelines.linkpred.run --graphs G0
       ```
7f. [~] CLOUD half done
       second line of 7f.
"""

STATUS = """# Status
## 0. Session log (latest first)
- 2026-11-01 (cloud, 7c read): first entry text
  continues here.
- 2026-10-31 (mac): older entry.
## 1. Other
- not a log entry
"""


def test_next_items_parse_state_owner_and_continuations(ac):
    items = ac.next_items(NEXT)
    assert [i["id"] for i in items] == ["7e", "7f"]                       # done item filtered out
    assert items[0]["state"] == "open" and items[0]["who"] == "MAC"
    assert items[1]["state"] == "running" and "second line of 7f" in items[1]["text"]
    assert [i["id"] for i in ac.next_items(NEXT, only_open=False)] == ["1", "7e", "7f"]


def test_step_text_returns_the_whole_block_including_fences(ac):
    block = ac.step_text(NEXT, "7e")
    assert block.startswith("7e. [ ]") and "--graphs G0" in block and "7f." not in block
    assert ac.step_text(NEXT, "99") == ""


def test_status_entries_latest_first_with_continuation(ac):
    ents = ac.status_entries(STATUS)
    assert [e["date"] for e in ents] == ["2026-11-01", "2026-10-31"]
    assert ents[0]["head"] == "cloud, 7c read" and "continues here" in ents[0]["text"]


def test_defs_and_fn_source_use_the_ast(ac, tmp_path, monkeypatch):
    src = 'def a(x, y):\n    """First line.\n    more"""\n    return x\n\nclass B:\n    def m(self):\n        pass\n'
    (tmp_path / "m.py").write_text(src)
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    rows = ac.defs("m.py")
    assert [(r["line"], r["name"]) for r in rows] == [(1, "a(x, y)"), (6, "B"), (7, "m(self)")]
    assert rows[0]["doc"] == "First line."
    assert ac.fn_source("m.py", "m").splitlines() == ["7\t    def m(self):", "8\t        pass"]
    assert ac.fn_source("m.py", "zz") == ""


def test_section_and_markdown_sections(ac, tmp_path, monkeypatch):
    (tmp_path / "d.md").write_text("# T\nintro\n## Week 1: build\nline a\n## Week 2\nline b\n")
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    out = ac.section("d.md", "week 1")
    assert out[0].startswith("## Week 1: build") and out[1:] == ["line a"]
    assert ac.section("d.md", "nothing") == []


def test_card_summary_reads_list_style_nodes_and_edges(ac, tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "x.yaml").write_text(
        "name: x\nlayer: L1\nlookups: {a_to_b: {table: t, key: k, value: v}}\n"
        "nodes:\n  - {table: t, id: {column: c, prefix: P}, category: 'biolink:Gene'}\n"
        "edges:\n  - name: e1\n    table: t\n    subject: {columns: [{column: s, lookup: a_to_b}, {column: s, prefix: P}], category: 'biolink:Gene'}\n"
        "    predicate: biolink:related_to\n    object: {column: o, prefix: Q, category: 'biolink:Disease'}\n    knowledge_level: knowledge_assertion\n")
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    out = ac.card_summary("sources/x.yaml")
    assert out["name"] == "x" and out["lookups"] == ["a_to_b"]
    node, edge = out["blocks"]
    assert node["kind"] == "node" and node["category"] == "biolink:Gene"
    assert edge["subject"] == "biolink:Gene<-s@a_to_b|s:P" and edge["object"] == "biolink:Disease<-o:Q"


def test_results_table_reads_linkpred_json(ac, tmp_path, monkeypatch):
    d = tmp_path / "outputs" / "linkpred"
    d.mkdir(parents=True)
    (d / "G0_B_kfold.json").write_text(json.dumps({
        "settings": {"epochs": 50, "hidden": 64, "sample_fraction": 0.01, "wiring": {"reverse_edges": True, "neg_pool": "all"}},
        "summary": {"auroc": {"mean": 0.87, "ci95": 0.01}, "auroc_degree_dst": {"mean": 0.8, "ci95": 0.0}}}))
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    rows = ac.results()
    assert rows[0]["auroc"] == "0.87+-0.01" and rows[0]["degree"] == "0.8+-0.0" and rows[0]["fraction"] == 0.01
    assert json.loads(rows[0]["wiring"]) == {"reverse_edges": True}          # defaults are not shown


def test_tsv_head_counts_rows_of_gz(ac, tmp_path, monkeypatch):
    import gzip
    with gzip.open(tmp_path / "t.tsv.gz", "wt") as f:
        f.write("id\tv\n1\ta\n2\tb\n3\tc\n")
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    out = ac.tsv_head("t.tsv.gz", n=2)
    assert out["columns"] == ["id", "v"] and out["rows"] == 3 and out["head"][1] == {"id": "2", "v": "b"}


def test_test_for_maps_a_module_to_its_test_files(ac):
    hits = ac.test_for("pipelines/make_graphs.py")
    assert "tests/test_make_g0.py" in hits and "tests/test_graph_harmonise.py" in hits
    assert ac.test_for("pipelines/linkpred/baselines.py") == ["tests/test_linkpred_baselines.py"]


def test_commit_message_has_the_mandatory_trailers(ac):
    msg = ac.commit_message("subject\n\nbody", "https://claude.ai/code/session_x")
    assert msg.endswith("Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>\nClaude-Session: https://claude.ai/code/session_x\n")
    assert "Claude-Session" not in ac.commit_message("s", "")


def test_do_commit_refuses_secrets_and_empty_paths(ac):
    with pytest.raises(SystemExit, match="rule 5"):
        ac.do_commit("m", [".env"], "", dry=True)
    with pytest.raises(SystemExit, match="name the paths"):
        ac.do_commit("m", [], "", dry=True)
    assert ac.do_commit("m", ["bin/agent-cli"], "", dry=True)["would_add"] == ["bin/agent-cli"]


def test_mac_block_has_the_standard_shape(ac):
    block = ac.mac_block(["python3 x.py"], pull=True, paste="the last line")
    assert block.splitlines()[1] == "cd ~/projects/TrialMindKG && source .venv/bin/activate && git pull --no-rebase --no-edit origin main"
    assert block.splitlines()[2] == "python3 x.py" and block.endswith("Paste back: the last line.")
    assert "git pull" not in ac.mac_block(["x"], pull=False)


def test_command_parts_strip_heredocs_and_split_compounds(ac):
    cmd = "git status && python3 - <<'EOF'\nprint('git push')\nEOF\ngit log -1; echo done"
    assert ac.command_parts(cmd) == ["git status", "python3 - <<HEREDOC", "git log -1"]


def test_repeats_classifies_tool_calls_from_a_transcript(ac, tmp_path):
    lines = []

    def use(uid, name, inp):
        lines.append(json.dumps({"message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}}))
        lines.append(json.dumps({"message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": "x" * 10}]}}))
    use("1", "Bash", {"command": "git log --oneline -1 && git status --short"})
    use("2", "Bash", {"command": 'grep -n "def " pipelines/run.py'})
    use("3", "Read", {"file_path": "/r/docs/NEXT_STEPS.md"})
    use("4", "Bash", {"command": "timeout 500 python3 -m pytest -q | tail -1"})
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(lines) + "\n")
    out = ac.repeats([str(p)])
    by = {r["subcommand"]: r for r in out["rows"]}
    assert out["tool_calls"] == 4
    assert by["start/branches"]["calls"] == 2 and by["start/branches"]["bytes_read"] == 10   # bytes once per call
    assert by["start/branches"]["questions"] == 1                                            # one tool call, two parts
    assert by["defs"]["calls"] == 1 and by["next/status/rules"]["calls"] == 1 and by["test"]["calls"] == 1


def test_cli_main_prints_json_with_flag_after_the_subcommand(ac, capsys):
    ac.main(["next", "--json", "-n", "2"])
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list) and all("id" in i for i in out)


# --- continuity: handoff, transcript, digest, manifest, end -------------------------------------------------------
def test_scrub_hides_keys_and_passwords(ac):
    s = ac.scrub("use nvapi-abcdefghijklmnop and password=hunter22 and " + "AKIA" + "ABCDEFGHIJKLMNOP but keep MONDO:0005148")
    assert "nvapi-" not in s and "hunter22" not in s and "AKIAABCD" not in s and "MONDO:0005148" in s


def test_handoff_markdown_is_computed_from_the_state(ac):
    h = {"date": "2026-11-01 12:00 UTC", "where": "cloud", "torch": False, "graphs_on_disk": [], "layers_on_disk": ["pharos"],
         "git": {"branch": "b", "head": "abc1234 subject", "origin_main": "abc1234 subject", "ahead_of_main": "0", "behind_main": "0",
                 "in_sync_with_main": True, "dirty": 0, "dirty_files": []},
         "open": [{"id": "7e", "state": "open", "who": "MAC", "text": "MAC run block 1"}],
         "running": [{"id": "6", "state": "running", "who": "MAC", "text": "MAC make_graphs"}],
         "next_mac": {"id": "7e", "text": "MAC run block 1"}, "next_cloud": None, "next_owner": None,
         "log": [{"date": "2026-11-01", "head": "cloud", "text": "did things"}]}
    md = ac.handoff_markdown(h)
    assert "- in sync with main: yes" in md and "- MAC: step 7e: MAC run block 1" in md
    assert "- CLOUD: no open item" in md and "- step 6: MAC make_graphs" in md and "layers on disk: pharos" in md
    h["git"].update(in_sync_with_main=False, ahead_of_main="2", behind_main="1")
    assert "NO (ahead 2, behind 1)" in ac.handoff_markdown(h)


def _transcript(tmp_path):
    rows = [
        {"type": "user", "message": {"role": "user", "content": "please build the CLI token=abc123secret"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Building it now."},
                                                                           {"type": "tool_use", "id": "1", "name": "Bash", "input": {"command": "git commit -m x"}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "<system-reminder>ignored</system-reminder>"}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "and push it"}]}},
    ]
    p = tmp_path / "abcdefgh-1234.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_transcript_rows_keep_user_claude_and_tool_lines_only(ac, tmp_path):
    rows = ac.transcript_rows(str(_transcript(tmp_path)), width=40)
    assert [r["who"] for r in rows] == ["USER", "CLAUDE", "TOOL", "USER"]
    assert rows[0]["text"].startswith("please build the CLI [REDACTED]")      # secret scrubbed, system reminder dropped
    assert rows[2]["text"] == "Bash: git commit -m x" and rows[3]["text"] == "and push it"


def test_session_digest_lists_owner_messages_and_commit_calls(ac, tmp_path):
    md = ac.session_digest(str(_transcript(tmp_path)))
    assert md.startswith("# Session digest ") and "(abcdefgh)" in md
    assert "1. please build the CLI [REDACTED]" in md and "2. and push it" in md
    assert "2 user messages, 1 tool calls" in md


def test_continuity_check_reports_missing_files(ac, tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    (tmp_path / "ok.md").write_text("x")
    (tmp_path / "m.yaml").write_text("start_sequence: [a]\nend_sequence: [b]\nlayers:\n  memory:\n    - {file: ok.md}\n    - {file: gone.md}\n  skills:\n    - {dir: nodir}\n  harness:\n    settings: ok.md\n")
    monkeypatch.setattr(ac, "ROOT", tmp_path)
    out = ac.continuity_check(tmp_path / "m.yaml")
    assert out["ok"] is False and out["missing"] == ["gone.md", "nodir"] and out["listed"] == 4
    assert out["layers"] == {"memory": 2, "skills": 1, "harness": 1}


def test_real_manifest_names_only_existing_files(ac):
    pytest.importorskip("yaml")
    out = ac.continuity_check()
    assert out["ok"], out["missing"]


def test_end_session_refuses_when_work_is_uncommitted(ac, monkeypatch):
    monkeypatch.setattr(ac, "git_state", lambda fetch=False: {"dirty_files": [" M pipelines/x.py", "?? docs/HANDOFF.md"], "dirty": 2})
    out = ac.end_session("m", "", run=False)
    assert "blocked" in out and out["dirty_files"] == [" M pipelines/x.py"]
