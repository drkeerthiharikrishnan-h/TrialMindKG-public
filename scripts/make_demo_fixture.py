"""
Write the `make demo` fixture: the Pharos rows of ONE small target family, pulled as delivered.

    python scripts/make_demo_fixture.py            (needs pharos-api.ncats.io; about one minute)

Family: the EGFR/ERBB receptor tyrosine kinases (FAMILY below, by gene symbol). Every table the Pharos and DTO source
cards read (sources/pharos.yaml, sources/dto.yaml) is fetched one target at a time with Pharos's bulk download
(`batch=<symbol>`, the same call pipelines/pharos.py makes for its per-target pulls), through snapshot.Snapshot.
Nothing is edited or filtered: the identifiers and values are Pharos's own (CLAUDE.md rule 1).

Writes:  tests/fixtures/demo/pharos/<table>.tsv.gz   (committed; the input of `make demo`, a few MB)
         tests/fixtures/demo/MANIFEST.tsv           (per table: rows, targets, sha256, Pharos version, retrieved)
Snapshots go to data/snapshots_pharos/ (git-ignored), like the full pull.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import pharos  # noqa: E402

FAMILY = ["EGFR", "ERBB2", "ERBB3", "ERBB4"]
# the tables sources/pharos.yaml and sources/dto.yaml read (checked by tests/test_demo.py)
TABLES = ["targets", "gene_ids", "diseases", "ligands", "go", "pathways", "phenotypes", "tiga", "tinx", "ppi", "dto"]
OUT = ROOT / "tests" / "fixtures" / "demo"


def family_rows(snap, group, symbols, log=print):
    """Takes a snapshot, a Pharos group name and gene symbols. Returns (fields, rows): the group's Pharos field names
    and every row of those targets, de-duplicated per target, in the order of `symbols`. Raises PharosError when a
    symbol does not resolve to a Pharos target of that symbol (checked on the `targets` group)."""
    fields = pharos.group_fields(snap, group)
    rows = []
    for sym in symbols:
        got = pharos.download(snap, "Target", fields, top=pharos.PER_TARGET_MAX, batch=sym, log=log)
        if group == "targets" and not any(r.get("Symbol") == sym for r in got):
            raise pharos.PharosError(f"Pharos returned no target with symbol {sym}")
        rows += pharos.dedupe(got)
    return fields, rows


def main(out=OUT, symbols=FAMILY, snap=None, log=print):
    """Pulls every table in TABLES for `symbols` and writes them under out/pharos plus out/MANIFEST.tsv.
    Returns the manifest entries (one dict per table)."""
    snap = snap or pharos.snapshot()
    version = pharos.db_version(snap)
    retrieved = snap.post("pharos", pharos.URL, {"query": "{ dbVersion }"}).fetched_at
    entries = []
    for g in TABLES:
        fields, rows = family_rows(snap, g, symbols, log)
        n, targets, sha = pharos.write_table(fields, rows, Path(out) / "pharos" / f"{g}.tsv.gz")
        model, list_name = pharos.GROUPS[g]
        entries.append(dict(group=g, model=model, list=list_name, rows=n, targets=targets, sha256=sha,
                            version=version, retrieved=retrieved))
        log(f"{g}: {n:,} rows, {targets} targets")
    pharos.write_manifest(entries, path=Path(out) / "MANIFEST.tsv")
    return entries


if __name__ == "__main__":
    main()
