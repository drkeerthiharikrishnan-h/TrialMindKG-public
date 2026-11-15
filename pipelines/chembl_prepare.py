"""
Pull the three ChEMBL 37 tables the L1 card (sources/chembl37.yaml) reads out of the ChEMBL SQLite database.

Usage:   python pipelines/chembl_prepare.py [path/to/chembl_37.db] [--chunks N]      (Mac; prints progress per step)
         python pipelines/chembl_prepare.py --report-only    rewrite outputs/chembl37/prepare.md from files already made
Reads:   data/external/chembl/**/chembl_37.db (scripts/download_external_data.sh without SKIP_CHEMBL)
Writes:  data/chembl37/activities.tsv.gz   one row per (compound, human single-protein target) with a pChEMBL value:
             molecule_chembl_id, uniprot, pchembl_max, n_activities, first_year, standard_types
         data/chembl37/mechanisms.tsv.gz   drug mechanism of action rows with a UniProt target:
             molecule_chembl_id, uniprot, mechanism_of_action, action_type, target_name, max_phase
         data/chembl37/indications.tsv.gz  drug indications: molecule_chembl_id, efo_id, efo_term, mesh_id,
             mesh_heading, max_phase_for_ind
         outputs/chembl37/prepare.md       row counts and the release named in the database (committed)
Only human SINGLE PROTEIN targets are kept, so every target is one UniProt accession (Pharos's Protein id).

How the activities step stays fast on a laptop (the table has ~25M rows, the database file ~25 GB):
  1. the human single-protein assays are listed once into a small indexed temp table (seconds), instead of
     re-joining five tables for every activity row;
  2. the aggregation runs in chunks of molecules (molregno ranges) using the activities(molregno) index, so each
     chunk's GROUP BY fits in memory and the terminal shows progress with a finish estimate;
  3. the SQLite page cache is raised from the default 2 MB to 2 GB (CACHE_MB) so index lookups hit memory, not disk.
  Without a molregno index on activities the script falls back to one pass (no progress inside it).
"""
import gzip
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "chembl37"
CACHE_MB = 2000
CHUNKS = 40

HUMAN_ASSAYS = """
CREATE TEMP TABLE human_assay AS
SELECT a.assay_id AS assay_id, cs.accession AS uniprot
FROM target_dictionary td
JOIN target_components tc ON tc.tid = td.tid
JOIN component_sequences cs ON cs.component_id = tc.component_id
JOIN assays a ON a.tid = td.tid
WHERE td.target_type = 'SINGLE PROTEIN' AND td.organism = 'Homo sapiens' AND cs.accession IS NOT NULL
"""

ACTIVITY_CHUNK = """
SELECT act.molregno AS molregno, ha.uniprot AS uniprot,
       MAX(act.pchembl_value) AS pchembl_max, COUNT(*) AS n_activities, MIN(d.year) AS first_year,
       GROUP_CONCAT(DISTINCT act.standard_type) AS standard_types
FROM activities act
JOIN human_assay ha ON ha.assay_id = act.assay_id
LEFT JOIN docs d ON d.doc_id = act.doc_id
WHERE act.pchembl_value IS NOT NULL AND act.molregno BETWEEN ? AND ?
GROUP BY act.molregno, ha.uniprot
"""

MECHANISMS = """
SELECT md.chembl_id AS molecule_chembl_id, cs.accession AS uniprot, dm.mechanism_of_action, dm.action_type,
       td.pref_name AS target_name, md.max_phase
FROM drug_mechanism dm
JOIN molecule_dictionary md ON md.molregno = dm.molregno
JOIN target_dictionary td ON td.tid = dm.tid
JOIN target_components tc ON tc.tid = td.tid
JOIN component_sequences cs ON cs.component_id = tc.component_id
WHERE cs.accession IS NOT NULL AND td.organism = 'Homo sapiens'
ORDER BY md.chembl_id, cs.accession, dm.mechanism_of_action
"""

INDICATIONS = """
SELECT md.chembl_id AS molecule_chembl_id, di.efo_id, di.efo_term, di.mesh_id, di.mesh_heading, di.max_phase_for_ind
FROM drug_indication di
JOIN molecule_dictionary md ON md.molregno = di.molregno
ORDER BY md.chembl_id, di.efo_id, di.mesh_id
"""

ACTIVITY_COLUMNS = ["molecule_chembl_id", "uniprot", "pchembl_max", "n_activities", "first_year", "standard_types"]


def say(msg):
    """Print one progress line with the clock time, flushed so it shows up at once in the terminal."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def release(con):
    """The ChEMBL release named in the database's version table (e.g. 'ChEMBL_37'), or '' if absent.
    The table also lists the ontologies the release uses (e.g. 'Bioassay Ontology 2.0'), so pick the ChEMBL row."""
    try:
        names = [str(r[0]) for r in con.execute("SELECT name FROM version").fetchall()]
    except sqlite3.Error:
        return ""
    chembl = [n for n in names if n.lower().startswith("chembl")]
    return chembl[0] if chembl else (names[0] if names else "")


def report_from_files(db_path, out_dir=OUT):
    """Rebuilds the report dict from an existing output folder without re-running the queries: release from the
    database, row counts from the three TSVs already written. Used by --report-only."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rel = release(con)
    con.close()
    rows = {}
    for name in ("activities", "mechanisms", "indications"):
        with gzip.open(Path(out_dir) / f"{name}.tsv.gz", "rt") as fh:
            rows[name] = sum(1 for _ in fh) - 1
    return {"release": rel, "rows": rows}


def has_molregno_index(con):
    """True when the activities table has an index whose first column is molregno (chunking is then cheap)."""
    for row in con.execute("PRAGMA index_list(activities)").fetchall():
        cols = con.execute(f"PRAGMA index_info('{row[1]}')").fetchall()
        if cols and cols[0][2] == "molregno":
            return True
    return False


def molregno_ranges(con, chunks):
    """Takes the connection and a chunk count; returns [(lo, hi), ...] covering every molregno in activities."""
    lo, hi = con.execute("SELECT MIN(molregno), MAX(molregno) FROM activities").fetchone()
    if lo is None:
        return []
    width = max(1, (hi - lo + 1) // chunks + 1)
    return [(a, min(a + width - 1, hi)) for a in range(lo, hi + 1, width)]


def sorted_types(value):
    """'Ki,IC50,Ki' -> 'IC50,Ki' (unique, sorted) so the bytes do not depend on SQLite's visiting order."""
    if not isinstance(value, str):
        return value
    return ",".join(sorted(set(value.split(","))))


def activities(con, chunks=CHUNKS, log=say):
    """Takes an open connection. Builds the human-assay temp table, aggregates activities per
    (molecule, UniProt) in molregno chunks, maps molregno -> ChEMBL id, and returns the sorted DataFrame
    with ACTIVITY_COLUMNS."""
    t0 = time.time()
    con.execute("DROP TABLE IF EXISTS human_assay")
    con.execute(HUMAN_ASSAYS)
    con.execute("CREATE INDEX human_assay_idx ON human_assay(assay_id)")
    n_assays = con.execute("SELECT COUNT(*) FROM human_assay").fetchone()[0]
    log(f"activities: {n_assays:,} human single-protein assays listed ({time.time() - t0:.0f}s)")
    ranges = molregno_ranges(con, chunks) if (chunks > 1 and has_molregno_index(con)) else molregno_ranges(con, 1)
    if len(ranges) == 1 and chunks > 1:
        log("activities: no molregno index on activities -> one pass (no progress shown inside it)")
    frames = []
    t1 = time.time()
    for i, (lo, hi) in enumerate(ranges, 1):
        frames.append(pd.read_sql_query(ACTIVITY_CHUNK, con, params=(lo, hi)))
        done, elapsed = i / len(ranges), time.time() - t1
        log(f"activities: chunk {i}/{len(ranges)} (molregno {lo}-{hi}): {sum(len(f) for f in frames):,} pairs so far; "
            f"{elapsed / 60:.1f} min elapsed, about {elapsed * (1 - done) / done / 60:.1f} min left")
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["molregno", "uniprot", "pchembl_max", "n_activities", "first_year", "standard_types"])
    names = pd.read_sql_query("SELECT molregno, chembl_id AS molecule_chembl_id FROM molecule_dictionary", con)
    df = df.merge(names, on="molregno", how="inner")
    df["standard_types"] = df["standard_types"].map(sorted_types)
    df = df[ACTIVITY_COLUMNS].sort_values(["molecule_chembl_id", "uniprot"], kind="mergesort").reset_index(drop=True)
    con.execute("DROP TABLE IF EXISTS human_assay")
    return df


def write_tsv(df, path):
    """Gzipped TSV with a fixed mtime so a rebuild gives identical bytes."""
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        gz.write(df.to_csv(sep="\t", index=False).encode())


def convert(db_path, out_dir=OUT, chunks=CHUNKS, log=say):
    """Takes the SQLite file and an output folder. Writes activities, mechanisms and indications as gzipped TSVs
    (sorted, deterministic bytes). Returns {'release': ..., 'rows': {table: n}}."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.execute(f"PRAGMA cache_size = -{CACHE_MB * 1024}")
    con.execute("PRAGMA temp_store = MEMORY")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    log(f"database {db_path} ({release(con) or 'release not stated'})")
    t = time.time()
    df = activities(con, chunks=chunks, log=log)
    write_tsv(df, out_dir / "activities.tsv.gz")
    rows["activities"] = len(df)
    log(f"activities: {len(df):,} rows written ({(time.time() - t) / 60:.1f} min)")
    for name, sql in (("mechanisms", MECHANISMS), ("indications", INDICATIONS)):
        t = time.time()
        df = pd.read_sql_query(sql, con)
        write_tsv(df, out_dir / f"{name}.tsv.gz")
        rows[name] = len(df)
        log(f"{name}: {len(df):,} rows written ({time.time() - t:.0f}s)")
    rep = {"release": release(con), "rows": rows}
    con.close()
    return rep


def report_markdown(rep):
    """Report dict -> markdown."""
    lines = ["# ChEMBL 37 tables for the L1 card (sources/chembl37.yaml)", "",
             f"Release named in the database: {rep['release'] or 'not stated'}", "", "| table | rows |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in rep["rows"].items()]
    return "\n".join(lines) + "\n"


def find_db(root=ROOT):
    """The first chembl_*.db under data/external/chembl, or None."""
    return next(iter(sorted((root / "data" / "external" / "chembl").glob("**/chembl_*.db"))), None)


def main(argv):
    chunks = CHUNKS
    if "--chunks" in argv:
        i = argv.index("--chunks")
        chunks = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    report_only = "--report-only" in argv
    argv = [a for a in argv if a != "--report-only"]
    db = Path(argv[0]) if argv else find_db()
    if db is None or not db.exists():
        sys.exit("ChEMBL SQLite not found: run scripts/download_external_data.sh (without SKIP_CHEMBL) on the Mac "
                 "or pass the .db path")
    rep = report_from_files(db) if report_only else convert(db, chunks=chunks)
    out = ROOT / "outputs" / "chembl37"
    out.mkdir(parents=True, exist_ok=True)
    (out / "prepare.md").write_text(report_markdown(rep))
    print(report_markdown(rep))


if __name__ == "__main__":
    main(sys.argv[1:])
