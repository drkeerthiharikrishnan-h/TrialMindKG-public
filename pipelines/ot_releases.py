"""
Open Targets bulk releases from the EBI FTP site - the newest release (enrichment layer) and one release per year
back to 2016 (the knowledge timeline on target-disease edges: when an association appeared, how its score moved).

Takes: nothing (release choices below). Writes: data/external/opentargets/<release>/<dataset>/<files>
(git-ignored) and data/reference/ot_releases_manifest.tsv (committed: release, dataset, folder url, files, bytes).

Folder names changed over the years (association_overall_direct in recent releases, associationByOverallDirect
under output/etl/... in 2020-2023, one association_data.json.gz file in 2016-2020, in output/ from
19.06), so each release folder is
searched (a few levels deep) for the dataset instead of assuming a path; a match must hold data files, and
intermediate/ and metadata/ folders (same names, no release data) are skipped. Directory listings and files go through
snapshot.Snapshot (get_text / download), never requests directly.

Commands:  python pipelines/ot_releases.py latest      (26.09 datasets for the enrichment layer)
           python pipelines/ot_releases.py timeline    (overall direct associations, one release per year)
"""
import csv, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot import Snapshot, ROOT  # noqa: E402

BASE = "https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/"
OUT = ROOT / "data" / "external" / "opentargets"
INDEX_SNAP = OUT / "_index"
MANIFEST = ROOT / "data" / "reference" / "ot_releases_manifest.tsv"
LATEST = "26.09"
LATEST_DATASETS = ["association_overall_direct", "association_by_datasource_direct", "target", "disease",
                   "drug_molecule", "drug_mechanism_of_action", "clinical_indication", "clinical_target",
                   "disease_phenotype", "evidence_eva", "evidence_gene_burden", "evidence_orphanet",
                   "evidence_gene2phenotype", "evidence_clingen", "evidence_cancer_gene_census",
                   "evidence_intogen", "evidence_uniprot_variants", "evidence_impc"]
# one release per year (first that exists wins): June release where available
YEARLY = {2016: ["16.04", "16.08"], 2017: ["17.06", "17.04"], 2018: ["18.06", "18.04"],
          2019: ["19.06", "19.09"], 2020: ["20.06", "20.04"], 2021: ["21.06", "21.04"], 2022: ["22.06", "22.04"],
          2023: ["23.06", "23.09"], 2024: ["24.06", "24.09"], 2025: ["25.06", "25.09"], 2026: [LATEST]}
DATA_FILE = re.compile(r"\.(parquet|json|json\.gz|jsonl|jsonl\.gz)$")


def norm(name):
    """'associationByOverallDirect' / 'association_overall_direct' -> 'associationoveralldirect'."""
    return re.sub(r"[^a-z]", "", name.lower()).replace("by", "")


def parse_listing(html):
    """Hrefs of an Apache directory listing, without parent links and sort links. Returns (dirs, files)."""
    hrefs = [h for h in re.findall(r'href="([^"?]+)"', html) if not h.startswith("/") and h != "../"]
    return [h.rstrip("/") for h in hrefs if h.endswith("/")], [h for h in hrefs if not h.endswith("/")]


def listing(snap, url):
    r = snap.get_text("ot_ftp", url)
    return parse_listing((r.body or {}).get("text", ""))


SKIP_DIRS = ("input", "jars", "webapp", "disk_images", "conf", "templates", "literature",
             "intermediate", "metadata", "excluded")   # same-named folders here hold no release data


def has_data_files(snap, url):
    """True if the folder at `url` directly holds at least one data file (parquet/json)."""
    _dirs, files = listing(snap, url)
    return any(DATA_FILE.search(f) for f in files)


def find_dataset(snap, release_url, dataset, depth=4):
    """URL of the folder whose name matches `dataset` (ignoring case, '_' and 'by') and that holds data files,
    searched breadth first; folders in SKIP_DIRS are not entered. Returns None if absent."""
    want, frontier = norm(dataset), [release_url]
    for _ in range(depth):
        nxt = []
        for url in frontier:
            dirs, _files = listing(snap, url)
            for d in dirs:
                if norm(d) == want and has_data_files(snap, url + d + "/"):
                    return url + d + "/"
                if d not in SKIP_DIRS:
                    nxt.append(url + d + "/")
        frontier = nxt
    return None


def download_folder(snap, folder_url, dest):
    """Download every data file of one folder. Returns (n_files, total_bytes)."""
    _dirs, files = listing(snap, folder_url)
    n = total = 0
    for f in files:
        if DATA_FILE.search(f):
            rec = snap.download(folder_url + f, dest / f)
            if rec["status"].startswith("ok"):
                n += 1
                total += rec["bytes"] or 0
            else:
                print(f"  {f}: {rec['status']}")
    return n, total


def old_association_file(snap, release):
    """2016-2020 releases: one '<release>_association_data.json.gz' file at the top of the release folder
    (2016-2018) or in its output/ folder (19.06, 20.06). Returns the file URL or None."""
    for folder in (BASE + release + "/", BASE + release + "/output/"):
        _dirs, files = listing(snap, folder)
        hit = next((f for f in files if f.endswith("association_data.json.gz")), None)
        if hit:
            return folder + hit
    return None


def write_manifest(rows, path=MANIFEST):
    """rows: dicts (release, dataset, url, files, bytes); merged with the existing manifest by (release, dataset)."""
    old = {}
    if path.exists():
        with open(path) as f:
            old = {(r["release"], r["dataset"]): r for r in csv.DictReader(f, delimiter="\t")}
    for r in rows:
        old[(r["release"], r["dataset"])] = {k: str(v) for k, v in r.items()}
    cols = ["release", "dataset", "url", "files", "bytes"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for k in sorted(old):
            w.writerow({c: old[k].get(c, "") for c in cols})


def fetch(release, datasets, snap):
    rows = []
    for ds in datasets:
        url = find_dataset(snap, BASE + release + "/", ds)
        if url is None and ds == "association_overall_direct":
            url = old_association_file(snap, release)
            if url:
                rec = snap.download(url, OUT / release / ds / url.rsplit("/", 1)[1])
                n, b = (1, rec["bytes"] or 0) if rec["status"].startswith("ok") else (0, 0)
                rows.append(dict(release=release, dataset=ds, url=url, files=n, bytes=b))
                print(f"{release} {ds}: {n} file, {b:,} bytes")
                continue
        if url is None:
            print(f"{release} {ds}: not found")
            continue
        n, b = download_folder(snap, url, OUT / release / ds)
        rows.append(dict(release=release, dataset=ds, url=url, files=n, bytes=b))
        print(f"{release} {ds}: {n} files, {b:,} bytes")
    write_manifest(rows)
    return rows


def timeline(snap):
    """Overall direct associations for one release per year (first candidate that has them)."""
    rows = []
    for year, candidates in YEARLY.items():
        for rel in candidates:
            got = fetch(rel, ["association_overall_direct"], snap)
            if got and got[0]["files"]:
                rows += got
                break
        else:
            print(f"{year}: no association data found in {candidates}")
    return rows


if __name__ == "__main__":
    snap = Snapshot(INDEX_SNAP, sleep=0.1)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "latest":
        fetch(LATEST, LATEST_DATASETS, snap)
    elif cmd == "timeline":
        timeline(snap)
    else:
        sys.exit(__doc__)
