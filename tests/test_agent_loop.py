"""CC-10: docs/AGENT_LOOP.md maps the brief's loop (Ingest, Filter, Clean, Limit, Maintain) to real modules, commands and
tests; the README table and the app's "For judges" page read the same mapping. Nothing here needs real data."""
import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pipelines")
import usability  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STEPS = ["Ingest", "Filter", "Clean", "Limit", "Maintain"]


@pytest.fixture(scope="module")
def loop():
    return usability.agent_loop(ROOT)


def test_the_five_steps_of_the_brief_in_order_with_its_own_wording(loop):
    assert loop["available"] and [s["step"] for s in loop["steps"]] == STEPS
    brief = (ROOT / loop["brief"]).read_text()
    for s in loop["steps"]:
        assert f"{s['step']}: {s['brief']}" in brief, s["step"]          # the quoted line is the brief's, not paraphrased
        assert len(s["plain"].split()) >= 25 and "\n" not in s["plain"]


def test_every_module_function_command_and_test_named_is_real(loop):
    for s in loop["steps"]:
        mod = ROOT / s["module"]
        assert mod.is_file(), s["module"]
        names = {n.name for n in ast.walk(ast.parse(mod.read_text())) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        assert set(s["functions"]) <= names, (s["step"], set(s["functions"]) - names)
        script = re.search(r"python3 (pipelines/\S+\.py)", s["command"])
        assert script and (ROOT / script.group(1)).is_file(), s["command"]
        for card in re.findall(r"sources/\S+\.yaml", s["command"]):
            assert (ROOT / card).is_file(), card
        assert s["tests"], s["step"]
        for t in s["tests"]:
            src = (ROOT / t).read_text()
            assert (ROOT / t).is_file() and re.search(r"^def test_", src, re.M), t


def test_each_step_has_a_test_that_actually_mentions_what_it_exercises(loop):
    for s in loop["steps"]:
        text = "\n".join((ROOT / t).read_text() for t in s["tests"])
        for token in s["exercises"]:
            assert re.search(rf"\b{re.escape(token)}\b", text), (s["step"], token)


def test_the_page_says_which_steps_use_a_model_what_runs_by_itself_and_what_a_scientist_approves(loop):
    """PK9: the weekly source check is the one part that runs by itself; everything that changes the graph is approved
    by a scientist. The sentences are shown on the For-judges page, so they hold no file path."""
    text = " ".join(loop["honest"])
    assert len(loop["honest"]) == 4 and "Filter" in text and "fixed programs" in text
    assert "Nothing runs by itself" not in text
    by_itself = [h for h in loop["honest"] if "runs by itself" in h]
    assert len(by_itself) == 1 and "weekly source check" in by_itself[0]
    for said in ("downloads no data", "rebuilds nothing", "changes no file", "opens an issue", "no secret", "skipped"):
        assert said in by_itself[0], said
    approves = [h for h in loop["honest"] if "scientist approves" in h]
    assert len(approves) == 1 and "Scientist decision" in approves[0] and "pruning settings" in approves[0]
    assert not re.search(r"[\w.-]+/[\w.-]+\.(?:py|yml|tsv|md)|`", by_itself[0] + approves[0])
    maintain = [s for s in loop["steps"] if s["step"] == "Maintain"][0]
    assert "runs by itself" in maintain["plain"] and "scientist then approves" in maintain["plain"]
    assert "nothing watches" not in maintain["plain"] and "source_check" in maintain["functions"]
    assert (ROOT / ".github/workflows/source_check.yml").is_file()
    doc = (ROOT / "docs/AGENT_LOOP.md").read_text()
    assert "## Maintain: what runs by itself and what a scientist approves" in doc and "/api/agent-log" in doc


def test_readme_says_what_runs_by_itself_and_what_a_scientist_approves():
    readme = (ROOT / "README.md").read_text()
    row = [l for l in readme.splitlines() if l.startswith("| Maintain |")][0]
    assert "by itself, weekly" in row and "approved by a scientist" in row and "maintain.py source-check" in row
    assert ".github/workflows/source_check.yml" in readme and "/api/agent-log" in readme


def test_the_agent_paragraph_is_the_same_in_the_three_documents():
    """PK5 (finding ES-06): README, the judge's card and this page carry one agent paragraph, written by headline.py, and
    none of them says the graph was built by an agent 'on its own'."""
    import headline
    want = headline.agent_statement()
    assert want.startswith("TrialMindKG is a semi-autonomous agent. It runs the loop Ingest, Filter, Clean, Limit, Maintain:")
    for rel in ("README.md", "docs/SUBMISSION.md", "docs/AGENT_LOOP.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        block = text.split(headline.AGENT_START, 1)[1].split(headline.AGENT_END, 1)[0].strip()
        assert block == want, rel
        assert "on its own" not in text and "Nothing runs by itself" not in text, rel
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.index("semi-autonomous agent") < readme.index("## What it does")     # on the first screen, not line 45


def test_agent_clauses_follow_the_shipped_packets():
    """A clause takes the sprint plan's wording only when its packet is listed as merged; otherwise what is true today."""
    import headline
    none, all_ = headline.agent_statement(frozenset()), headline.agent_statement({"PK4", "PK8", "PK9", "PK11"})
    assert "drafts the source card" not in none and "scheduled check" not in none and "auditing step" not in none
    assert "a person starts" in none and "dated ledger" in none
    plan = (ROOT / "docs" / "SPRINT_PLAN_2026-11-11.md")
    if plan.is_file():                                   # with every packet merged the paragraph is the plan's, word for word
        assert all_ in re.sub(r"\s+", " ", plan.read_text(encoding="utf-8"))
    assert headline.agent_statement({"PK8"}).count("drafts the source card") == 1


def test_run_log_rows_are_ledger_rows_with_computed_numbers():
    """PK5 (plan A4): every row of the dated 'what the agent did' log is a row of loop/ledger.tsv (same date and id), its
    numbers come from the result files, and README and this page carry the same table."""
    import headline
    rows = headline.run_log(ROOT)
    assert len(rows) == len(headline.RUN_LOG) >= 8 and all(r["in_ledger"] for r in rows), [r for r in rows if not r["in_ledger"]]
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)
    c = headline.comparisons(ROOT)
    shipped = next(r for r in rows if r["ledger"] == "D (CS-9 graphs)")
    assert f"{c['paths_g2']['mean']:+.3f}" in shipped["agent"] and f"{c['paths_g3']['mean']:+.3f}" in shipped["agent"]
    assert "{" not in " ".join(r["agent"] for r in rows)
    table = headline.run_log_markdown(rows)
    for rel in ("README.md", "docs/AGENT_LOOP.md"):
        assert table in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_readme_table_matches_the_mapping(loop):
    readme = (ROOT / "README.md").read_text()
    assert "## For judges: the brief's agent loop" in readme and "docs/AGENT_LOOP.md" in readme
    assert "docs/organizers/BRIEF.md" in readme
    for s in loop["steps"]:
        row = [l for l in readme.splitlines() if l.startswith(f"| {s['step']} |")]
        assert len(row) == 1, s["step"]
        assert f"`{s['module']}`" in row[0] and all(t in row[0] for t in s["tests"]), row[0]
        assert re.search(r"python3 pipelines/\S+\.py", s["command"]).group(0) in row[0], row[0]


def test_missing_doc_is_reported_not_raised(tmp_path):
    assert usability.agent_loop(tmp_path) == {"available": False, "doc": "docs/AGENT_LOOP.md"}


def test_the_app_serves_the_endpoint_and_renders_the_judges_tab():
    from fastapi.testclient import TestClient
    import app as appmod
    c = TestClient(appmod.app)
    r = c.get("/api/agent-loop").json()
    assert [s["step"] for s in r["steps"]] == STEPS and r["honest"]
    home = c.get("/").text
    assert 'id="tabJudges"' in home and 'id="judges"' in home and "showJudges" in home
