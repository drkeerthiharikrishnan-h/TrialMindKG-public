"""PK9: GET /api/agent-log (pipelines/routes_agent.py), the dated log of what the agent did and what the scientist
decided, in user words, built from committed files only. Two kinds of test: on the repository's own records (every
entry cites a committed file that exists; the wording check passes on the JSON; numbers are the records' numbers), and
on a small SYNTHETIC set of records in a temporary folder (each sentence is computed from the record it cites)."""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, "pipelines")
import routes_agent as ra  # noqa: E402
from schema_card import parse_summary  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# The wording rule of the packets, written out here independently of routes_agent.internal_terms(): nothing a user
# sees may hold a file path, a run tag, a graph code (G0-G4), a packet id or a model-operations word.
FORBIDDEN = {
    "file path": r"\b(?:outputs|data|pipelines|scripts|docs|loop|logs|sources|tests)/|~/|\.(?:py|md|tsv|csv|json|ya?ml|gz|xlsx)\b",
    "run tag": r"@\w|frozen_[ab]|diag_|kfold",
    "graph code": r"\bG[0-4]R?\b",
    "packet id": r"\b(?:CC|CS|PK)-?R?\d+|\bE0\d\d\b|\bES-\d+",
    "layer file name": r"opentargets_layer|chembl37|_hierarchy\b|\bdto\b",
    "raw graph term": r"biolink:|infores:|knowledge_assertion|text_co_occurrence|statistical_association|not_provided",
    "model-operations word": r"(?i)\b(?:gpu|h100|brev|ec2|s3|cloudfront|epochs?|seed|digest|docker|commit|branch|nim|"
                             r"stand-in|snapshot|claude|codex|mac|pytest|svd)\b",
}


def strings(obj):
    """Every string value in a JSON-like object (the keys are field names for the page's code, not text a user reads)."""
    if isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)
    elif isinstance(obj, str):
        yield obj


@pytest.fixture(scope="module")
def log():
    return ra.agent_log(ROOT)


@pytest.fixture(scope="module")
def raw():
    return ra.build(ROOT)


def committed():
    """The paths git tracks, or None where git cannot be asked (then the test checks the files exist)."""
    try:
        r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return set(r.stdout.splitlines()) if r.returncode == 0 and r.stdout else None


# ------------------------------------------------------------------------------------ on the repository's records
def test_every_entry_cites_a_committed_file_that_exists(log, raw):
    tracked = committed()
    assert log["available"] and len(log["entries"]) == len(raw) >= 10
    files = ra.record_files(ROOT)
    for e, r in zip(log["entries"], raw):
        assert e["cites"] and len(e["cites"]) == len(r["_cites"]), e["title"]
        for cite, (title, rel, where) in zip(e["cites"], r["_cites"]):
            assert cite == {"record": title, "where": where} and files[title] == rel
            assert (ROOT / rel).is_file(), rel
            if tracked is not None:
                assert rel in tracked, f"{rel} is cited but not committed"
    assert sorted(files) == log["records"]
    assert len(set(files.values())) == len(files)                         # one title, one file


def test_the_log_is_built_from_the_records_the_packet_names(raw):
    cited = {rel for e in raw for _, rel, _ in e["_cites"]}
    assert "loop/ledger.tsv" in cited and "outputs/pharos/prune_report.md" in cited
    assert {"outputs/pharos/summary.md", "outputs/opentargets/summary.md"} <= cited
    assert any(rel == "outputs/maintain_diff.md" or rel.startswith("outputs/maintain/diff_") for rel in cited)
    allowed = re.compile(r"loop/ledger\.tsv|outputs/[^/]+/summary\.md|outputs/pharos/prune_report\.md|outputs/maintain_diff\.md|"
                         r"outputs/maintain/diff_[^/]+\.md|data/reference/pharos_manifest\.tsv|data/external/DATA_MANIFEST\.tsv")
    assert all(allowed.fullmatch(rel) for rel in cited), cited


def test_the_wording_test_passes_on_the_json(log):
    assert json.loads(json.dumps(log)) == log                             # plain JSON, nothing else
    for s in strings(log):
        for kind, pat in FORBIDDEN.items():
            assert not re.search(pat, s), f"{kind} in: {s}"
        assert ra.internal_terms(s) == [], s
    assert not any(k.startswith("_") for e in log["entries"] for k in e)  # the files behind the cites stay server-side


def test_the_wording_check_itself_finds_each_kind_of_internal_term():
    bad = ["see outputs/pharos/summary.md", "written by prune_kg.py", "G2 minus G0", "run kfold@frozen_a", "tag diag_gnnpath_1m",
           "packet CC-28 part B", "E006 pending", "biolink:Gene", "knowledge_assertion", "on a Brev H100 GPU", "300 epochs",
           "the Mac run", "opentargets_layer", "PK9"]
    for s in bad:
        assert ra.internal_terms(s), s
    good = ["Built the Pharos layer from release 26.09: 1,034,552 links.", "PASS if >= 60 of 100 pruned edges are judged unsupported",
            "text mining (two names in the same text)", "keep / prune", "3.19.2 / TCRD 6.13.5", "gene-disease links"]
    for s in good:
        assert ra.internal_terms(s) == [], s


def test_entries_are_dated_newest_first_and_say_where_the_date_comes_from(log):
    dates = [e["date"] for e in log["entries"]]
    assert all(d and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in dates)     # in the repository every entry is dated
    assert dates == sorted(dates, reverse=True)
    assert log["counts"]["first_date"] == dates[-1] and log["counts"]["last_date"] == dates[0]
    for e in log["entries"]:
        assert e["date_from"] and e["date_words"] and e["date_words"].endswith(e["date"][:4])
        assert e["step"] in {"Ingest", "Clean", "Limit", "Maintain", "Decision", "Log"} and e["title"] and e["text"]
        assert e["by"] in {"program", "agent", "scientist"}
    ledger_dates = {r["date"] for r in ra.ledger_rows(ROOT)}
    for e in log["entries"]:
        if "experiment log" in e["date_from"] or "experiment-log" in e["date_from"]:
            assert e["date"] in ledger_dates, e["title"]


def test_scientist_decisions_are_marked_and_each_is_a_row_of_the_experiment_log(log):
    rows = {r["line"] - 1: r for r in ra.ledger_rows(ROOT)}
    marked = [e for e in log["entries"] if e["marked"]]
    assert marked and all(e["marked"] == "Scientist decision" and e["by"] == "scientist" and e["step"] == "Decision" for e in marked)
    assert all(e["marked"] is None for e in log["entries"] if e["by"] != "scientist")
    assert log["counts"]["scientist_decisions"] == len(marked) == sum(ra._is_scientist(r) for r in rows.values())
    for e in marked:
        n = int(re.fullmatch(r"row (\d+)", e["cites"][0]["where"]).group(1))
        r = rows[n]
        assert e["cites"][0]["record"] == "Experiment log" and r["date"] == e["date"]
        assert re.search(r"[Oo]wner", r["judged_by"] + r["hypothesis"][:40])       # the log itself names the scientist
        if "rule" in e:
            assert r["decision"].lower().startswith("pre-registered")
            for clause in e["rule"].rstrip(".").split(". "):                       # the rule is the log's own words
                assert clause.rstrip(".") in r["preregistered_threshold"], clause
    assert any("rule" in e and "PASS if" in e["rule"] for e in marked)
    # a row the agent judged alone is never shown as the scientist's
    agent_only = [r for r in rows.values() if r["judged_by"] == "Claude Science" and not r["hypothesis"].startswith("(DECISION, owner")]
    assert agent_only and not any(ra._is_scientist(r) for r in agent_only)


def test_the_agents_daily_entries_count_every_other_row_once(log):
    rows = ra.ledger_rows(ROOT)
    daily = [e for e in log["entries"] if e["by"] == "agent"]
    counted = sum(int(re.search(r"added (\d+) entr", e["text"]).group(1)) for e in daily)
    assert counted == len(rows) - log["counts"]["scientist_decisions"]
    for e in daily:
        parts = re.search(r"\((.*?)\)\. Each entry", e["text"]).group(1).split("; ")
        assert sum(int(p.rsplit(": ", 1)[1]) for p in parts) == int(re.search(r"added (\d+) entr", e["text"]).group(1))
    assert any("results that did not pass their rule" in e["text"] for e in daily)   # failures are in the log too


def test_numbers_in_the_build_and_pruning_entries_are_the_records_numbers(raw):
    for e in raw:
        rel = e["_cites"][0][1]
        text = (ROOT / rel).read_text()
        if re.fullmatch(r"outputs/[^/g][^/]*/summary\.md|outputs/g[a-z][^/]*/summary\.md", rel) and e["title"].endswith("layer built"):
            s = parse_summary(text)
            assert f"{s['n_nodes']:,} entities and {s['n_edges']:,} links" in e["text"], rel
            rows_in, links = sum(x["rows"] for x in s["edges"]), sum(x["edges"] for x in s["edges"])
            assert f"{rows_in:,} source rows were kept" in e["text"]
            if rows_in > links:
                assert f"{rows_in - links:,} of them repeated a link" in e["text"]
        if rel == "outputs/pharos/prune_report.md":
            scored, pruned = (int(x.replace(",", "")) for x in re.search(
                r"edges scored: \*\*([\d,]+)\*\*; verdict 'prune': \*\*([\d,]+)\*\*", text).groups())
            assert f"{scored:,} Pharos links" in e["text"] and f"{pruned:,} ({100 * pruned / scored:.2f}%)" in e["text"]
            assert f"{scored - pruned:,} kept" in e["text"] and "Open Targets, which the pruner never sees" in e["text"]
            for n in re.findall(r"\d[\d,]{4,}", e["text"]):                # every large number is in the report, or is
                assert n in text or n == f"{scored - pruned:,}", n         # the one difference computed above
        if rel == "data/reference/pharos_manifest.tsv":
            rows = list(csv.DictReader(text.splitlines(), delimiter="\t"))
            day = [r for r in rows if r["retrieved"][:10] == e["date"]]
            assert f"{len(day)} tables" in e["text"] and f"{sum(int(r['rows']) for r in day):,} rows in all" in e["text"]


def test_the_app_serves_the_log_from_its_own_router_module():
    from fastapi.testclient import TestClient
    import app as appmod
    r = TestClient(appmod.app).get("/api/agent-log")
    assert r.status_code == 200 and r.json() == json.loads(json.dumps(ra.agent_log(ROOT)))
    src = (ROOT / "pipelines/app.py").read_text()
    assert src.count("include_router(routes_agent.router)") == 1 and "/api/agent-log\")" not in src.replace("GET /api/agent-log", "")


# ---------------------------------------------------------------------- on SYNTHETIC records in a temporary folder
LEDGER = "date\tid\thypothesis\tpreregistered_threshold\tresult_file\tdecision\tjudged_by\n" + "\n".join("\t".join(r) for r in [
    ("2030-01-02", "X1", "(run) layer alpha rebuilt: 90 nodes, 1,200 edges", "-", "-", "done", "Claude Science"),
    ("2030-01-03", "X2", "PRE-REGISTERED: blinded spot check of 20 edges", "PASS if >= 12 of 20 are judged wrong; sheet in "
     "outputs/alpha/sheet.xlsx; seed 7 fixed", "-", "pre-registered", "Owner (rater) + Claude Science"),
    ("2030-01-03", "X3", "(DECISION, owner) keep the hosted demo small", "-", "-", "decided", "Claude Science"),
    ("2030-01-03", "X4", "enrichment helps", "CI above 0", "-", "rejected: no gain", "Claude Science"),
    ("2030-01-04", "X5", "assembled: 2,150 edges in all", "-", "-", "done", "Claude Science"),
    ("not a date", "X6", "ignored", "-", "-", "done", "Claude Science"),
]) + "\n"
SUMMARY = """# alpha graph (source version 9.1 (file data/alpha/v.txt))

90 nodes, 1,200 edges.

## Nodes by class

| class | nodes |
|---|---|
| biolink:Gene | 90 |

## Edges by predicate and primary knowledge source

| predicate | primary knowledge source | edges | source rows |
|---|---|---|---|
| biolink:related_to | infores:alpha | 1,000 | 1,300 |
| biolink:has_phenotype | infores:alpha | 200 | 200 |

## Source rows not in the graph (identifier could not be standardised)

| edge spec | reason | rows |
|---|---|---|
| a_b | no subject identifier | 40 |
| a_c | no subject identifier | 2 |
| a_c | no usable object identifier | 7 |
"""
DIFF = """# Rebuild diff: alpha

alpha: 0 node classes and 3 predicate/source rows changed; edges 800 -> 1,150 (+350 (+43.8%)).

| | before | after | change |
|---|---|---|---|
| nodes | 80 | 90 | +10 (+12.5%) |
| edges | 800 | 1,150 | +350 (+43.8%) |

## Edges by predicate | primary source

| | before | after | change |
|---|---|---|---|
| biolink:related_to | infores:alpha | 800 | 950 | +150 (+18.8%) |
| biolink:has_phenotype | infores:alpha | - | 200 | new |
| biolink:located_in | infores:old | 30 | - | gone |
"""
PRUNE = """# Pruning report - pharos

- edges scored: **1,000**; verdict 'prune': **50** (**5.00%**); edges marked protected in the reasons: 12

## Three thresholds: pruned share and Open Targets corroboration

| threshold | confidence cut | edges pruned | share of all edges | share of gene-disease edges | pruned that are text co-occurrence | non-text G-D pruned: n / in OT / OT >= 0.2 | non-text G-D kept: in OT / OT >= 0.2 | TIN-X pruned: n / in OT | TIN-X kept: in OT |
|---|---|---|---|---|---|---|---|---|---|
| shipped | 0.3 | 50 | 5.00% | 1% | 4% | 20 / 5.0% / 1.0% | 30.0% / 20.0% | 3 / 0.0% | 1.2% |
| other | 0.4 | 99 | 9.90% | 1% | 4% | 99 / 9.9% / 1.0% | 99.9% / 20.0% | 3 / 0.0% | 1.2% |

## By knowledge level

| knowledge level | edges | pruned | share pruned |
|---|---|---|---|
| text_co_occurrence | 600 | 10 | 1.67% |
| knowledge_assertion | 400 | 40 | 10.00% |

## By source

| source | edges | pruned | share pruned |
|---|---|---|---|
| SourceA | 700 | 25 | 3.57% |
| made_up_rows | 25 | 25 | 100.00% |
| SourceB | 300 | 0 | 0.00% |
"""


@pytest.fixture()
def synthetic(tmp_path):
    """SYNTHETIC records (not real builds) laid out like the repository's."""
    files = {"loop/ledger.tsv": LEDGER, "sources/alpha.yaml": "name: alpha\n", "outputs/alpha/summary.md": SUMMARY, "outputs/maintain/diff_alpha.md": DIFF,
             "outputs/pharos/prune_report.md": PRUNE,
             "outputs/g0/summary.md": "# G0 (alpha)\n\n90 nodes, 1,200 edges; 0 edges removed by pruning; 0 node rows re-typed.\n",
             "outputs/g3/summary.md": "# G3 (alpha + mondo_hierarchy)\n\n100 nodes, 2,150 edges; 50 edges removed by pruning; "
                                      "0 edge rows not used; 6 node rows re-typed to one class per id prefix.\n",
             "data/reference/pharos_manifest.tsv": "group\tmodel\tlist\trows\ttargets\tsha256\tversion\tretrieved\n"
                                                   "a\tTarget\tList A\t10\t1\tx\tpharosX\t2030-01-01T00:00:00Z\n"
                                                   "b\tTarget\tList B\t25\t1\ty\tpharosX\t2030-01-01T05:00:00Z\n",
             "data/external/DATA_MANIFEST.tsv": "source\tfile\turl\tretrieved_utc\tbytes\tsha256\n"
                                                "mondo\tm/a.json\tu\t2030-01-01T00:00:00Z\t1\tx\n"
                                                "mondo\tm/a.json\tu\t2030-01-01T09:00:00Z\t1\tx\n"
                                                "newsource\tn/b.txt\tu\t2030-01-01T00:00:00Z\t1\ty\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def entry(root, title_part):
    hits = [e for e in ra.agent_log(root)["entries"] if title_part in e["title"]]
    assert len(hits) == 1, (title_part, [e["title"] for e in ra.agent_log(root)["entries"]])
    return hits[0]


def test_a_layer_entry_says_rows_kept_merged_and_left_out_with_the_reasons(synthetic):
    e = entry(synthetic, "alpha layer built")
    assert "from release 9.1:" in e["text"]                               # the bracket with a file path is dropped
    assert "90 entities and 1,200 links" in e["text"]
    assert "1,500 source rows were kept; 300 of them repeated a link" in e["text"]
    assert "49 source rows were left out" in e["text"] and "(no subject identifier: 42; no usable object identifier: 7)" in e["text"]
    assert e["date"] == "2030-01-02" and "first day the experiment log states this count" in e["date_from"]
    assert [c["record"] for c in e["cites"]] == ["Build record of the alpha layer", "Experiment log"] and e["by"] == "program"


def test_pull_and_download_entries_count_tables_rows_files_and_sources(synthetic):
    e = entry(synthetic, "Pharos pulled")
    assert "release pharosX" in e["text"] and "2 tables: 35 rows in all" in e["text"] and '"List B" with 25 rows' in e["text"]
    d = entry(synthetic, "Reference files downloaded")
    assert "Downloaded 2 reference files from 2 public sources (MONDO, newsource)" in d["text"] and d["date"] == "2030-01-01"


def test_the_pruning_entry_gives_kept_pruned_and_why(synthetic):
    e = entry(synthetic, "keep or prune")
    assert "1,000 Pharos links" in e["text"] and "50 (5.00%) were marked prune and 950 kept" in e["text"]
    assert "12 links were protected" in e["text"]
    assert "stated by a source as knowledge: 40 of 400 pruned (10.00%); text mining (two names in the same text): 10 of 600" in e["text"]
    assert "SourceA: 25 of 700 (3.57%)" in e["text"] and "made_up_rows" not in e["text"]      # a code name is not shown
    assert "of the 20 pruned gene-disease links that are not text-mined, 5.0% are also in Open Targets; of the kept ones, 30.0%" in e["text"]
    assert e["step"] == "Clean" and e["date"] is None and "no dated record" in e["date_from"]


def test_the_change_entry_gives_before_after_new_gone_and_whether_a_later_build_exists(synthetic):
    e = entry(synthetic, "compared with the build before it")
    assert "links went from 800 to 1,150 (+350) and entities from 80 to 90 (+10)" in e["text"]
    assert "1 new, 1 gone, 1 changed in size" in e["text"] and "has phenotype from alpha (200)" in e["text"]
    assert "today has 1,200 links, 50 more than the later build in this comparison" in e["text"] and e["step"] == "Maintain"
    assert [c["record"] for c in e["cites"]][:2] == ["Change record of the alpha layer", "Build record of the alpha layer"]
    # the combined record, when it exists, is the one that is read
    (synthetic / "outputs/maintain_diff.md").write_text(DIFF.replace("1,150", "1,200").replace("+350", "+400"))
    e = entry(synthetic, "compared with the build before it")
    assert "from 800 to 1,200 (+400)" in e["text"] and "today has" not in e["text"]
    assert ra.record_files(synthetic)["Change record of the alpha layer"] == "outputs/maintain_diff.md"
    (synthetic / "outputs/maintain_diff.md").write_text("# Rebuild diff: alpha\n\n| | before | after | change |\n|---|---|---|---|\n"
                                                        "| nodes | 90 | 90 | +0 |\n| edges | 1,200 | 1,200 | +0 |\n")
    assert "no change, 1,200 links in both" in entry(synthetic, "compared with the build before it")["text"]


def test_assembled_graphs_are_named_in_words_and_the_pruned_one_says_what_was_left_out(synthetic):
    base = entry(synthetic, "Baseline graph assembled")
    assert "alpha layer alone" in base["text"] and "1,200 links" in base["text"] and base["step"] == "Ingest"
    pruned = entry(synthetic, "pruned links left out")
    assert "2 layers into one graph (alpha, MONDO disease hierarchy)" in pruned["text"]
    assert "50 links marked prune were left out of this version (2.27% of 2,200)" in pruned["text"]
    assert "6 entity rows were given one class" in pruned["text"] and pruned["step"] == "Limit" and pruned["date"] == "2030-01-04"
    assert not ra.internal_terms(base["title"] + pruned["title"])


def test_decisions_and_daily_entries_from_the_synthetic_log(synthetic):
    log = ra.agent_log(synthetic)
    sci = [e for e in log["entries"] if e["by"] == "scientist"]
    assert [e["date"] for e in sci] == ["2030-01-03", "2030-01-03"] and log["counts"]["scientist_decisions"] == 2
    rule = [e for e in sci if "rule" in e][0]
    assert rule["title"].startswith("Pass rule fixed before the data were seen") and "spot" not in rule["rule"]
    assert rule["rule"] == "PASS if >= 12 of 20 are judged wrong."         # the clauses with a path and a seed are dropped
    hosted = [e for e in sci if "rule" not in e][0]
    assert hosted["text"] == "The scientist took a decision on where and how the demonstration is hosted."
    day = [e for e in log["entries"] if e["by"] == "agent" and e["date"] == "2030-01-03"][0]
    assert "added 1 entry to the experiment log (results that did not pass their rule: 1)" in day["text"]
    assert len(ra.ledger_rows(synthetic)) == 5                             # the row without a date is not an entry
    assert [e["date"] for e in log["entries"]][-1] is None                 # undated entries come last
    for s in strings(log):
        assert ra.internal_terms(s) == [], s


def test_missing_records_give_fewer_entries_and_say_so_never_an_error(synthetic, tmp_path_factory):
    empty = tmp_path_factory.mktemp("empty")
    log = ra.agent_log(empty)
    assert log["available"] is False and log["entries"] == [] and len(log["missing"]) == 2
    (synthetic / "loop/ledger.tsv").unlink()                               # a copy without the experiment log
    log = ra.agent_log(synthetic)
    assert log["available"] and log["counts"]["scientist_decisions"] == 0 and "experiment log is not on this machine" in log["missing"][0]
    built = [e for e in log["entries"] if "alpha layer built" in e["title"]][0]
    assert built["date"] is None and built["date_words"] is None and "no dated record" in built["date_from"]
    (synthetic / "outputs/pharos/prune_report.md").write_text("# report without totals\n")
    (synthetic / "outputs/alpha/summary.md").write_text("not a summary\n")
    titles = [e["title"] for e in ra.agent_log(synthetic)["entries"]]
    assert not any("keep or prune" in t or "alpha layer built" in t for t in titles)


def test_a_summary_folder_without_a_source_card_gives_no_entry(synthetic):
    """PK9 follow-up 1: an example or trial build left on disk (no sources/*.yaml card) is not a layer of the shipped
    graph; the log must not read it."""
    before = ra.build(synthetic)
    extra = synthetic / "outputs/lab_example/summary.md"
    extra.parent.mkdir(parents=True)
    extra.write_text(SUMMARY.replace("alpha", "lab_example"))
    assert ra.shipped_records(synthetic) == ["outputs/alpha/summary.md", "outputs/g0/summary.md", "outputs/g3/summary.md"]
    after = ra.build(synthetic)
    assert [e["title"] for e in after] == [e["title"] for e in before]
    assert not any("lab_example" in rel for e in after for _, rel, _ in e["_cites"])
    (synthetic / "sources/lab_example.yaml").write_text("name: lab_example\n")      # with a card it is a layer
    assert "outputs/lab_example/summary.md" in ra.shipped_records(synthetic)
    assert len(ra.build(synthetic)) == len(before) + 1


def test_the_repository_log_reads_only_layers_that_have_a_source_card():
    cards = {yaml.safe_load(p.read_text())["name"] for p in (ROOT / "sources").glob("*.yaml")}
    for rel in ra.shipped_records(ROOT):
        folder = rel.split("/")[1]
        assert folder in cards or re.fullmatch(r"g\d+", folder), rel


def test_the_loop_document_says_which_change_record_exists_today():
    """PK9 follow-up 4: the combined change record is named but not committed; the document must say so, and stop
    saying so once the file is committed."""
    doc = (ROOT / "docs/AGENT_LOOP.md").read_text()
    only_pharos = sorted(p.name for p in (ROOT / "outputs/maintain").glob("diff_*.md")) == ["diff_pharos.md"]
    combined = (ROOT / "outputs/maintain_diff.md").is_file()
    says = "Only the Pharos change record exists today" in doc
    assert says == (only_pharos and not combined)
