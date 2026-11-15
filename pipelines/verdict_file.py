"""
The gold-set verdict file: one row per SOURCE ROW of a layer (for Pharos: every row of every Pharos table an edge
spec reads), keyed by the source's own identifiers, with the edge it became and that edge's keep / prune verdict.

Why per source row, not per edge: the builder merges source rows that state the same (subject, predicate, object,
source) into one edge and keeps the first row's raw ids in source_record. Two Pharos rows with different native
disease ids (UMLS:C1 and DOID:X) that map to the same MONDO term become ONE edge; a per-edge file would carry only
the first id, and a gold-set row keyed by the second would find no verdict. Rows that did not become an edge are
listed too, with the reason (no identifier, excluded by the card's filter): never dropped, never guessed.

    python pipelines/verdict_file.py sources/pharos.yaml [--nodenorm]
Reads:   the card's tables (data/pharos/), data/kg/<name>/edges.tsv.gz and edge_verdicts.tsv.gz (build_kg, prune_kg)
Writes:  data/kg/<name>/row_verdicts.tsv.gz (git-ignored; tens of millions of rows for Pharos)
         outputs/<name>/verdict_file_report.md (committed: coverage per table and the checks)

Columns: source_table, source_row (0-based data row of the table file), edge_spec, source_subject / source_object
(the first non-empty raw value of the spec's subject / object columns, as the source writes it: for Pharos the
UniProt accession and e.g. the Mondo ID or disease source id), source_object_name, source_name (the row's source,
e.g. DisGeNET), status (in_graph | not_in_graph), status_reason, edge_id, subject, predicate, object (our ids),
verdict, confidence, reasons.
Gold-set id columns (added after `reasons`; empty where the row has no such id, never guessed):
  subject_uniprot   UniProt accession(s) of the subject: the row's own source_subject when it is an accession, else the
                    accessions HGNC lists for the subject's NCBIGene / Ensembl / HGNC id ('|' joined, sorted)
  subject_hgnc      HGNC id(s) of the subject gene or protein (data/reference/hgnc_gene_ids.tsv, pipelines/hgnc_ref.py)
  object_mondo      the object when it is a MONDO term; for a row that did not become an edge, the row's own MONDO id
  subject_nodenorm / object_nodenorm
                    the Node Normalizer (Babel) preferred id of our subject / object id; only with --nodenorm, which asks
                    the RENCI service through pipelines/babel.py (snapshotted); empty where Babel does not know the id
"""
import gzip
import io
import itertools
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kgbuilder import engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["source_table", "source_row", "edge_spec", "source_subject", "source_object", "source_object_name",
           "source_name", "status", "status_reason", "edge_id", "subject", "predicate", "object",
           "verdict", "confidence", "reasons"]
ID_COLUMNS = ["subject_uniprot", "subject_hgnc", "object_mondo", "subject_nodenorm", "object_nodenorm"]
ALL_COLUMNS = COLUMNS + ID_COLUMNS
HGNC_REF = ROOT / "data" / "reference" / "hgnc_gene_ids.tsv"
UNIPROT = re.compile(r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})(-\d+)?$")


def side_columns(side):
    """Takes a subject / object spec. Returns the raw column names it reads, in order of preference."""
    if side.get("column"):
        return [side["column"]]
    return [c if isinstance(c, str) else c["column"] for c in side.get("columns") or []]


def first_raw(df, cols):
    """Takes a table and column names. Returns, per row, the first non-empty cell among those columns (as text)."""
    out = pd.Series(pd.NA, index=df.index, dtype="string")
    for c in cols:
        if c in df:
            v = engine._clean(df[c]).astype("string")
            out = out.fillna(v)
    return out


def spec_rows(df, spec, card, lookups):
    """Takes one table, one edge spec, the card and the lookups. Returns a DataFrame in COLUMNS order (without the
    verdict columns filled) with one row per table row that passed the spec's filter (an exploded JSON list gives
    one row per value): in_graph rows carry the id of the merged edge they belong to (the same id build() gives).
    All chunks of spec_row_chunks joined (tests, small tables)."""
    parts = list(spec_row_chunks(df, spec, card, lookups))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLUMNS[:13])


def spec_row_chunks(df, spec, card, lookups, chunk=1_000_000):
    """As spec_rows, yielded in pieces of at most `chunk` rows (the Pharos PPI and TIN-X tables have ~11M rows;
    one frame of 16 text columns for them would need several GB). The merge that gives the edge ids runs once over
    the whole spec, exactly as in build()."""
    e, d, status, _ = engine.edge_rows(df, spec, card, lookups)
    filt = engine.filter_rows(df, spec)                 # the rows `status` is aligned with (same order)
    st = status.to_numpy()
    ok = st == "edge"
    edge_id = pd.Series(pd.NA, index=range(len(st)), dtype=object)
    spo = {c: pd.Series(pd.NA, index=range(len(st)), dtype=object) for c in ("subject", "predicate", "object")}
    if len(e):
        merged = engine._merge(e, list(spec.get("properties") or {}))
        merged["id"] = engine.edge_ids(merged)
        key = lambda f: f["subject"] + "\x1f" + f["predicate"] + "\x1f" + f["object"] + "\x1f" + f["source"]
        id_of = dict(zip(key(merged), merged["id"]))
        edge_id[ok] = key(e).map(id_of).to_numpy()
        for c in spo:
            spo[c][ok] = e[c].to_numpy()
        del merged, id_of
    subj_raw = first_raw(filt, side_columns(spec["subject"])).to_numpy()
    obj_raw = first_raw(filt, side_columns(spec["object"])).to_numpy()
    name = engine._clean(filt[spec["object"]["name"]]).to_numpy() if spec["object"].get("name") else None
    src = engine.by_column(filt, spec.get("source"), default=card["name"]).fillna(card["name"]).to_numpy()
    rown = filt.index.to_numpy()
    for a in range(0, len(st), chunk):
        b = min(a + chunk, len(st))
        part = st[a:b]
        yield pd.DataFrame({
            "source_table": spec["table"], "source_row": rown[a:b], "edge_spec": spec["name"],
            "source_subject": subj_raw[a:b], "source_object": obj_raw[a:b],
            "source_object_name": name[a:b] if name is not None else pd.NA, "source_name": src[a:b],
            "status": ["in_graph" if x == "edge" else "not_in_graph" for x in part],
            "status_reason": ["" if x == "edge" else x for x in part],
            "edge_id": edge_id.iloc[a:b].to_numpy(), "subject": spo["subject"].iloc[a:b].to_numpy(),
            "predicate": spo["predicate"].iloc[a:b].to_numpy(), "object": spo["object"].iloc[a:b].to_numpy()})


def _filtered(df, spec):
    """The table rows edge_rows works on (engine.filter_rows: filter, require, JSON lists exploded)."""
    return engine.filter_rows(df, spec)


def excluded_rows(df, table, specs, card):
    """Takes one table, the edge specs that read it and the card. Returns COLUMNS rows for the table rows that NO
    spec reads (all removed by the specs' filters): status not_in_graph, reason 'excluded by the card filter'."""
    seen = set()
    for spec in specs:
        seen.update(_filtered(df, spec).index.tolist())
    left = df.loc[~df.index.isin(seen)]
    rows = pd.DataFrame({"source_table": table, "source_row": left.index.to_numpy(), "edge_spec": "",
                         "source_subject": first_raw(left, side_columns(specs[0]["subject"])).to_numpy(),
                         "source_object": pd.NA, "source_object_name": pd.NA, "source_name": pd.NA,
                         "status": "not_in_graph", "status_reason": "excluded by the card filter"})
    for c in ("edge_id", "subject", "predicate", "object"):
        rows[c] = pd.NA
    return rows


def attach_verdicts(rows, verdicts):
    """Takes source rows and {edge id: (verdict, confidence, reasons)}. Returns the rows with the verdict columns;
    an in_graph row whose edge has no verdict gets status_reason 'no verdict for this edge id' (verdicts older than
    the edges: re-run prune_kg.py)."""
    rows = rows.copy()
    v = rows["edge_id"].map(lambda i: verdicts.get(i) if isinstance(i, str) else None)
    rows["verdict"] = [x[0] if x else pd.NA for x in v]
    rows["confidence"] = [x[1] if x else pd.NA for x in v]
    rows["reasons"] = [x[2] if x else pd.NA for x in v]
    missing = (rows["status"] == "in_graph") & rows["verdict"].isna()
    rows.loc[missing, "status_reason"] = "no verdict for this edge id"
    return rows[COLUMNS]


def load_hgnc(path=HGNC_REF):
    """Takes the HGNC reference table path. Returns {'uniprot': {accession: {hgnc ids}}, 'ncbigene': {...},
    'ensembl': {...}, 'hgnc': {hgnc id: {accessions}}} with the sets sorted when used."""
    t = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    maps = {"uniprot": {}, "ncbigene": {}, "ensembl": {}, "hgnc": {}}
    for r in t.itertuples():
        for key, val in (("uniprot", r.uniprot_id), ("ncbigene", r.ncbigene_id), ("ensembl", r.ensembl_id)):
            if val:
                maps[key].setdefault(val, set()).add(r.hgnc_id)
        if r.uniprot_id:
            maps["hgnc"].setdefault(r.hgnc_id, set()).add(r.uniprot_id)
    return maps


def _ids_of(curie, hgnc):
    """Takes one of our subject ids and the HGNC maps. Returns (set of HGNC ids, set of UniProt accessions) it
    stands for; empty sets when the id is not a gene / protein HGNC knows."""
    if not isinstance(curie, str) or ":" not in curie:
        return set(), set()
    pre, val = curie.split(":", 1)
    if pre == "UniProtKB":
        hg = hgnc["uniprot"].get(val.split("-")[0], set())
        return set(hg), {val.split("-")[0]}
    if pre == "NCBIGene":
        hg = hgnc["ncbigene"].get(curie, set())
    elif pre == "ENSEMBL":
        hg = hgnc["ensembl"].get(val, set())
    elif pre == "HGNC":
        hg = {curie}
    else:
        return set(), set()
    return set(hg), set().union(*(hgnc["hgnc"].get(h, set()) for h in hg))


def add_gold_ids(rows, hgnc, nodenorm=None):
    """Takes source rows (COLUMNS, verdicts attached), the HGNC maps (load_hgnc) and optionally {our id: Babel
    preferred id}. Returns the rows with ID_COLUMNS appended: UniProt and HGNC ids of the subject, the MONDO id of the object,
    and the Babel ids when `nodenorm` is given. Nothing is guessed: a column stays empty when the id is unknown."""
    rows = rows.copy()
    uniprot, hgnc_ids = [], []
    cache = {}
    for subj, raw in zip(rows["subject"], rows["source_subject"]):
        key = (subj, raw)
        if key not in cache:
            hg, up = _ids_of(subj, hgnc)
            if isinstance(raw, str) and UNIPROT.match(raw):          # the source's own accession is the gold key
                acc = raw.split("-")[0]
                up, hg = {acc}, hg or set(hgnc["uniprot"].get(acc, set()))
            cache[key] = ("|".join(sorted(up)) or pd.NA, "|".join(sorted(hg)) or pd.NA)
        uniprot.append(cache[key][0])
        hgnc_ids.append(cache[key][1])
    rows["subject_uniprot"], rows["subject_hgnc"] = uniprot, hgnc_ids
    mondo = []
    for obj, raw in zip(rows["object"], rows["source_object"]):
        pick = obj if isinstance(obj, str) and obj.startswith("MONDO:") else (
            raw if isinstance(raw, str) and raw.startswith("MONDO:") else pd.NA)
        mondo.append(pick)
    rows["object_mondo"] = mondo
    for col, src in (("subject_nodenorm", "subject"), ("object_nodenorm", "object")):
        rows[col] = rows[src].map(lambda i: (nodenorm or {}).get(i) if isinstance(i, str) else None) if nodenorm \
            else pd.NA
    return rows


def nodenorm_map(snap, ids, batch=200):
    """Takes a babel Snapshot and our node ids. Returns {id: Babel preferred id} for the ids Node Normalizer knows
    (the service call is frozen in the snapshot, so a rerun gives the same file). Licensed ids are skipped."""
    import babel
    ask = sorted({i for i in ids if isinstance(i, str) and not i.startswith(babel.LICENSED)})
    out = {}
    for c, v in babel.normalize(snap, ask, batch=batch).items():
        if v:
            out[c] = v["id"]
    return out


def new_coverage():
    return {"tables": {}, "edge_ids": set()}


def add_coverage(cov, rows):
    """Adds one batch of rows to the running counts: per table rows / in_graph / with a verdict / not_in_graph by
    reason; and the set of edge ids seen."""
    for table, g in rows.groupby("source_table", sort=False):
        t = cov["tables"].setdefault(table, {"rows": 0, "in_graph": 0, "with_verdict": 0, "not_in_graph": {}})
        t["rows"] += len(g)
        ing = g["status"] == "in_graph"
        t["in_graph"] += int(ing.sum())
        t["with_verdict"] += int(g["verdict"].notna().sum())
        for reason, n in g.loc[~ing, "status_reason"].value_counts().items():
            t["not_in_graph"][reason] = t["not_in_graph"].get(reason, 0) + int(n)
        nov = int((ing & g["verdict"].isna()).sum())
        if nov:
            t["not_in_graph"].setdefault("(in graph) no verdict for this edge id", 0)
            t["not_in_graph"]["(in graph) no verdict for this edge id"] += nov
    cov["edge_ids"].update(rows.loc[rows["status"] == "in_graph", "edge_id"].dropna().tolist())
    return cov


def checks(cov, edge_ids):
    """Takes the coverage and the edge ids of edges.tsv.gz. Returns a list of problems (empty = the file is
    complete): edges no source row maps to, rows pointing at an edge id that is not in the graph, in-graph rows
    without a verdict."""
    edge_ids = set(edge_ids)
    problems = []
    orphan = edge_ids - cov["edge_ids"]
    if orphan:
        problems.append(f"{len(orphan):,} edges in edges.tsv.gz have no source row (e.g. {sorted(orphan)[:3]})")
    stray = cov["edge_ids"] - edge_ids
    if stray:
        problems.append(f"{len(stray):,} edge ids from the source rows are not in edges.tsv.gz "
                        f"(the graph is older than the tables or the card: rebuild with build_kg.py)")
    nov = sum(t["in_graph"] - t["with_verdict"] for t in cov["tables"].values())
    if nov:
        problems.append(f"{nov:,} in-graph rows have no verdict (re-run prune_kg.py on the current edges)")
    return problems


def report_markdown(name, cov, problems, n_edges):
    total = sum(t["rows"] for t in cov["tables"].values())
    lines = [f"# Verdict file for the gold set: {name}", "",
             f"{total:,} source rows; {len(cov['edge_ids']):,} of {n_edges:,} graph edges reached from them. "
             "One row per source row, keyed by the source's own identifiers (data/kg/{0}/row_verdicts.tsv.gz).".format(name),
             "", "| table | rows | in graph | with verdict | not in graph (reason: rows) |", "|---|---|---|---|---|"]
    for table, t in cov["tables"].items():
        why = "; ".join(f"{k}: {v:,}" for k, v in sorted(t["not_in_graph"].items())) or "-"
        lines.append(f"| {table} | {t['rows']:,} | {t['in_graph']:,} | {t['with_verdict']:,} | {why} |")
    lines += ["", "## Checks", ""]
    lines += [f"- PROBLEM: {p}" for p in problems] or ["- every graph edge is reached by at least one source row, "
                                                       "every in-graph row has a verdict"]
    return "\n".join(lines) + "\n"


def write(card, tables_dir=None, kg_dir=None, out_dir=None, chunk=1_000_000, hgnc_path=HGNC_REF, nodenorm=None, snap=None):
    """Builds the verdict file for one card, `chunk` rows at a time, with the gold-set id columns (HGNC joined from
    hgnc_path; `nodenorm` = {id: Babel preferred id} from nodenorm_map, or `snap` = a Snapshot to ask Node Normalizer for the
    ids of edges.tsv.gz, or neither to leave those two columns empty).
    Returns (path of the row file, report text, problems)."""
    tables_dir, cache, lookups = engine.prepare(card, tables_dir)
    kg_dir = Path(kg_dir or ROOT / "data" / "kg" / card["name"])
    out_dir = Path(out_dir or ROOT / "outputs" / card["name"])
    edges = pd.read_csv(kg_dir / "edges.tsv.gz", sep="\t", dtype=str, usecols=["id", "subject", "object"])
    if nodenorm is None and snap is not None:
        nodenorm = nodenorm_map(snap, pd.concat([edges["subject"], edges["object"]]).unique())
    edges = edges[["id"]]
    vt = pd.read_csv(kg_dir / "edge_verdicts.tsv.gz", sep="\t", dtype=str,
                     usecols=["id", "verdict", "confidence", "reasons"], keep_default_na=False)
    verdicts = dict(zip(vt["id"], zip(vt["verdict"], vt["confidence"], vt["reasons"])))
    del vt
    hgnc = load_hgnc(hgnc_path)
    cov = new_coverage()
    by_table = {}
    for spec in card.get("edges", []):
        if engine._table_exists(tables_dir, spec["table"]):
            by_table.setdefault(spec["table"], []).append(spec)
    path = kg_dir / "row_verdicts.tsv.gz"
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        gz.write(("\t".join(ALL_COLUMNS) + "\n").encode())
        for table, specs in by_table.items():
            df = engine.read_table(tables_dir, table, cache)
            batches = itertools.chain((c for s in specs for c in spec_row_chunks(df, s, card, lookups, chunk)),
                                      [excluded_rows(df, table, specs, card)])
            for rows in batches:                        # generated one chunk at a time, written, then dropped
                rows = attach_verdicts(rows, verdicts)
                add_coverage(cov, rows)
                rows = add_gold_ids(rows, hgnc, nodenorm)
                buf = io.StringIO()
                rows.to_csv(buf, sep="\t", index=False, header=False)
                gz.write(buf.getvalue().encode())
            cache.pop(table, None)                      # one table in memory at a time
    problems = checks(cov, edges["id"])
    text = report_markdown(card["name"], cov, problems, len(edges))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verdict_file_report.md").write_text(text)
    return path, text, problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    flags = [a for a in argv if a.startswith("--")]
    argv = [a for a in argv if not a.startswith("--")]
    if len(argv) != 1 or set(flags) - {"--nodenorm"}:
        sys.exit(__doc__)
    snap = None
    if "--nodenorm" in flags:                           # optional: Babel preferred ids (network, snapshotted)
        from snapshot import Snapshot
        snap = Snapshot()
    path, text, problems = write(engine.load_card(argv[0]), snap=snap)
    print(text)
    print(f"written: {path}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
