"""
Change monitor (P6): what changed in the registry, and what changed in the graph because of it.

    python pipelines/monitor.py check            # Mac, after downloading a new AACT export (AACT_FLATFILES in .env):
                                                 #   trials whose AACT tables differ from the frozen records
    python pipelines/monitor.py diff [git-ref]   # after the rebuild: graph now vs the committed graph (default HEAD)
                                                 #   -> outputs/change_report.md + change_report.csv

Monthly routine (owner's Mac): download AACT -> monitor.py check -> aact.py freeze -> seed_kg.py -> monitor.py diff
-> re-run the reports listed under 'questions to re-run' -> commit. Nothing is overwritten by `check`.

Functions
- record_changes(src, snap, ncts) -> [dict(nct, changed_tables)]   frozen AACT record vs the new export
- load_graph(folder=None, ref=None) -> (nodes {id: row}, edges {id: row}) from outputs/ or a git revision
- graph_diff(old, new) -> dict(nodes_added, nodes_removed, nodes_changed, edges_added, edges_removed, by_trial,
                               by_program)   edge ids hash their defining fields, so a changed value = removed + added
- affected_questions(diff, kg_nodes, questions) -> benchmark question ids whose program / trials changed
- to_markdown(diff, questions) -> str
"""
import csv, io, json, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def record_changes(src, snap, ncts):
    """Compares, table by table, the frozen AACT record of each trial with the same tables read from the new export."""
    import aact
    out = []
    for nct in sorted(ncts):
        frozen = snap.call("aact", {"record": nct, "tables": aact.TABLES}, lambda: None).body or {}
        changed = []
        for t in aact.TABLES:
            new = aact._sorted(t, src._query(t, [nct]))
            if json.dumps(new, sort_keys=True, default=str) != json.dumps(aact._sorted(t, frozen.get(t, [])),
                                                                          sort_keys=True, default=str):
                changed.append(t)
        out.append(dict(nct=nct, changed_tables=changed))
    return out


def _read_csv(text):
    csv.field_size_limit(sys.maxsize)
    return {r["id"]: {k: v for k, v in r.items() if v != ""} for r in csv.DictReader(io.StringIO(text))}   # new empty
    # columns (a property added to other rows) are not a change


def load_graph(folder=None, ref=None):
    """From a folder (default outputs/) or from a git revision (e.g. 'HEAD', 'HEAD~3')."""
    if ref:
        get = lambda f: subprocess.run(["git", "show", f"{ref}:outputs/{f}"], cwd=ROOT, capture_output=True,
                                       text=True, check=True).stdout
    else:
        get = lambda f: (Path(folder or OUT) / f).read_text(encoding="utf-8")
    return _read_csv(get("kg_nodes.csv")), _read_csv(get("kg_edges.csv"))


def _trial_of(nid):
    for pre in ("NCT:", "POP:", "ARM:", "ENDPOINT:", "OUTCOME:", "AEREPORT:"):
        if nid.startswith(pre):
            return "NCT:" + nid.split(":")[1][:11]
    return None


def graph_diff(old, new):
    (on, oe), (nn, ne) = old, new
    changed = sorted(k for k in set(on) & set(nn) if on[k] != nn[k])
    d = dict(nodes_added=sorted(set(nn) - set(on)), nodes_removed=sorted(set(on) - set(nn)), nodes_changed=changed,
             edges_added=sorted(set(ne) - set(oe)), edges_removed=sorted(set(oe) - set(ne)))
    by_trial, by_program = {}, {}
    touched = [(n, "node") for n in d["nodes_added"] + d["nodes_removed"] + changed]
    touched += [(ne.get(e, oe.get(e))["subject"], "edge") for e in d["edges_added"] + d["edges_removed"]]
    touched += [(ne.get(e, oe.get(e))["object"], "edge") for e in d["edges_added"] + d["edges_removed"]]
    for nid, kind in touched:
        t = _trial_of(nid)
        if t:
            by_trial[t] = by_trial.get(t, 0) + 1
        if nid[:1] == "P" and nid[1:].isdigit():
            by_program[nid] = by_program.get(nid, 0) + 1
    prog_of = {}                                     # trial or investigational drug -> programs
    for e in list(oe.values()) + list(ne.values()):
        if e["predicate"] in ("tmkg:has_trial", "tmkg:has_intervention"):
            prog_of.setdefault(e["object"], set()).add(e["subject"])
    for t, n in by_trial.items():
        for p in prog_of.get(t, ()):
            by_program[p] = by_program.get(p, 0) + n
    for nid, _ in touched:
        if nid.startswith("CHEMBL:"):
            for p in prog_of.get(nid, ()):
                by_program[p] = by_program.get(p, 0) + 1
    d["by_trial"], d["by_program"] = dict(sorted(by_trial.items())), dict(sorted(by_program.items()))
    return d


def affected_questions(diff, questions):
    """questions: rows with 'id' and 'program' (benchmark_questions.csv)."""
    return sorted(q["id"] for q in questions if q.get("program") in diff["by_program"])


def to_markdown(diff, questions=()):
    n = lambda k: len(diff[k])
    lines = ["# Graph change report", "",
             f"Nodes: +{n('nodes_added')} / -{n('nodes_removed')} / changed {n('nodes_changed')}. "
             f"Edges: +{n('edges_added')} / -{n('edges_removed')} (a changed edge value shows as one removed + one added).",
             "", "## Programs affected", ""]
    lines += [f"- {p}: {c} changes" for p, c in diff["by_program"].items()] or ["- none"]
    lines += ["", "## Trials affected", ""]
    lines += [f"- {t}: {c} changes" for t, c in diff["by_trial"].items()] or ["- none"]
    lines += ["", "## Questions to re-run", ""]
    lines += [f"- {q}" for q in affected_questions(diff, questions)] or ["- none"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diff"
    if cmd == "check":
        import aact
        from snapshot import Snapshot
        snap = Snapshot(mode="offline")
        nodes, _ = load_graph()
        rows = record_changes(aact.Source(snap), snap, [n[4:] for n in nodes if n.startswith("NCT:")])
        for r in rows:
            if r["changed_tables"]:
                print(f"{r['nct']}: {', '.join(r['changed_tables'])}")
        print(f"{sum(bool(r['changed_tables']) for r in rows)} of {len(rows)} trials changed in the new AACT export")
    else:
        ref = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
        d = graph_diff(load_graph(ref=ref), load_graph())
        qs = list(csv.DictReader(open(ROOT / "data" / "curated" / "benchmark_questions.csv", encoding="utf-8")))
        (OUT / "change_report.md").write_text(to_markdown(d, qs))
        with open(OUT / "change_report.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["change", "id"])
            for k in ("nodes_added", "nodes_removed", "nodes_changed", "edges_added", "edges_removed"):
                w.writerows([k, i] for i in d[k])
        print(to_markdown(d, qs))
