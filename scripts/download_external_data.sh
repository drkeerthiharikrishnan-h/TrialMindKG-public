#!/usr/bin/env bash
################################################################################
# Download reference data sources for TrialMindKG into data/external/
#
# Usage:   bash scripts/download_external_data.sh [output_directory]
# Default: <repo>/data/external   (git-ignored: files are large and re-creatable)
#
# What it fetches:
#   1. BioLink Model schema (YAML)          - predicate/class vocabulary
#   2. MONDO disease ontology (latest OWL)  - disease identifiers
#   3. ChEMBL SQLite (release set below)    - drugs, targets, mechanisms (5.8 GB download)
#   4. GTEx v8 sample attributes            - tissue sample metadata
#
# Writes DATA_MANIFEST.tsv: one row per file with source URL, retrieved date,
# size in bytes and sha256, so every local file has provenance.
# Each step is independent: a failed download is logged and the script moves on.
################################################################################

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$REPO_ROOT/data/external}"
CHEMBL_RELEASE="${CHEMBL_RELEASE:-chembl_37}"
SKIP_CHEMBL="${SKIP_CHEMBL:-0}"   # set SKIP_CHEMBL=1 to skip the multi-GB download

mkdir -p "$OUTPUT_DIR"/{biolink,mondo,chembl,gtex,logs}
LOG_FILE="$OUTPUT_DIR/logs/download_$(date +%Y%m%d_%H%M%S).txt"
MANIFEST="$OUTPUT_DIR/DATA_MANIFEST.tsv"
RETRIEVED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log() { echo "$(date '+%H:%M:%S') $*" | tee -a "$LOG_FILE"; }
FAILED=0
fail() { log "$*"; FAILED=$((FAILED + 1)); }

# GNU tar needs --wildcards for '*.dmp' patterns; macOS bsdtar rejects the flag and matches patterns by default.
TAR_WILD=""
tar --version 2>/dev/null | grep -q "GNU tar" && TAR_WILD="--wildcards"

sha256() {
    if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# fetch NAME DEST URL [URL...]
# Tries each URL in turn; on success appends a manifest row. Returns 0/1.
fetch() {
    local name="$1" dest="$2"; shift 2
    local url
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
    fail "[$name] FAILED - see $LOG_FILE"
    return 1
}

# fetch_resumable NAME DEST URL: for a multi-GB file. Keeps DEST.part when the connection breaks and continues it
# on the next attempt (curl -C -; EBI answers byte-range requests), up to 30 attempts 20 s apart, and on a re-run of
# the script. The file counts as complete only when its size equals the server's Content-Length.
fetch_resumable() {
    local name="$1" dest="$2" url="$3" attempt have want
    want=$(curl -sIL --max-time 60 "$url" 2>>"$LOG_FILE" | tr -d '\r' | grep -i '^content-length:' | tail -n1 | awk '{print $2}')
    [ -n "$want" ] && log "[$name] expected size: $want bytes ($((want / 1048576)) MB)"
    for attempt in $(seq 1 30); do
        have=0; [ -f "$dest.part" ] && have=$(wc -c <"$dest.part" | tr -d ' ')
        if [ -n "$want" ] && [ "$have" -ge "$want" ]; then
            break
        fi
        log "[$name] attempt $attempt: $url (have $((have / 1048576)) MB, resuming)"
        curl -L --fail --silent --show-error -C - "$url" -o "$dest.part" 2>>"$LOG_FILE" && break
        sleep 20
    done
    have=0; [ -f "$dest.part" ] && have=$(wc -c <"$dest.part" | tr -d ' ')
    if [ "$have" -gt 0 ] && { [ -z "$want" ] || [ "$have" -eq "$want" ]; }; then
        mv "$dest.part" "$dest"
        manifest_row "$name" "$dest" "$url"
        log "[$name] OK ($(du -h "$dest" | cut -f1))"
        return 0
    fi
    fail "[$name] FAILED after $attempt attempts: have $have of ${want:-?} bytes; the partial file is kept, re-run to continue - see $LOG_FILE"
    return 1
}

# manifest_row NAME DEST URL: appends a manifest row for a file already on disk (size + sha256).
manifest_row() {
    local bytes; bytes=$(wc -c <"$2" | tr -d ' ')
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "${2#"$OUTPUT_DIR"/}" "$3" "$RETRIEVED" "$bytes" "$(sha256 "$2")" >>"$MANIFEST"
}

printf 'source\tfile\turl\tretrieved_utc\tbytes\tsha256\n' >"$MANIFEST"
log "Output directory: $OUTPUT_DIR"

# 1. BioLink Model
fetch biolink "$OUTPUT_DIR/biolink/biolink-model.yaml" \
    "https://w3id.org/biolink/biolink-model.yaml" \
    "https://raw.githubusercontent.com/biolink/biolink-model/master/biolink-model.yaml"

# 2. MONDO (latest GitHub release; purl as fallback)
if fetch mondo "$OUTPUT_DIR/mondo/mondo.owl" \
    "https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.owl" \
    "https://purl.obolibrary.org/obo/mondo.owl"; then
    VERSION=$(grep -m1 -o 'owl:versionIRI rdf:resource="[^"]*"' "$OUTPUT_DIR/mondo/mondo.owl" | cut -d'"' -f2)
    log "[mondo] version: ${VERSION:-unknown}"
fi

# 3. ChEMBL SQLite (EBI publishes <release>_sqlite.tar.gz)
if [ "$SKIP_CHEMBL" = "1" ]; then
    log "[chembl] skipped (SKIP_CHEMBL=1)"
else
    CHEMBL_DIR="https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/releases/${CHEMBL_RELEASE}"
    CHEMBL_TGZ="$OUTPUT_DIR/chembl/${CHEMBL_RELEASE}_sqlite.tar.gz"
    CHEMBL_DB=$(find "$OUTPUT_DIR/chembl" -name "${CHEMBL_RELEASE}.db" 2>/dev/null | head -1)
    if [ -n "$CHEMBL_DB" ] && [ ! -s "$CHEMBL_TGZ" ]; then
        # A finished unpack deletes the archive; a leftover archive means the unpack did not complete.
        log "[chembl] already unpacked: $CHEMBL_DB (delete it to re-download)"
    else
        if [ -s "$CHEMBL_TGZ" ]; then
            log "[chembl] reusing downloaded archive $CHEMBL_TGZ ($(du -h "$CHEMBL_TGZ" | cut -f1))"
            manifest_row chembl "$CHEMBL_TGZ" "$CHEMBL_DIR/${CHEMBL_RELEASE}_sqlite.tar.gz"
        else
            fetch_resumable chembl "$CHEMBL_TGZ" "$CHEMBL_DIR/${CHEMBL_RELEASE}_sqlite.tar.gz"
        fi
        if [ -s "$CHEMBL_TGZ" ]; then
            # Compare with EBI's published sha256 before unpacking (the manifest row holds the local sha256).
            WANT=$(curl -fsSL --max-time 60 "$CHEMBL_DIR/checksums.txt" 2>>"$LOG_FILE" | grep "${CHEMBL_RELEASE}_sqlite.tar.gz" | cut -f1)
            HAVE=$(tail -n1 "$MANIFEST" | cut -f6)
            if [ -n "$WANT" ] && [ "$WANT" != "$HAVE" ]; then
                fail "[chembl] checksum MISMATCH: archive is truncated or corrupt. Delete $CHEMBL_TGZ and re-run."
            else
                [ -z "$WANT" ] && log "[chembl] checksums.txt not reachable; unpacking unverified"
                [ -n "$WANT" ] && log "[chembl] sha256 verified"
                rm -rf "$OUTPUT_DIR/chembl/${CHEMBL_RELEASE}"           # a partial earlier unpack, if any
                log "[chembl] unpacking (needs ~30 GB free; several minutes)..."
                TAR_ERR=$(tar -xzf "$CHEMBL_TGZ" -C "$OUTPUT_DIR/chembl" 2>&1 >/dev/null)
                if [ -z "$TAR_ERR" ]; then
                    rm -f "$CHEMBL_TGZ"
                    log "[chembl] unpacked: $(find "$OUTPUT_DIR/chembl" -name '*.db' | head -1)"
                else
                    echo "$TAR_ERR" >>"$LOG_FILE"
                    rm -rf "$OUTPUT_DIR/chembl/${CHEMBL_RELEASE}"       # never leave a half-written database
                    fail "[chembl] unpack FAILED (archive kept): $(echo "$TAR_ERR" | head -n1); free disk: $(df -h "$OUTPUT_DIR" | awk 'NR==2{print $4}')"
                fi
            fi
        fi
    fi
fi

# 3b. ChEMBL synonym table (every name / research code / trade name + salt -> parent), streamed from the MySQL
#     dump: ~2.1 GB downloaded, nothing large stored; writes data/reference/chembl_synonyms.tsv.gz (committed)
if [ "$SKIP_CHEMBL" = "1" ]; then
    log "[chembl-synonyms] skipped (SKIP_CHEMBL=1)"
else
    log "[chembl-synonyms] streaming ${CHEMBL_RELEASE}_mysql.tar.gz (takes 20-40 min)..."
    PY="${PYTHON:-python3}"
    # pipefail: a broken curl or tar stage fails the step; chembl_synonyms.py keeps the committed table unless
    # it extracted at least one molecule.
    if ( set -o pipefail; curl -fsSL "https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/releases/${CHEMBL_RELEASE}/${CHEMBL_RELEASE}_mysql.tar.gz" \
        | tar -xzO $TAR_WILD '*.dmp' | "$PY" "$REPO_ROOT/pipelines/chembl_synonyms.py" "$CHEMBL_RELEASE" 2>>"$LOG_FILE" | tee -a "$LOG_FILE" ); then
        log "[chembl-synonyms] done"
    else
        fail "[chembl-synonyms] FAILED (see log)"
    fi
fi

# 4. GTEx v8 sample attributes + data dictionary
GTEX_BASE="https://storage.googleapis.com/adult-gtex/annotations/v8/metadata-files"
fetch gtex "$OUTPUT_DIR/gtex/GTEx_Analysis_v8_Annotations_SampleAttributesDS.txt" \
    "$GTEX_BASE/GTEx_Analysis_v8_Annotations_SampleAttributesDS.txt"
fetch gtex "$OUTPUT_DIR/gtex/GTEx_Analysis_v8_Annotations_SampleAttributesDD.xlsx" \
    "$GTEX_BASE/GTEx_Analysis_v8_Annotations_SampleAttributesDD.xlsx"

log "Done. Manifest: $MANIFEST"
column -t -s $'\t' "$MANIFEST" 2>/dev/null | cut -c1-160 | tee -a "$LOG_FILE" || cat "$MANIFEST"
if [ "$FAILED" -gt 0 ]; then log "$FAILED step(s) FAILED - see $LOG_FILE"; exit 1; fi
