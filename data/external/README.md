# data/external - downloaded public reference data

Not stored in git (MONDO alone is ~240 MB; GitHub's limit is 100 MB per file).
Re-create it any time with:

    bash scripts/download_external_data.sh            # everything (ChEMBL is several GB)
    SKIP_CHEMBL=1 bash scripts/download_external_data.sh   # skip ChEMBL

| Folder    | Contents                                          | Source |
|-----------|---------------------------------------------------|--------|
| biolink/  | biolink-model.yaml (BioLink Model schema)         | github.com/biolink/biolink-model |
| mondo/    | mondo.owl (latest MONDO release)                  | github.com/monarch-initiative/mondo releases |
| chembl/   | ChEMBL SQLite database (release set in script)    | ftp.ebi.ac.uk |
| gtex/     | GTEx v8 sample attributes + data dictionary       | storage.googleapis.com/adult-gtex |

`DATA_MANIFEST.tsv` (committed) records, for each file: source URL, retrieval
date (UTC), size in bytes and sha256 - the provenance for anything the
pipelines read from this folder. Download logs are in `logs/`.
