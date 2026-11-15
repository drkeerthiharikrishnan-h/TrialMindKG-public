#!/usr/bin/env bash
# Upload one graph version to S3, keyed by the snapshot fingerprint in outputs/run_manifest.json:
#   s3://$TMKG_BUCKET/trialmindkg/<fingerprint>/{outputs,snapshots,snapshots_raw,snapshots_names,snapshots_llm}/
# Usage: bash aws/upload_s3.sh [--dry-run]            (reads aws/config.env)
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
cd "$ROOT_DIR"
FP=$(python3 -c "import json;print(json.load(open('outputs/run_manifest.json'))['snapshot']['fingerprint'][:12])")
DEST="s3://${TMKG_BUCKET}/trialmindkg/${FP}"
echo "Uploading graph version ${FP} to ${DEST}"
run aws s3 sync outputs/ "${DEST}/outputs/" --region "${AWS_REGION}" --exclude "*.png"
for d in snapshots snapshots_raw snapshots_names snapshots_llm; do
  if [ -d "data/${d}" ] || [ "$DRY_RUN" = 1 ]; then
    run aws s3 sync "data/${d}/" "${DEST}/${d}/" --region "${AWS_REGION}"
  else
    echo "# skipped (no data/${d})"
  fi
done
if [ "$DRY_RUN" = 0 ]; then echo "${FP}" > outputs/last_s3_version.txt; fi
echo "Done. Neptune load source: ${DEST}/outputs/neptune/opencypher/"
