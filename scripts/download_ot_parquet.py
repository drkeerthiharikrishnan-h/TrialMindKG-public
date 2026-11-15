#!/usr/bin/env python3
"""Download Open Targets 26.09 parquet datasets from EBI FTP.

Usage (on the Mac, from the project root):
    cd ~/projects/TrialMindKG && source .venv/bin/activate
    caffeinate -i python3 -I scripts/download_ot_parquet.py

Discovers part files from the HTML directory listing, downloads each,
merges with pyarrow, and writes one .parquet per dataset in data/opentargets/.

Requires: pyarrow.
"""

import html.parser, os, re, tempfile, time, urllib.request
from pathlib import Path

import pyarrow.parquet as pq

RELEASE = "26.09"
BASE_URL = f"https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/{RELEASE}/output"

# Maps our desired output name -> FTP directory name under output/
DATASETS = {
    "associationByOverallDirect": "association_overall_direct",
    "targets":                    "target",
    "diseases":                   "disease",
    "mechanismOfAction":          "drug_mechanism_of_action",
    "indication":                 "clinical_indication",
    "diseaseToPhenotype":         "disease_phenotype",
}

OUT_DIR = Path("data/opentargets")


def list_parts(ftp_dir: str) -> list[str]:
    """Scrape HTML directory listing for .parquet filenames."""
    url = f"{BASE_URL}/{ftp_dir}/"
    with urllib.request.urlopen(url, timeout=60) as r:
        body = r.read().decode()
    return re.findall(r'href="([^"]*\.parquet)"', body)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, ftp_dir in DATASETS.items():
        dest = OUT_DIR / f"{name}.parquet"
        if dest.exists():
            print(f"  skip {name} (already exists)")
            continue

        print(f"[{name}] listing {ftp_dir}/ ...", flush=True)
        parts = list_parts(ftp_dir)
        print(f"  {len(parts)} part file(s)")

        with tempfile.TemporaryDirectory() as tmp:
            for i, fname in enumerate(parts):
                url = f"{BASE_URL}/{ftp_dir}/{fname}"
                local = os.path.join(tmp, fname)
                print(f"  downloading {i+1}/{len(parts)}: {fname} ...", end=" ", flush=True)
                t0 = time.time()
                urllib.request.urlretrieve(url, local)
                mb = os.path.getsize(local) / 1e6
                print(f"{mb:.1f} MB ({time.time()-t0:.0f}s)")

            print("  merging ...", flush=True)
            table = pq.read_table(tmp)
            print(f"  {table.num_rows:,} rows, {table.num_columns} cols")
            pq.write_table(table, str(dest), compression="zstd")
            sz_mb = dest.stat().st_size / 1e6
            print(f"  saved {dest} ({sz_mb:.1f} MB)")

    print("\nDone. Datasets in", OUT_DIR)


if __name__ == "__main__":
    main()
