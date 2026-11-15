"""PK9: the weekly source check (pipelines/maintain.py source_check, .github/workflows/source_check.yml). It compares
each source's current release identifier with what the repository's manifests and source cards say was pulled, writes a
dated report and names the layers that would be rebuilt. No network here: the Snapshot is a stand-in that returns the
small answers the sources give (a version query, a folder listing, a release feed, a release-date file)."""
import csv
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, "pipelines")
import maintain  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LISTING_OT = '<a href="/pub/">Parent</a> <a href="25.12/">25.12/</a> <a href="26.09/">26.09/</a> <a href="latest/">latest/</a>'
LISTING_CHEMBL = '<a href="chembl_9/">chembl_9/</a> <a href="chembl_36/">chembl_36/</a> <a href="chembl_37/">chembl_37/</a>'
ATOM = ('<feed><title>Release notes</title><updated>2026-10-01T00:00:00Z</updated>'
        '<entry><id>tag:github.com,2008:Repository/1/v2026-10-01</id><updated>2026-10-01T09:00:00Z</updated>'
        '<link rel="alternate" href="https://github.com/x/y/releases/tag/v2026-10-01"/><title>v2026-10-01</title></entry>'
        '<entry><updated>2026-09-01T09:00:00Z</updated><link href="https://github.com/x/y/releases/tag/v2026-09-01"/></entry></feed>')


class Body:
    def __init__(self, body):
        self.body = body


class FakeSnap:
    """Stands in for snapshot.Snapshot: answers by address; an address mapped to an Exception raises it."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def _answer(self, url):
        self.calls.append(url)
        a = next(v for k, v in self.answers.items() if k in url)
        if isinstance(a, Exception):
            raise a
        return a

    def post(self, source, url, body, **kw):
        return Body(self._answer(url))

    def get_text(self, source, url, **kw):
        return Body({"text": self._answer(url)})


def answers(**over):
    """The sources' answers when nothing changed since the repository's pull (releases read from the repo itself)."""
    held = {w["source"]: maintain.repo_release(ROOT, w)[0] for w in maintain.WATCHED}
    a = {"pharos-api": {"data": {"dbVersion": held["Pharos"]}},
         "opentargets/platform": f'<a href="{held["Open Targets"]}/">x</a> <a href="16.04/">y</a>',
         "chembl/ChEMBLdb": f'<a href="chembl_{held["ChEMBL"]}/">x</a> <a href="chembl_9/">y</a>',
         "mondo/releases.atom": ATOM, "human-phenotype-ontology/releases.atom": ATOM,
         "geneontology.org": '{"date": "2026-10-01"}'}
    a.update(over)
    return a


def test_the_repository_side_is_read_from_the_manifests_and_cards_not_typed_in():
    by = {w["source"]: w for w in maintain.WATCHED}
    rid, rel = maintain.repo_release(ROOT, by["Pharos"])
    versions = {r["version"] for r in csv.DictReader(open(ROOT / rel), delimiter="\t")}
    assert rel == "data/reference/pharos_manifest.tsv" and set(rid.split(", ")) == versions
    for name, card in (("Open Targets", "sources/opentargets_layer.yaml"), ("ChEMBL", "sources/chembl37.yaml")):
        rid, rel = maintain.repo_release(ROOT, by[name])
        assert rel == card and str(yaml.safe_load((ROOT / card).read_text())["version"]).startswith(rid)
    for name in ("MONDO", "Human Phenotype Ontology", "Gene Ontology"):
        rid, rel = maintain.repo_release(ROOT, by[name])
        assert rel == "data/external/DATA_MANIFEST.tsv" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", rid)
        assert rid in (ROOT / rel).read_text()
    with pytest.raises(ValueError):
        maintain.repo_release(ROOT, dict(repo=("manifest_date", "data/external/DATA_MANIFEST.tsv", "no/such.file")))


def test_each_kind_of_upstream_answer_is_parsed_and_the_highest_release_wins():
    up = lambda kind, text, *more: maintain.upstream_release(
        FakeSnap({"u": text}), dict(upstream=(kind, "https://u/") + more))
    assert up("pharos_graphql", {"data": {"dbVersion": "pharos320"}}) == dict(id="pharos320", date=None, url="https://u/")
    assert up("listing", LISTING_OT, r'href="(\d{2}\.\d{2})/"')["id"] == "26.09"
    assert up("listing", LISTING_CHEMBL, r'href="chembl_(\d+)/"')["id"] == "37"          # 37 > 9: numbers, not text
    assert up("atom", ATOM) == dict(id="v2026-10-01", date="2026-10-01", url="https://u/")
    assert up("date_json", '{"date": "2026-11-02"}')["date"] == "2026-11-02"
    for kind, text, more in (("pharos_graphql", {"errors": ["x"]}, ()), ("listing", "<html></html>", (r'href="(\d+)/"',)),
                             ("atom", "<feed></feed>", ()), ("date_json", "{}", ())):
        with pytest.raises(ValueError):
            up(kind, text, *more)


def test_nothing_changed_gives_same_for_every_watched_source_and_one_small_request_each():
    snap = FakeSnap(answers())
    res = maintain.source_check(ROOT, snap, "2026-11-12")
    watched = [r for r in res["sources"] if r["source"] in {w["source"] for w in maintain.WATCHED}]
    assert res["changed"] == [] and [r["status"] for r in watched] == ["same"] * len(maintain.WATCHED)
    assert len(snap.calls) == len(maintain.WATCHED)                       # one request per source, no bulk download
    assert maintain.source_check_title(res) == "Source check: no new upstream release"
    md = maintain.source_check_markdown(res)
    assert md.startswith("# Source check, 2026-11-12") and "None: no source changed." in md
    assert f"None of the {len(maintain.WATCHED)} sources checked publishes a newer release" in md
    assert "downloads no data, rebuilds nothing and changes no file" in md


def test_a_newer_release_is_reported_with_the_layers_that_would_be_rebuilt():
    snap = FakeSnap(answers(**{"pharos-api": {"data": {"dbVersion": "pharos999"}},
                               "opentargets/platform": LISTING_OT + ' <a href="99.12/">n</a>',
                               "mondo/releases.atom": ATOM.replace("2026-10-01", "2099-01-01")}))
    res = maintain.source_check(ROOT, snap, "2026-11-12")
    by = {r["source"]: r for r in res["sources"]}
    assert res["changed"] == ["Pharos", "Open Targets", "MONDO"]
    assert by["Pharos"]["upstream"] == "pharos999" and by["Open Targets"]["upstream"] == "99.12"
    assert by["ChEMBL"]["status"] == "same" and by["Gene Ontology"]["status"] == "same"
    # layers are computed from the source cards: a card that reads the source's data folder is rebuilt
    assert by["Pharos"]["layers"][0] == "pharos" and "dto" in by["Pharos"]["layers"]
    assert by["Open Targets"]["layers"] == ["opentargets"]
    assert {"mondo_hierarchy", "pharos", "opentargets", "chembl37"} <= set(by["MONDO"]["layers"])    # mapping tables too
    for r in res["sources"]:
        for layer in r["layers"]:
            assert any(yaml.safe_load(c.read_text()).get("name") == layer for c in (ROOT / "sources").glob("*.yaml")), layer
    assert maintain.source_check_title(res) == "New upstream release: Pharos pharos999, Open Targets 99.12, MONDO v2099-01-01"
    md = maintain.source_check_markdown(res)
    assert f"3 of {len(maintain.WATCHED)} sources checked publish a newer release" in md
    assert "| Pharos | " + by["Pharos"]["in_repo"] + " | pharos999 | changed |" in md
    assert "v2099-01-01 (2099-01-01)" in md and "`mondo_hierarchy`" in md and "A scientist decides" in md
    n_layers = len({l for r in res["sources"] if r["status"] == "changed" for l in r["layers"]})
    assert f"{n_layers} layers in all" in md


def test_a_source_that_does_not_answer_or_needs_a_login_is_reported_not_raised():
    snap = FakeSnap(answers(**{"pharos-api": RuntimeError("POST failed after retries"),
                               "chembl/ChEMBLdb": "<html>no folders</html>",
                               "geneontology.org": '{"date": "2020-01-01"}'}))
    res = maintain.source_check(ROOT, snap, "2026-11-12")
    by = {r["source"]: r for r in res["sources"]}
    assert res["changed"] == []
    assert by["Pharos"]["status"] == "not checked" and "RuntimeError" in by["Pharos"]["note"]
    assert by["ChEMBL"]["status"] == "not checked" and "no release folder" in by["ChEMBL"]["note"]
    assert by["Gene Ontology"]["status"] == "same"                       # an older release date is not a change
    aact = by["AACT trial registry export"]
    assert aact["status"] == "not checked" and "needs a login" in aact["note"] and aact["upstream"] is None
    assert "HGNC gene names" in by and by["HGNC gene names"]["layers"]
    md = maintain.source_check_markdown(res)
    assert "## Not checked" in md and "AACT trial registry export: needs a login" in md
    assert re.search(r"; \d+ could not be checked \(listed below\)\.", md)
    older = maintain.source_check(ROOT, FakeSnap(answers(**{"chembl/ChEMBLdb": '<a href="chembl_1/">x</a>'})), "2026-11-12")
    row = [r for r in older["sources"] if r["source"] == "ChEMBL"][0]
    assert row["status"] == "same" and "older release" in row["note"] and "## Notes" in maintain.source_check_markdown(older)


def test_a_release_on_the_day_of_the_download_is_counted_as_the_same(tmp_path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "m.tsv").write_text("source\tfile\turl\tretrieved_utc\tbytes\tsha256\nx\ta/b.json\tu\t2026-10-01T10:00:00Z\t1\tz\n")
    w = dict(source="X", kind="date", repo=("manifest_date", "m.tsv", "a/b.json"), upstream=("atom", "https://u/"), uses=["nope"])
    old = maintain.WATCHED
    maintain.WATCHED = [w]
    try:
        res = maintain.source_check(tmp_path, FakeSnap({"u": ATOM}), "2026-11-12")
    finally:
        maintain.WATCHED = old
    assert res["sources"][0]["status"] == "same" and "day of our download" in res["sources"][0]["note"]
    assert res["sources"][0]["layers"] == []


def test_the_command_writes_a_dated_report_and_a_machine_readable_result(tmp_path, monkeypatch, capsys):
    import snapshot
    made = {}

    def fake(directory=None, mode=None, sleep=None):
        made.update(directory=directory, mode=mode)
        return FakeSnap(answers(**{"chembl/ChEMBLdb": LISTING_CHEMBL + ' <a href="chembl_999/">n</a>'}))

    monkeypatch.setattr(snapshot, "Snapshot", fake)
    assert maintain.main(["source-check", "--out", str(tmp_path), "--date", "2026-11-12"]) == 0
    assert made["mode"] == "refresh" and Path(made["directory"]) == tmp_path / "responses"   # live answers, via Snapshot
    report = (tmp_path / "report_2026-11-12.md").read_text()
    result = json.loads((tmp_path / "source_check.json").read_text())
    assert result["changed"] == ["ChEMBL"] and result["title"] == "New upstream release: ChEMBL 999"
    assert result["report"] == "report_2026-11-12.md" and "`chembl37`" in report and report in capsys.readouterr().out


def test_the_diff_command_also_writes_one_combined_change_record(tmp_path, monkeypatch):
    summary = lambda n: maintain.parse_summary(f"# x graph (source version 1)\n\n5 nodes, {n} edges.\n")
    monkeypatch.setattr(maintain, "layer_summaries", lambda root: [("pharos", "outputs/pharos/summary.md", summary(12)),
                                                                   ("chembl37", "outputs/chembl37/summary.md", summary(7))])
    monkeypatch.setattr(maintain, "committed_text", lambda root, rel: "# x graph (source version 1)\n\n5 nodes, 10 edges.\n")
    assert maintain.main(["diff", "--root", str(tmp_path)]) == 0
    combined = (tmp_path / maintain.COMBINED_DIFF).read_text()
    assert combined.count("# Rebuild diff: ") == 2 and "edges 10 -> 12 (+2 (+20.0%))" in combined
    assert (tmp_path / "outputs/maintain/diff_pharos.md").read_text() in combined
    (tmp_path / maintain.COMBINED_DIFF).unlink()
    assert maintain.main(["diff", "pharos", "--root", str(tmp_path)]) == 0     # one layer: no combined record
    assert not (tmp_path / maintain.COMBINED_DIFF).exists()


def test_the_workflow_runs_weekly_and_by_hand_without_secrets_and_opens_an_issue_only_on_a_change():
    path = ROOT / ".github/workflows/source_check.yml"
    text = path.read_text()
    wf = yaml.safe_load(text)
    on = wf.get("on", wf.get(True))                                       # YAML reads the key `on` as true
    cron = on["schedule"][0]["cron"].split()
    assert len(cron) == 5 and cron[2] == cron[3] == "*" and re.fullmatch(r"[0-6]", cron[4])    # one day a week
    assert "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "read", "issues": "write"}   # reads the repo, may open an issue, no push
    assert "secrets." not in text
    steps = wf["jobs"]["check"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "python pipelines/maintain.py source-check --out source_check" in runs
    assert "GITHUB_STEP_SUMMARY" in runs and any("upload-artifact" in s.get("uses", "") for s in steps)
    decide = [s for s in steps if "maintain.py issue-decision" in s.get("run", "")]
    assert len(decide) == 1 and decide[0]["id"] == "decide" and "gh issue list" in decide[0]["run"]
    assert decide[0]["env"] == {"GH_TOKEN": "${{ github.token }}"}
    issue = [s for s in steps if "gh issue create" in s.get("run", "")]
    assert len(issue) == 1 and issue[0]["if"] == "steps.decide.outputs.open == 'true'"
    assert issue[0]["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert steps.index(decide[0]) < steps.index(issue[0])
    assert not re.search(r"\bif \[|python -c", runs)                      # the decision is in the tested function
    assert not re.search(r"git (push|commit)|pip install .*-r requirements|download_|pharos\.py pull|build_kg", runs)


def test_every_source_of_the_download_manifest_appears_in_the_report(tmp_path):
    """PK9 follow-up 2: reactome, gtex, onsides, optimuskg and biolink were downloaded but neither checked nor
    reported. Every manifest source is now named by a WATCHED or NOT_CHECKED row and shows in the report."""
    codes = maintain.manifest_sources(ROOT)
    assert {"reactome", "gtex", "onsides", "optimuskg", "biolink", "mondo", "hpo", "go", "hgnc"} <= set(codes)
    assert maintain.unlisted_manifest_sources(ROOT) == []
    name = {c: row["source"] for row in maintain.WATCHED + maintain.NOT_CHECKED for c in row.get("manifest", [])}
    res = maintain.source_check(ROOT, FakeSnap(answers()), "2026-11-12")
    by = {r["source"]: r for r in res["sources"]}
    md = maintain.source_check_markdown(res)
    for code in codes:
        assert name[code] in by and f"| {name[code]} |" in md, code
    for row in maintain.NOT_CHECKED:
        assert by[row["source"]]["status"] == "not checked" and row["reason"] and f"- {row['source']}: " in md
    # a source added to the manifest later, with no rule yet, is still reported (not checked, with the reason)
    (tmp_path / "data/external").mkdir(parents=True)
    (tmp_path / maintain.DATA_MANIFEST).write_text("source\tfile\turl\tretrieved_utc\n"
                                                   "mondo\tmondo/mondo.json\tu\t2030-01-01T00:00:00Z\n"
                                                   "brandnew\tb/x.txt\tu\t2030-01-01T00:00:00Z\n")
    assert maintain.manifest_sources(tmp_path) == ["brandnew", "mondo"]
    assert maintain.unlisted_manifest_sources(tmp_path) == ["brandnew"]
    res = maintain.source_check(tmp_path, FakeSnap(answers()), "2026-11-12")
    row = [r for r in res["sources"] if r["source"] == "brandnew"]
    assert len(row) == 1 and row[0]["status"] == "not checked" and "no rule for this source" in row[0]["note"]
    assert "- brandnew: " in maintain.source_check_markdown(res)
    assert maintain.manifest_sources(tmp_path / "nowhere") == []


def test_the_issue_decision_opens_once_per_finding_and_never_when_nothing_changed(tmp_path, capsys):
    """PK9 follow-up 5: the workflow's "open an issue or not" is this function."""
    changed = maintain.source_check(ROOT, FakeSnap(answers(**{"chembl/ChEMBLdb": '<a href="chembl_999/">x</a>'})), "2026-11-12")
    same = maintain.source_check(ROOT, FakeSnap(answers()), "2026-11-12")
    title = maintain.source_check_title(changed)
    assert maintain.issue_decision(same, []) == dict(open=False, title=maintain.source_check_title(same),
                                                     reason="No source changed: no issue opened.")
    assert maintain.issue_decision(same, [maintain.source_check_title(same)])["open"] is False
    d = maintain.issue_decision(changed, ["Something else", title + " (old)"])
    assert d["open"] is True and d["title"] == title == "New upstream release: ChEMBL 999"
    again = maintain.issue_decision(changed, ["Something else", title])
    assert again["open"] is False and title in again["reason"]
    assert maintain.issue_decision(changed, None)["open"] is True
    # the command the workflow runs: reads the saved result and gh's list, writes the step outputs
    (tmp_path / "r.json").write_text(json.dumps(dict(changed, title=title)))
    (tmp_path / "open.json").write_text(json.dumps([{"title": "Something else"}]))
    out = tmp_path / "gh_out"
    assert maintain.main(["issue-decision", str(tmp_path / "r.json"), "--open-issues", str(tmp_path / "open.json"),
                          "--github-output", str(out)]) == 0
    assert out.read_text() == f"open=true\ntitle={title}\n" and "Opening an issue" in capsys.readouterr().out
    (tmp_path / "open.json").write_text(json.dumps([{"title": title}]))
    out.unlink()
    assert maintain.main(["issue-decision", str(tmp_path / "r.json"), "--open-issues", str(tmp_path / "open.json"),
                          "--github-output", str(out)]) == 0
    assert out.read_text().startswith("open=false\n")
