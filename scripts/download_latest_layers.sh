#!/usr/bin/env bash
################################################################################
# Download the latest open data used ON TOP of Pharos (layers L1/L2 of the challenge graph) into data/external/
#
# Usage:   bash scripts/download_latest_layers.sh [output_directory]      (run on the Mac: several hosts are
#          blocked from the cloud container)
# Default: <repo>/data/external   (git-ignored)
#
# What it fetches (latest release at run time; the version lands in the manifest via URL/date/sha256):
#   1. HPO ontology + disease-phenotype annotations (phenotype.hpoa)   - human phenotypes (not in Pharos)
#   2. GO basic ontology (JSON)                                         - GO hierarchy for multi-hop paths
#   3. MONDO (JSON) + SSSOM mappings                                    - disease hierarchy, DOID/UMLS -> MONDO
#   4. HGNC complete set                                                - current / withdrawn gene ids (pruning)
#   5. Reactome pathways, hierarchy, UniProt -> pathway                 - pathway hierarchy
#   6. OnSIDES v3.1.0 (drug side effects from labels, open)             - drug safety context
#   7. DrugCentral 2023-11-01 dump                                      - newer than the 2022 copy inside Pharos
#   8. OptimusKG (Zitnik lab, Harvard Dataverse)                         - second public-KG baseline
# Open Targets releases (26.09 + one per year back to 2016) are fetched by pipelines/ot_releases.py.
# ChEMBL 37 is fetched by scripts/download_external_data.sh.
#
# Appends one row per file to DATA_MANIFEST.tsv (source, file, url, retrieved, bytes, sha256).
# Each step is independent: a failed download is logged and the script moves on.
# Files already on disk are skipped (no new manifest row), so a re-run only fetches what failed before.
################################################################################

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$REPO_ROOT/data/external}"
SKIP_OPTIMUSKG="${SKIP_OPTIMUSKG:-0}"   # set SKIP_OPTIMUSKG=1 to skip the large OptimusKG download
FORCE="${FORCE:-0}"                     # set FORCE=1 to download again files that are already on disk

mkdir -p "$OUTPUT_DIR"/{hpo,go,mondo,hgnc,reactome,onsides,drugcentral,optimuskg,logs}
LOG_FILE="$OUTPUT_DIR/logs/layers_$(date +%Y%m%d_%H%M%S).txt"
MANIFEST="$OUTPUT_DIR/DATA_MANIFEST.tsv"
RETRIEVED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log() { echo "$(date '+%H:%M:%S') $*" | tee -a "$LOG_FILE"; }

sha256() {
    if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# fetch NAME DEST URL [URL...]   (same as scripts/download_external_data.sh)
fetch() {
    local name="$1" dest="$2"; shift 2
    local url
    if [ "$FORCE" != "1" ] && [ -s "$dest" ]; then
        log "[$name] already on disk, skipped: ${dest#"$OUTPUT_DIR"/} (FORCE=1 to fetch again)"
        return 0
    fi
    for url in "$@"; do
        log "[$name] trying $url"
        if curl -L --fail --silent --show-error --retry 3 "$url" -o "$dest.part" 2>>"$LOG_FILE"; then
            mv "$dest.part" "$dest"
            local bytes; bytes=$(wc -c <"$dest" | tr -d ' ')
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "${dest#"$OUTPUT_DIR"/}" "$url" \
                "$RETRIEVED" "$bytes" "$(sha256 "$dest")" >>"$MANIFEST"
            log "[$name] OK ($(du -h "$dest" | cut -f1))"
            return 0
        fi
        rm -f "$dest.part"
    done
    log "[$name] FAILED - see $LOG_FILE"
    return 1
}

[ -f "$MANIFEST" ] || printf 'source\tfile\turl\tretrieved_utc\tbytes\tsha256\n' >"$MANIFEST"
log "Output directory: $OUTPUT_DIR"

OBO="https://purl.obolibrary.org/obo"
fetch hpo "$OUTPUT_DIR/hpo/hp.json" "$OBO/hp.json"
fetch hpo "$OUTPUT_DIR/hpo/phenotype.hpoa" "$OBO/hp/hpoa/phenotype.hpoa" \
    "https://github.com/obophenotype/human-phenotype-ontology/releases/latest/download/phenotype.hpoa"
fetch go "$OUTPUT_DIR/go/go-basic.json" "$OBO/go/go-basic.json"
fetch mondo "$OUTPUT_DIR/mondo/mondo.json" "$OBO/mondo.json" \
    "https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.json"
fetch mondo "$OUTPUT_DIR/mondo/mondo.sssom.tsv" \
    "https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.sssom.tsv" \
    "https://raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings/mondo.sssom.tsv"
fetch hgnc "$OUTPUT_DIR/hgnc/hgnc_complete_set.txt" \
    "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
fetch hgnc "$OUTPUT_DIR/hgnc/withdrawn.txt" \
    "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/withdrawn.txt"

RX="https://reactome.org/download/current"
for f in ReactomePathways.txt ReactomePathwaysRelation.txt UniProt2Reactome.txt; do
    fetch reactome "$OUTPUT_DIR/reactome/$f" "$RX/$f"
done

fetch onsides "$OUTPUT_DIR/onsides/onsides-v3.1.0.zip" \
    "https://github.com/tatonetti-lab/onsides/releases/download/v3.1.0/onsides-v3.1.0.zip"
fetch drugcentral "$OUTPUT_DIR/drugcentral/drugcentral.dump.11012023.sql.gz" \
    "https://unmtid-dbs.net/download/drugcentral.dump.11012023.sql.gz"

if [ "$SKIP_OPTIMUSKG" = "1" ]; then
    log "[optimuskg] skipped (SKIP_OPTIMUSKG=1)"
else
    fetch optimuskg "$OUTPUT_DIR/optimuskg/optimuskg_dataverse.zip" \
        "https://dataverse.harvard.edu/api/access/dataset/:persistentId/?persistentId=doi:10.7910/DVN/IYNGEV"
fi

log "Done. Manifest: $MANIFEST"
