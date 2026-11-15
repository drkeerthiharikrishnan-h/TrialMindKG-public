"""
Maintain procedure, step 3 "diff" (docs/MAINTAIN.md: re-pull -> rebuild -> diff -> re-prune -> re-index): what changed
in a layer after a rebuild, read from its build summary (outputs/<layer>/summary.md is committed, so the last
committed version is the "before" and the file on disk is the "after").

    python pipelines/maintain.py diff [layer ...]            -> outputs/maintain/diff_<layer>.md (+ printed)
    python pipelines/maintain.py diff --old a.md --new b.md  -> compare two summary files

  summary_diff(old, new)    -> {source_version: (old, new), totals, classes, predicates}: per row (old, new, change);
                               rows that appear or vanish have None on one side
  diff_markdown(layer, d)   -> Markdown report; sentences computed from the numbers (CLAUDE.md rule 7)
  committed_text(root, rel) -> the file's text at git HEAD, or None (new file / not a git checkout)

Source check (runs by itself every week: .github/workflows/source_check.yml; nothing is downloaded in bulk, nothing is
rebuilt, no secret is used). It asks each source for its current release identifier (one small request per source,
through snapshot.Snapshot) and compares it with what the repository's manifests and source cards say was pulled:

    python pipelines/maintain.py source-check [--out outputs/source_check]
        -> <out>/report_<date>.md (what changed upstream, which layers would be rebuilt) + <out>/source_check.json

  repo_release(root, w)          -> (identifier the repo holds for watched source w, the file it was read from)
  upstream_release(snap, w)      -> the identifier the source publishes now (raises when it cannot be read)
  layers_using(root, paths)      -> names of the source cards that read from any of these data folders
  source_check(root, snap, today)-> {date, changed, sources: [{source, status, in_repo, upstream, layers, ...}]}
  source_check_markdown(result)  -> the dated report; sentences computed from the result (CLAUDE.md rule 7)
  manifest_sources(root)         -> the source codes of the download manifest
  unlisted_manifest_sources(root)-> manifest codes no WATCHED / NOT_CHECKED row names (each still gets a report row)
  issue_decision(result, titles) -> {open, title, reason}: whether the weekly run opens an issue
    python pipelines/maintain.py issue-decision source_check.json --open-issues open_issues.json
"""
import csv
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_card import parse_summary, layer_summaries  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COMBINED_DIFF = "outputs/maintain_diff.md"


def _rows(old, new):
    out = []
    for k in sorted(set(old) | set(new), key=str):
        a, b = old.get(k), new.get(k)
        if a != b:
            out.append(dict(key=k, old=a, new=b, change=(b or 0) - (a or 0)))
    return out


def summary_diff(old, new):
    """Takes two parsed summaries (schema_card.parse_summary). Returns {source_version: (old, new), totals:
    {nodes: (old, new), edges: (old, new)}, classes: [...], predicates: [...]}; the lists hold only rows whose
    count changed, each {key, old, new, change}; predicates are keyed 'predicate | primary source'."""
    edges = lambda s: {f"{e['predicate']} | {e['source']}": e["edges"] for e in s["edges"]}
    return dict(source_version=(old["source_version"], new["source_version"]),
                totals=dict(nodes=(old["n_nodes"], new["n_nodes"]), edges=(old["n_edges"], new["n_edges"])),
                classes=_rows(old["nodes_by_class"], new["nodes_by_class"]),
                predicates=_rows(edges(old), edges(new)))


def _fmt(v):
    return "-" if v is None else f"{v:,}"


def _change(a, b):
    if a is None or b is None:
        return "new" if a is None else "gone"
    d = b - a
    pct = f" ({100 * d / a:+.1f}%)" if a else ""
    return f"{d:+,}{pct}"


def diff_markdown(layer, d):
    """Takes the layer name and a summary_diff result. Returns the Markdown report: a headline sentence, totals,
    and tables of the changed classes and predicate / source rows."""
    (vo, vn), t = d["source_version"], d["totals"]
    n_rows = len(d["classes"]) + len(d["predicates"])
    if n_rows == 0 and vo == vn and t["nodes"][0] == t["nodes"][1] and t["edges"][0] == t["edges"][1]:
        head = f"No change in {layer}: same source version, same counts."
    else:
        head = (f"{layer}: {len(d['classes'])} node classes and {len(d['predicates'])} predicate/source rows changed; "
                f"edges {_fmt(t['edges'][0])} -> {_fmt(t['edges'][1])} ({_change(*t['edges'])}).")
        if vo != vn:
            head += f" Source version {vo or '-'} -> {vn or '-'}."
    lines = [f"# Rebuild diff: {layer}", "", head, "", "| | before | after | change |", "|---|---|---|---|",
             f"| nodes | {_fmt(t['nodes'][0])} | {_fmt(t['nodes'][1])} | {_change(*t['nodes'])} |",
             f"| edges | {_fmt(t['edges'][0])} | {_fmt(t['edges'][1])} | {_change(*t['edges'])} |"]
    for title, rows in (("Node classes", d["classes"]), ("Edges by predicate | primary source", d["predicates"])):
        if rows:
            lines += ["", f"## {title}", "", "| | before | after | change |", "|---|---|---|---|"]
            lines += [f"| {r['key']} | {_fmt(r['old'])} | {_fmt(r['new'])} | {_change(r['old'], r['new'])} |" for r in rows]
    return "\n".join(lines) + "\n"


def committed_text(root, rel):
    """Takes the repo root and a path relative to it. Returns the file's text at git HEAD, or None."""
    try:
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# ---------------------------------------------------------------------------------------------------------------
# Source check: does a source publish a newer release than the one in the repo? (weekly, by itself; see the docstring)
# ---------------------------------------------------------------------------------------------------------------
PHAROS_MANIFEST = "data/reference/pharos_manifest.tsv"
DATA_MANIFEST = "data/external/DATA_MANIFEST.tsv"
EBI = "https://ftp.ebi.ac.uk/pub/databases"

# One row per watched source. `repo` says where the repository records what was pulled:
#   ("manifest_column", file, column)   the distinct values of a column of a committed manifest
#   ("card_version", card, regex)       the release at the start of a source card's `version:` line
#   ("manifest_date", file, data file)  the last download date of one file in the download manifest
# `upstream` says how the source's current release is read (one small request each, no bulk download):
#   ("pharos_graphql", url)             the Pharos API's own database version
#   ("listing", url, regex)             a release folder listing; the highest release wins
#   ("atom", url)                       a GitHub releases feed: newest release tag and its date
#   ("date_json", url)                  a small JSON file that holds the release date
# `kind` "id" compares identifiers; "date" asks whether the source released after our download.
# `uses`: the data folders a source card reads when it depends on this source (see layers_using).
WATCHED = [
    dict(source="Pharos", kind="id", repo=("manifest_column", PHAROS_MANIFEST, "version"),
         upstream=("pharos_graphql", "https://pharos-api.ncats.io/graphql"), uses=["data/pharos"]),
    dict(source="Open Targets", kind="id", repo=("card_version", "sources/opentargets_layer.yaml", r"\d+\.\d+"),
         upstream=("listing", f"{EBI}/opentargets/platform/", r'href="(\d{2}\.\d{2})/"'), uses=["data/opentargets"]),
    dict(source="ChEMBL", kind="id", repo=("card_version", "sources/chembl37.yaml", r"\d+"),
         upstream=("listing", f"{EBI}/chembl/ChEMBLdb/releases/", r'href="chembl_(\d+)/"'), uses=["data/chembl37"]),
    dict(source="MONDO", kind="date", repo=("manifest_date", DATA_MANIFEST, "mondo/mondo.json"),
         upstream=("atom", "https://github.com/monarch-initiative/mondo/releases.atom"),
         uses=["data/ontologies/mondo", "data/reference/mondo_"], manifest=["mondo"]),
    dict(source="Human Phenotype Ontology", kind="date", repo=("manifest_date", DATA_MANIFEST, "hpo/hp.json"),
         upstream=("atom", "https://github.com/obophenotype/human-phenotype-ontology/releases.atom"),
         uses=["data/ontologies/hpo"], manifest=["hpo"]),
    dict(source="Gene Ontology", kind="date", repo=("manifest_date", DATA_MANIFEST, "go/go-basic.json"),
         upstream=("date_json", "https://current.geneontology.org/metadata/release-date.json"),
         uses=["data/ontologies/go"], manifest=["go"]),
]
# Sources the check leaves out, with the reason printed in every report (the packet's rule: skip and say so).
# `manifest`: the source codes of the download manifest (first column of DATA_MANIFEST) that a row stands for. Every
# code in the manifest must be named by a WATCHED or NOT_CHECKED row; one that is not still gets a report row
# (unlisted_manifest_sources), so no downloaded source is silently left out.
NOT_CHECKED = [
    dict(source="AACT trial registry export", reason="needs a login (a database account); the monthly routine in "
         "pipelines/monitor.py checks it on the owner's computer", uses=[]),
    dict(source="HGNC gene names", reason="publishes no release identifier that can be read without downloading the "
         "whole file", uses=["data/reference/hgnc_gene_ids.tsv"], manifest=["hgnc"]),
    dict(source="Reactome", reason="the download address always gives the current files and the download record "
         "holds a date only; the check does not read Reactome's release number", uses=["data/external/reactome"],
         manifest=["reactome"]),
    dict(source="GTEx", reason="the download address is pinned to version 8 (two sample annotation files); a later "
         "version would be a deliberate change of the download script", uses=["data/external/gtex"],
         manifest=["gtex"]),
    dict(source="OnSIDES", reason="the download address is pinned to release v3.1.0; the check does not look for a "
         "later release", uses=["data/external/onsides"], manifest=["onsides"]),
    dict(source="OptimusKG", reason="one deposited dataset fetched by its DOI; the check does not read the "
         "deposit's version", uses=["data/external/optimuskg"], manifest=["optimuskg"]),
    dict(source="BioLink model", reason="the download address always gives the latest model file; the check does "
         "not read its version", uses=["data/external/biolink"], manifest=["biolink"]),
]


def manifest_sources(root):
    """Takes the repo root. Returns the distinct source codes (first column) of the download manifest, sorted;
    [] when the manifest is not on this machine."""
    path = Path(root) / DATA_MANIFEST
    if not path.is_file():
        return []
    with open(path, newline="") as f:
        return sorted({r["source"] for r in csv.DictReader(f, delimiter="\t") if r.get("source")})


def unlisted_manifest_sources(root):
    """Takes the repo root. Returns the manifest source codes that no WATCHED or NOT_CHECKED row names."""
    named = {c for row in WATCHED + NOT_CHECKED for c in row.get("manifest", [])}
    return [c for c in manifest_sources(root) if c not in named]


def issue_decision(result, open_titles):
    """Takes a source_check result (or the saved source_check.json) and the titles of the issues that are open.
    Returns {open: bool, title, reason}: an issue is opened only when a source has a newer release and no open
    issue already carries the same title (the title names the sources and their new releases, so one finding is
    reported once, not weekly)."""
    title = result.get("title") or source_check_title(result)
    if not result.get("changed"):
        return dict(open=False, title=title, reason="No source changed: no issue opened.")
    if title in set(open_titles or []):
        return dict(open=False, title=title, reason=f"An open issue already reports this: {title}")
    return dict(open=True, title=title, reason=f"Opening an issue: {title}")


def _release_key(v):
    """Takes a release string ('26.09', '37'). Returns a tuple of its numbers, so that 26.12 > 26.09 and 100 > 37."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))


def repo_release(root, w):
    """Takes the repo root and one WATCHED row. Returns (identifier, file): what the repository says was pulled (a
    release such as 'pharos319' or '26.09', or a download date 'YYYY-MM-DD') and the committed file that says it.
    Raises ValueError when the file does not hold it."""
    how, rel, arg = w["repo"]
    path = Path(root) / rel
    if how == "card_version":
        m = re.search(r'^version:\s*"?\s*(' + arg + ")", path.read_text(), re.M)
        if not m:
            raise ValueError(f"no release at the start of the version line of {rel}")
        return m.group(1), rel
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if how == "manifest_column":
        vals = sorted({r[arg] for r in rows if r.get(arg)})
        if not vals:
            raise ValueError(f"no {arg} in {rel}")
        return ", ".join(vals), rel
    dates = sorted(r["retrieved_utc"][:10] for r in rows if r.get("file") == arg and r.get("retrieved_utc"))
    if not dates:
        raise ValueError(f"{arg} is not listed in {rel}")
    return dates[-1], rel


def upstream_release(snap, w):
    """Takes a snapshot.Snapshot (every HTTP call goes through it, CLAUDE.md rule 9) and one WATCHED row. Returns
    {id: the release the source publishes now, date: its date 'YYYY-MM-DD' or None, url}. Raises when the source
    does not answer or the answer holds no release."""
    how, url = w["upstream"][0], w["upstream"][1]
    if how == "pharos_graphql":
        body = snap.post("source_check", url, {"query": "{ dbVersion }"}).body or {}
        rid = (body.get("data") or {}).get("dbVersion")
        if not rid:
            raise ValueError(f"no dbVersion in the answer: {str(body)[:120]}")
        return dict(id=str(rid), date=None, url=url)
    body = snap.get_text("source_check", url).body
    text = (body or {}).get("text") or ""
    if how == "listing":
        found = re.findall(w["upstream"][2], text)
        if not found:
            raise ValueError("no release folder in the listing")
        return dict(id=max(found, key=_release_key), date=None, url=url)
    if how == "atom":
        entry = re.search(r"<entry>(.*?)</entry>", text, re.S)
        tag = entry and re.search(r"/releases/tag/([^\"<\s]+)", entry.group(1))
        when = entry and re.search(r"<updated>(\d{4}-\d{2}-\d{2})", entry.group(1))
        if not (tag and when):
            raise ValueError("no release entry in the feed")
        return dict(id=tag.group(1), date=when.group(1), url=url)
    when = re.search(r"\d{4}-\d{2}-\d{2}", text)                      # date_json
    if not when:
        raise ValueError("no release date in the answer")
    return dict(id=when.group(0), date=when.group(0), url=url)


def layers_using(root, paths):
    """Takes the repo root and a list of data paths. Returns the names of the source cards (sources/*.yaml) whose
    text names any of them, i.e. the layers that would be rebuilt when that data changes; Pharos first."""
    import yaml
    out = []
    for card in sorted((Path(root) / "sources").glob("*.yaml")):
        text = card.read_text()
        if any(p in text for p in paths):
            out.append((yaml.safe_load(text) or {}).get("name") or card.stem)
    return sorted(out, key=lambda n: (n != "pharos", n))


def source_check(root, snap, today):
    """Takes the repo root, a Snapshot and today's date 'YYYY-MM-DD'. Returns {date, changed: [source names],
    sources: [{source, status, in_repo, in_repo_from, upstream, upstream_date, upstream_from, layers, note}]} with
    status 'changed' (a newer release is published), 'same', or 'not checked' (note says why: the source did not
    answer, or it is in NOT_CHECKED). Never raises for one source: a failed check is a row, not a crash."""
    rows = []
    for w in WATCHED:
        row = dict(source=w["source"], status="not checked", in_repo=None, in_repo_from=w["repo"][1], upstream=None,
                   upstream_date=None, upstream_from=w["upstream"][1], layers=layers_using(root, w["uses"]), note="")
        try:
            row["in_repo"], row["in_repo_from"] = repo_release(root, w)
        except (OSError, ValueError, KeyError) as e:
            row["note"] = f"the repository's record could not be read ({e})"
            rows.append(row)
            continue
        try:
            up = upstream_release(snap, w)
        except Exception as e:          # network errors, HTTP errors, an answer in a new format: all reported
            row["note"] = f"the source could not be read ({type(e).__name__}: {str(e)[:160]})"
            rows.append(row)
            continue
        row["upstream"], row["upstream_date"] = up["id"], up["date"]
        if w["kind"] == "date":         # ours is a download date: changed when the source released after it
            row["status"] = "changed" if up["date"] > row["in_repo"] else "same"
            if up["date"] == row["in_repo"]:
                row["note"] = "released on the day of our download; counted as the same release"
        elif w["repo"][0] == "manifest_column":
            row["status"] = "same" if up["id"] in row["in_repo"].split(", ") else "changed"
        else:
            row["status"] = "changed" if _release_key(up["id"]) > _release_key(row["in_repo"]) else "same"
            if _release_key(up["id"]) < _release_key(row["in_repo"]):
                row["note"] = "the source lists an older release than the repository holds"
        rows.append(row)
    for n in NOT_CHECKED:
        rows.append(dict(source=n["source"], status="not checked", in_repo=None, in_repo_from=None, upstream=None,
                         upstream_date=None, upstream_from=None, layers=layers_using(root, n["uses"]) if n["uses"]
                         else [], note=n["reason"]))
    for code in unlisted_manifest_sources(root):
        rows.append(dict(source=code, status="not checked", in_repo=None, in_repo_from=DATA_MANIFEST, upstream=None,
                         upstream_date=None, upstream_from=None, layers=[],
                         note="listed in the download record, but the check has no rule for this source yet"))
    return dict(date=today, changed=[r["source"] for r in rows if r["status"] == "changed"], sources=rows)


def source_check_title(result):
    """Takes a source_check result. Returns the one-line title used for the report and the issue: the changed
    sources with their new release, or that nothing changed."""
    new = [f"{r['source']} {r['upstream']}" for r in result["sources"] if r["status"] == "changed"]
    return ("New upstream release: " + ", ".join(new)) if new else "Source check: no new upstream release"


def source_check_markdown(result):
    """Takes a source_check result. Returns the dated Markdown report: a headline computed from the counts, one
    table row per source (what the repo holds, what the source publishes, status), the layers that would be
    rebuilt, the sources that could not be checked and why, and the next steps (docs/MAINTAIN.md)."""
    rows = result["sources"]
    changed = [r for r in rows if r["status"] == "changed"]
    same = [r for r in rows if r["status"] == "same"]
    unchecked = [r for r in rows if r["status"] == "not checked"]
    head = (f"{len(changed)} of {len(changed) + len(same)} sources checked publish a newer release than the "
            f"repository holds" if changed else
            f"None of the {len(same)} sources checked publishes a newer release than the repository holds")
    head += f"; {len(unchecked)} could not be checked (listed below)." if unchecked else "."
    up = lambda r: "-" if r["upstream"] is None else (
        r["upstream"] if r["upstream_date"] in (None, r["upstream"]) else f"{r['upstream']} ({r['upstream_date']})")
    lines = [f"# Source check, {result['date']}", "", source_check_title(result) + ".", "", head,
             "This check reads release identifiers only (one small request per source). It downloads no data, "
             "rebuilds nothing and changes no file in the repository.", "",
             "| source | in the repository | published by the source | status |", "|---|---|---|---|"]
    lines += [f"| {r['source']} | {r['in_repo'] or '-'} | {up(r)} | {r['status']} |" for r in rows]
    lines += ["", "## Layers that would be rebuilt", ""]
    if changed:
        layers = sorted({l for r in changed for l in r["layers"]}, key=lambda n: (n != "pharos", n))
        lines += [f"- {r['source']} ({r['in_repo']} -> {up(r)}): " + (", ".join(f"`{l}`" for l in r["layers"])
                  or "no source card reads this source") for r in changed]
        lines += ["", f"{len(layers)} layer{'s' if len(layers) != 1 else ''} in all: " +
                  ", ".join(f"`{l}`" for l in layers) + ". After a layer is rebuilt its verdicts, the assembled "
                  "graphs and the app's card index are redone too (docs/MAINTAIN.md, steps 2 to 4)."]
    else:
        lines += ["None: no source changed."]
    if unchecked:
        lines += ["", "## Not checked", ""]
        lines += [f"- {r['source']}: {r['note'].rstrip('.')}." + (
                  f" Layers that read it: {', '.join(f'`{l}`' for l in r['layers'])}." if r["layers"] else "")
                  for r in unchecked]
    notes = [r for r in rows if r["note"] and r["status"] != "not checked"]
    if notes:
        lines += ["", "## Notes", ""] + [f"- {r['source']}: {r['note']}" for r in notes]
    lines += ["", "## Where each identifier was read", "", "| source | repository record | source address |",
              "|---|---|---|"]
    lines += [f"| {r['source']} | {('`' + r['in_repo_from'] + '`') if r['in_repo_from'] else '-'} | "
              f"{r['upstream_from'] or '-'} |" for r in rows]
    lines += ["", "## What happens next (a scientist decides)", ""]
    if changed:
        lines += ["Nothing was pulled or rebuilt. A scientist decides whether to take the new release, then runs "
                  "docs/MAINTAIN.md: re-pull, rebuild, `python3 pipelines/maintain.py diff`, read the diff, re-prune, "
                  "re-index, commit the record."]
    else:
        lines += ["Nothing to do. The next check runs in a week."]
    return "\n".join(lines) + "\n"


def _source_check_cli(root, out=None, date=None):
    """Runs the check against the live sources and writes report_<date>.md and source_check.json into `out`
    (default <root>/outputs/source_check, git-ignored responses under <out>/responses). Returns 0."""
    from snapshot import Snapshot
    today = date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    out = Path(out) if out else root / "outputs" / "source_check"
    out.mkdir(parents=True, exist_ok=True)
    result = source_check(root, Snapshot(directory=out / "responses", mode="refresh", sleep=0), today)
    md = source_check_markdown(result)
    (out / f"report_{today}.md").write_text(md)
    (out / "source_check.json").write_text(json.dumps(dict(result, title=source_check_title(result),
                                                            report=f"report_{today}.md"), indent=1) + "\n")
    print(md)
    return 0


def _issue_decision_cli(result_file, open_file, github_output=None):
    """Takes the saved source_check.json, a JSON file with the open issues ([{"title": ...}], as `gh issue list
    --json title` writes it) and optionally the file GitHub reads step outputs from. Prints the reason; writes
    `open=true|false` and `title=...` to the output file. Returns 0."""
    result = json.loads(Path(result_file).read_text())
    titles = [i.get("title", "") for i in json.loads(Path(open_file).read_text() or "[]")]
    d = issue_decision(result, titles)
    print(d["reason"])
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"open={'true' if d['open'] else 'false'}\ntitle={d['title']}\n")
    return 0


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="maintain procedure: diff a rebuilt layer against its last commit")
    sp = p.add_subparsers(dest="cmd", required=True)
    d = sp.add_parser("diff")
    d.add_argument("layers", nargs="*", help="layer names (default: every layer with a summary)")
    d.add_argument("--old", help="summary file to compare from (instead of git HEAD)")
    d.add_argument("--new", help="summary file to compare to (with --old)")
    d.add_argument("--root", default=str(ROOT))
    c = sp.add_parser("source-check", help="compare each source's current release with the repo's manifests")
    c.add_argument("--out", default=None, help="folder for the report (default: <root>/outputs/source_check)")
    c.add_argument("--date", default=None, help="report date, YYYY-MM-DD (default: today, UTC)")
    c.add_argument("--root", default=str(ROOT))
    i = sp.add_parser("issue-decision", help="say whether the weekly check should open an issue")
    i.add_argument("result", help="source_check.json written by source-check")
    i.add_argument("--open-issues", required=True, help="JSON list of the open issues' titles (gh issue list --json title)")
    i.add_argument("--github-output", default=None, help="file to append open=/title= to (GitHub step outputs)")
    a = p.parse_args(argv)
    if a.cmd == "issue-decision":
        return _issue_decision_cli(a.result, a.open_issues, a.github_output)
    root = Path(a.root)
    if a.cmd == "source-check":
        return _source_check_cli(root, a.out, a.date)
    if a.old or a.new:
        if not (a.old and a.new):
            p.error("--old and --new go together")
        print(diff_markdown(Path(a.new).parent.name, summary_diff(parse_summary(Path(a.old).read_text()),
                                                                  parse_summary(Path(a.new).read_text()))))
        return 0
    out = root / "outputs" / "maintain"
    out.mkdir(parents=True, exist_ok=True)
    reports = []
    for layer, rel, new in layer_summaries(root):
        if a.layers and layer not in a.layers:
            continue
        old_text = committed_text(root, rel)
        if old_text is None:
            print(f"{layer}: {rel} is not committed yet, nothing to compare with")
            continue
        md = diff_markdown(layer, summary_diff(parse_summary(old_text), new))
        (out / f"diff_{layer}.md").write_text(md)
        reports.append(md)
        print(md)
    if reports and not a.layers:          # every layer in one file: the "what changed" record the app's agent log reads
        (root / COMBINED_DIFF).write_text("\n".join(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
