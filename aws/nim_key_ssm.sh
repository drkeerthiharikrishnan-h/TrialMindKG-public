#!/usr/bin/env bash
# STEP 3: store the NVIDIA key in SSM Parameter Store (SecureString /trialmindkg/NVIDIA_API_KEY). The app instance reads it at
# start; it is never in the repo, the image or the user-data. The key is taken from the environment variable NVIDIA_API_KEY
# or typed at a hidden prompt, goes through a 0600 temp file (not the command line, so `ps` cannot show it) and is deleted.
# Usage: NVIDIA_API_KEY=... bash aws/nim_key_ssm.sh [--dry-run]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "$0")/lib.sh"
init "$@"
NAME="/trialmindkg/NVIDIA_API_KEY"
KEYFILE="$(mktemp)"
chmod 600 "$KEYFILE"
trap 'rm -f "$KEYFILE"' EXIT
if [ "$DRY_RUN" = 1 ]; then
  echo "# dry-run: the key is not read; the temp file stays empty"
elif [ -n "${NVIDIA_API_KEY:-}" ]; then
  printf '%s' "$NVIDIA_API_KEY" > "$KEYFILE"
else
  printf 'NVIDIA API key (hidden): ' >&2
  read -rs key
  echo >&2
  printf '%s' "$key" > "$KEYFILE"
fi
awsx ssm put-parameter --name "$NAME" --type SecureString --overwrite --value "file://${KEYFILE}"
awsx ssm add-tags-to-resource --resource-type Parameter --resource-id "$NAME" --tags "Key=${TAG_KEY},Value=${TAG_VALUE}"
echo "Stored $NAME (SecureString). Rotate it by running this script again."
