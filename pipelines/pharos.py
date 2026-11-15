"""
Pharos connector (pharos.nih.gov, built on the TCRD database) - the backbone layer (L0) of the challenge graph.

Pharos is pulled AS DELIVERED: nothing here edits or filters its rows, so the graph built from it is the plain
Pharos baseline (G0) and every Pharos edge can get a keep/prune verdict later (prune.py).

How: the Pharos GraphQL API has a bulk download, `download(model, fields, top, skip)`, that returns table rows
(one row per target x associated record). Field groups ("download lists", e.g. "Associated Diseases") are read from
the API's own configuration, so the field names are exactly Pharos's. Every call goes through snapshot.Snapshot
(source folder 'pharos' under data/snapshots_pharos/, git-ignored because the full pull is large); the committed
manifest data/reference/pharos_manifest.tsv records version, rows and a checksum per group.

Paging: rows are ordered by target id (pages of 20,000 rows, halved on a server timeout), so a page can end in
the middle of one target's rows. The target that ends a page is fetched once on its own (batch=<UniProt>) and its
paged rows are skipped, so no row is lost or doubled. Rows are streamed to disk (the PPI table has ~11M rows).

PPI and TIN-X (any group in PER_TARGET_GROUPS) is fetched one target at a time (batch=<UniProt>), not by offset: an
offset page deep in an 11M-row table makes Pharos count past every earlier row, so it slows down and times out.
Each target's rows are appended to data/pharos/<group>.progress.jsonl as they arrive, so a stop resumes at the
first target not yet saved.

Outages: snapshot.Snapshot retries a failed HTTP call 4 times within ~15 s and then raises. The Pharos API drops
out for longer than that (a TIN-X pull died after 6,500 targets on 2026-10-29), so query() retries such a failure
again after 30, 60, 90, 120 s (PharosUnavailable when still failing), and a per-target pull then pauses
OUTAGE_PAUSE seconds and carries on from the same target, up to OUTAGE_LIMIT pauses in a row. Only a long outage
stops the pull, and a re-run resumes where it stopped. Offline mode (TMKG_SNAPSHOT_MODE=offline) never retries.

Commands:  python pipelines/pharos.py version
           python pipelines/pharos.py pull [group ...]      (default: all DEFAULT_GROUPS; resumable)
Outputs:   data/pharos/<group>.tsv.gz (git-ignored) + data/reference/pharos_manifest.tsv (committed)
"""
import csv, gzip, hashlib, io, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot import Snapshot, MissingFromSnapshot, ROOT  # noqa: E402

URL = "https://pharos-api.ncats.io/graphql"
SNAP_DIR = ROOT / "data" / "snapshots_pharos"
TABLE_DIR = ROOT / "data" / "pharos"
MANIFEST = ROOT / "data" / "reference" / "pharos_manifest.tsv"
PAGE = 20_000
MIN_PAGE = 1_000
# PPI and TIN-X are very large (~11M rows each) with wide rows; Pharos times out at 20k.
GROUP_PAGE = {"ppi": 5_000, "tinx": 5_000}
# Groups fetched one target at a time (see per_target_rows). The largest single target stays far below this.
PER_TARGET_GROUPS = {"ppi", "tinx"}   # TIN-X slowed past ~175k rows with offset pages (2026-10-29)
PER_TARGET_MAX = 100_000
# A per-target pull pauses this long after query() gave up on a target, then resumes; gives up after OUTAGE_LIMIT
# pauses in a row (a target that succeeds resets the count). 12 x 5 min + the retries inside query() ~ 2 hours.
OUTAGE_PAUSE = 300
OUTAGE_LIMIT = 12

# short name -> (model, Pharos download list). Model "Target" lists are paged per target id.
GROUPS = {
    "targets": ("Target", "Single Value Fields"),        # fields limited by TARGET_FIELDS (see below)
    "diseases": ("Target", "Associated Diseases"),
    "ligands": ("Target", "Drugs and Ligands"),
    "ppi": ("Target", "Interacting Targets"),
    "go": ("Target", "GO Terms"),
    "pathways": ("Target", "Pathways"),
    "phenotypes": ("Target", "Phenotype"),
    "tiga": ("Target", "GWAS Analytics (TIGA)"),
    "tinx": ("Target", "TINX Disease Associations"),
    "dto": ("Target", "DTO Classes"),
    "panther": ("Target", "PANTHER Classes"),
    "nearest_tclin": ("Target", "Nearest Tclin Targets"),
    "keywords": ("Target", "UniProt Keywords"),
    "gtex": ("Target", "GTEx"),
    "expression": ("Target", "Expression"),
    "gene_ids": ("Target", "Gene RIFs"),                 # only UniProt, Symbol, NCBI Gene ID (see GENE_ID_FIELDS)
    "disease_info": ("Disease", "Single Value Fields"),
    "ligand_info": ("Ligand", "Single Value Fields"),
}
# The full "Single Value Fields" list joins several many-valued tables (PDB ids, families, long texts) and becomes
# too slow to download (1,000 rows took 51 s); these single-valued fields download in about a second per 1,000.
TARGET_FIELDS = ["UniProt", "Symbol", "Name", "Target Development Level", "IDG Family", "Novelty",
                 "PubMed Score"]   # "Antibody Count" is 10x slower; "PubTator Score" returns one row per year
# Pharos has no NCBI Gene field in its target lists; the Gene RIF list carries it (one row per RIF, identical
# rows collapse to one per target). The GraphQL `targets` query is capped at 10 targets per page, too slow.
GENE_ID_FIELDS = ["UniProt", "Symbol", "NCBI Gene ID"]
DEFAULT_GROUPS = [g for g in GROUPS if g != "expression"]   # expression (all sources) is very large; ask for it


class PharosError(RuntimeError):
    pass


class PharosUnavailable(PharosError):
    """The server or the network kept failing (HTTP 5xx, timeouts, dropped connections) after every retry."""


def reason(e):
    """Short cause of a failure for a log line. Snapshot's message starts with the whole query text and ends with
    the cause ("... failed after retries: HTTP 502"): keep the cause, not the query."""
    return str(e).split("failed after retries:")[-1].strip()[:200]


def snapshot(mode=None):
    return Snapshot(SNAP_DIR, mode=mode, sleep=0.2)


def query(snap, q, attempts=5, wait=30, log=print, fail_fast=False):
    """Run one GraphQL query. Takes the query text; returns the `data` dict.
    Two kinds of failure are retried after wait, 2*wait, ... seconds (attempts times in all):
    - an error answer (e.g. the server's "connection pool is full"): deleted from the snapshot so it is never reused;
    - a transport failure raised by the snapshot after its own quick retries (HTTP 5xx, timeout, dropped connection).
    Raises PharosError (error answers) or PharosUnavailable (transport) when every attempt failed. A missing
    snapshot in offline mode and a client error (HTTP 4xx) are raised at once: waiting would not change them.
    fail_fast=True raises a transport failure at once too (error answers are still retried): iter_group uses it
    while a page may simply be too large for the server."""
    last = None
    for attempt in range(attempts):
        try:
            r = snap.post("pharos", URL, {"query": q})
        except (MissingFromSnapshot, PermissionError):
            raise
        except RuntimeError as e:
            last, unavailable = reason(e), True
            if fail_fast:
                break
        else:
            if isinstance(r.body, dict) and not r.body.get("errors") and "data" in r.body:
                return r.body["data"]
            snap.path("pharos", r.key).unlink(missing_ok=True)
            last, unavailable = str((r.body or {}).get("errors"))[:300], False
        if attempt < attempts - 1:
            pause = wait * (attempt + 1)
            log(f"  pharos: {last[:120]} - retrying in {pause} s ({attempt + 1} of {attempts - 1} retries)")
            time.sleep(pause)
    err = PharosUnavailable if unavailable else PharosError
    raise err(f"Pharos {'unreachable' if unavailable else 'error'} after {attempts} attempts: {last}")


def db_version(snap):
    """Pharos database version string, e.g. 'pharos319' (Pharos 3.19 / TCRD 6.13)."""
    return query(snap, "{ dbVersion }")["dbVersion"]


def list_fields(snap, model, list_name):
    """Field names of one Pharos download list, in Pharos's order."""
    data = query(snap, '{ configuration { downloadLists(modelName:%s) { listName field { name } } } }'
                 % json.dumps(model))
    for lst in data["configuration"]["downloadLists"]:
        if lst["listName"] == list_name:
            return [f["name"] for f in lst["field"]]
    raise PharosError(f"no download list {list_name!r} for {model}")


def download(snap, model, fields, skip=0, top=PAGE, batch=None, fail_fast=False, log=print):
    """One bulk-download page. model: Target/Disease/Ligand; batch: one UniProt (or name) to restrict to;
    fail_fast: see query() (used while a page may simply be too large).
    Returns the list of row dicts (each has Pharos's internal 'id' plus the requested fields)."""
    args = f"model:{json.dumps(model + 's')}, fields:{json.dumps(fields)}, top:{top}, skip:{skip}, sqlOnly:false"
    if batch:
        args += f", batch:{json.dumps(batch)}"
    d = query(snap, "{ download(%s) { result data errorDetails } }" % args, log=log, fail_fast=fail_fast)["download"]
    if not d["result"]:
        raise PharosError(f"download failed: {d['errorDetails']}")
    return d["data"] or []


def row_key(row):
    return json.dumps(row, sort_keys=True, default=str)


def dedupe(rows):
    """Exact duplicate rows removed (the bulk download repeats rows when a join matches twice); order kept."""
    seen, out = set(), []
    for r in rows:
        k = row_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def group_fields(snap, group):
    """Pharos field names downloaded for one group (see GROUPS / TARGET_FIELDS)."""
    model, list_name = GROUPS[group]
    fields = list_fields(snap, model, list_name)
    keep = {"targets": TARGET_FIELDS, "gene_ids": GENE_ID_FIELDS}.get(group)
    return [f for f in fields if f in keep] if keep else fields


def id_is_target(snap, model, fields):
    """True when the download's `id` column is the Pharos target id (protein.id). Not true for every list:
    TIGA rows carry the TIGA row id, so split-target repair must not be applied there."""
    args = f"model:{json.dumps(model + 's')}, fields:{json.dumps(fields)}, top:1, skip:0, sqlOnly:true"
    sql = query(snap, "{ download(%s) { sql } }" % args)["download"]["sql"] or ""
    return "protein.id as `id`" in sql


def iter_group(snap, group, fields, page=None, log=print):
    """Yield every row of one group, page by page, without holding the table in memory (PPI has ~11 million rows).
    Rows come ordered by Pharos id; each entity's rows are de-duplicated. The target that ends a full page may be
    split across pages: it is skipped in the pages and fetched once on its own (batch=<UniProt>) instead."""
    if page is None:
        page = GROUP_PAGE.get(group, PAGE)
    model = GROUPS[group][0]
    repair = model == "Target" and "UniProt" in fields and id_is_target(snap, model, fields)
    done, cur, buf, skip = set(), None, [], 0
    while True:
        try:   # a page above MIN_PAGE fails fast: a server timeout there means the page is too large
            chunk = download(snap, model, fields, skip=skip, top=page, fail_fast=page > MIN_PAGE, log=log)
        except PharosUnavailable:          # server gateway timeout (HTTP 502/504) on a large page: halve the page
            if page <= MIN_PAGE:
                raise
            page //= 2
            log(f"  {group}: page too large, retrying with {page:,} rows")
            continue
        log(f"  {group}: {skip + len(chunk):,} rows")
        full = len(chunk) == page
        boundary = chunk[-1].get("id") if (full and repair) else None
        for r in chunk:
            i = r.get("id")
            if i in done or i == boundary:
                continue
            if i != cur:
                yield from dedupe(buf)
                done.add(cur)
                cur, buf = i, []
            buf.append(r)
        if not full:
            yield from dedupe(buf)
            return
        if boundary is not None and boundary not in done:
            yield from dedupe(buf)
            done.add(cur)
            cur, buf = None, []
            acc = next(r["UniProt"] for r in chunk if r.get("id") == boundary)
            whole = download(snap, model, fields, top=10 * page, batch=acc, log=log)
            if len(whole) >= 10 * page:
                raise PharosError(f"target {acc} has more than {10 * page} rows in {group}")
            yield from dedupe(whole)
            done.add(boundary)
        skip += page


def pull_group(snap, group, page=PAGE, log=print):
    """All rows of one group as a list (small groups, tests). Returns (fields, rows)."""
    fields = group_fields(snap, group)
    return fields, list(iter_group(snap, group, fields, page=page, log=log))


def write_table(fields, rows, path):
    """Rows (any iterable) -> gzipped TSV (columns: id + fields), written as they come.
    Returns (n_rows, n_distinct_uniprot, sha256 of the uncompressed text)."""
    cols = ["id"] + [f for f in fields if f != "id"]
    sha, n, accs = hashlib.sha256(), 0, set()
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")       # renamed only when complete: no half-written tables
    with gzip.GzipFile(part, "wb", mtime=0) as gz:
        def put(values):
            buf = io.StringIO()
            csv.writer(buf, delimiter="\t", lineterminator="\n").writerow(values)
            data = buf.getvalue().encode()
            sha.update(data)
            gz.write(data)
        put(cols)
        for r in rows:
            put(["" if r.get(c) is None else r.get(c) for c in cols])
            n += 1
            if r.get("UniProt"):
                accs.add(r["UniProt"])
    part.replace(path)
    return n, len(accs), sha.hexdigest()


def write_manifest(entries, path=MANIFEST):
    """entries: dicts (group, model, list, rows, targets, sha256, version, retrieved); merged by group, sorted."""
    old = {}
    if path.exists():
        with open(path) as f:
            old = {r["group"]: r for r in csv.DictReader(f, delimiter="\t")}
    for e in entries:
        old[e["group"]] = {k: str(v) for k, v in e.items()}
    cols = ["group", "model", "list", "rows", "targets", "sha256", "version", "retrieved"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for g in sorted(old):
            w.writerow({c: old[g].get(c, "") for c in cols})


def target_accessions(snap, log=print):
    """UniProt id of every Pharos target, in Pharos's order: the list that per-target pulls walk."""
    fields = group_fields(snap, "targets")
    seen, out = set(), []
    for r in iter_group(snap, "targets", fields, log=log):
        acc = r.get("UniProt")
        if acc and acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


def load_progress(path):
    """Targets already saved by an earlier run: a set of UniProt ids. The file is rewritten without any line a
    crash cut short, so the next append starts on a clean line."""
    if not path.exists():
        return set()
    good, done = [], set()
    for line in path.read_text().splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        good.append(line)
        done.add(obj["uniprot"])
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(line + "\n" for line in good))
    tmp.replace(path)
    return done


def per_target_rows(snap, group, accs, fields, path, log=print):
    """Every row of one large group, fetched one target at a time (batch=<UniProt>). Each target's rows are appended
    to `path` (JSON lines, one target per line) as soon as they arrive, so a stop loses at most one target and a
    re-run skips the saved ones. Yields the rows read back from that file, so the table never sits in memory."""
    model = GROUPS[group][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    done = load_progress(path)
    todo = [a for a in accs if a not in done]
    log(f"  {group}: {len(done):,} targets already saved, {len(todo):,} to fetch")
    outages = 0
    with open(path, "a") as f:
        for i, acc in enumerate(todo, 1):
            while True:
                try:
                    rows = download(snap, model, fields, top=PER_TARGET_MAX, batch=acc, log=log)
                    break
                except PharosUnavailable as e:     # outage longer than query()'s retries: pause, then same target
                    outages += 1
                    if outages > OUTAGE_LIMIT:
                        raise PharosUnavailable(f"{reason(e)} ({OUTAGE_LIMIT} pauses in a row; {i - 1:,} more "
                                                f"targets saved - re-run to resume at {acc})") from e
                    log(f"  {group}: Pharos unreachable at target {acc} ({reason(e)[:80]}); pausing "
                        f"{OUTAGE_PAUSE} s before trying again ({outages} of {OUTAGE_LIMIT})")
                    time.sleep(OUTAGE_PAUSE)
            outages = 0
            if len(rows) >= PER_TARGET_MAX:
                raise PharosError(f"target {acc} has {PER_TARGET_MAX:,}+ rows in {group}")
            f.write(json.dumps({"uniprot": acc, "rows": rows}, default=str) + "\n")
            f.flush()
            if i % 500 == 0:
                log(f"  {group}: {i:,} of {len(todo):,} targets fetched")
    with open(path) as f:
        for line in f:
            yield from dedupe(json.loads(line)["rows"])


def pull(groups=None, snap=None, log=print):
    """Pull groups (default DEFAULT_GROUPS) -> tables + manifest. Returns the manifest entries."""
    snap = snap or snapshot()
    version = db_version(snap)
    retrieved = snap.post("pharos", URL, {"query": "{ dbVersion }"}).fetched_at
    entries = []
    for g in groups or DEFAULT_GROUPS:
        try:
            fields = group_fields(snap, g)
            if g in PER_TARGET_GROUPS:
                rows = per_target_rows(snap, g, target_accessions(snap, log), fields,
                                       TABLE_DIR / f"{g}.progress.jsonl", log)
            else:
                rows = iter_group(snap, g, fields, log=log)
            n, targets, sha = write_table(fields, rows, TABLE_DIR / f"{g}.tsv.gz")
        except (PharosError, RuntimeError) as e:     # one failed group must not stop the others; re-run resumes
            log(f"{g}: FAILED ({reason(e)}) - re-run `python pipelines/pharos.py pull {g}` later")
            continue
        model, list_name = GROUPS[g]
        entries.append(dict(group=g, model=model, list=list_name, rows=n, targets=targets,
                            sha256=sha, version=version, retrieved=retrieved))
        log(f"{g}: {n:,} rows, {targets:,} targets")
        write_manifest(entries[-1:])
    return entries


if __name__ == "__main__":
    cmd, rest = (sys.argv[1] if len(sys.argv) > 1 else "pull"), sys.argv[2:]
    if cmd == "version":
        print(db_version(snapshot()))
    elif cmd == "pull":
        unknown = [g for g in rest if g not in GROUPS]
        if unknown:
            sys.exit(f"unknown group(s) {unknown}; choose from {list(GROUPS)}")
        pull(rest or None)
    else:
        sys.exit(__doc__)
