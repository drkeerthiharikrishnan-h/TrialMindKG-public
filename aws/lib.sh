# shellcheck shell=bash
# Shared helpers for the aws/ scripts. Source it; do not run it.
#   init "$@"     parses the common flags (--dry-run, --yes) and leaves the other arguments in ARGS
#   run CMD...    prints the command ("+ aws ...") and, unless --dry-run, runs it
#   cap PLACEHOLDER CMD...   as run, but returns the command's output; in --dry-run it returns PLACEHOLDER instead
#   awsx ARGS     run aws ARGS --region "$AWS_REGION"          awsx_cap PLACEHOLDER ARGS   likewise, with output
#   render FILE   prints FILE with BUCKET_NAME / ACCOUNT_ID / REGION / TAG_VALUE (and the user-data placeholders) filled in
# Every script is meant to be read and dry-run first: bash aws/<script>.sh --dry-run prints every call and changes nothing.
# Nothing here reads or prints a credential. Resources are tagged project=trialmindkg (TAG_KEY / TAG_VALUE).

DRY_RUN=0
YES=0
ARGS=()
TAG_KEY="project"
TAG_VALUE="trialmindkg"
export TAG_KEY TAG_VALUE

init() {
  ARGS=()
  for a in "$@"; do
    case "$a" in
      --dry-run) DRY_RUN=1 ;;
      --yes) YES=1 ;;
      *) ARGS+=("$a") ;;
    esac
  done
  ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)"
  AWS_DIR="$ROOT_DIR/aws"
  local cfg="${TMKG_AWS_CONFIG:-$AWS_DIR/config.env}"     # TMKG_AWS_CONFIG: another config file (tests, a second account)
  if [ -f "$cfg" ]; then
    # shellcheck disable=SC1090
    source "$cfg"
  elif [ "$DRY_RUN" = 1 ]; then
    # shellcheck disable=SC1091
    source "$AWS_DIR/config.example.env"
    echo "# no aws/config.env: dry-run uses the example values"
  else
    echo "aws/config.env is missing: copy aws/config.example.env and fill it in" >&2
    exit 2
  fi
  : "${AWS_REGION:=us-east-1}"
  : "${TMKG_BUCKET:?TMKG_BUCKET is not set}"
  ACCOUNT_ID="${AWS_ACCOUNT_ID:-123456789012}"
  export AWS_REGION
  if [ "$DRY_RUN" = 1 ]; then echo "# DRY RUN: nothing below is executed"; fi
}

show() {   # one line, quoted only where an argument needs it
  printf '+'
  local a
  for a in "$@"; do
    case "$a" in
      "" | *[!]A-Za-z0-9_@%+=:,./{}[-]*) printf " '%s'" "$(sq_escape "$a")" ;;
      *) printf ' %s' "$a" ;;
    esac
  done
  printf '\n'
}

# sq_escape TEXT: TEXT with every ' written as '\'' (for a single-quoted shell word). No ${var//pat/rep}: macOS bash 3.2
# handles quotes in its replacement differently from bash 4 and 5, so the dry-run lines differed between the two.
sq_escape() {
  local rest="$1" out="" sq="'"
  while [[ $rest == *"$sq"* ]]; do
    out=$out${rest%%"$sq"*}$sq\\$sq$sq
    rest=${rest#*"$sq"}
  done
  printf '%s' "${out}${rest}"
}

run() {
  show "$@"
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  "$@"
}

cap() {
  local placeholder="$1"
  shift
  show "$@" >&2
  if [ "$DRY_RUN" = 1 ]; then
    echo "$placeholder"
    return 0
  fi
  "$@"
}

awsx() { run aws "$@" --region "$AWS_REGION"; }
awsx_cap() {
  local placeholder="$1"
  shift
  cap "$placeholder" aws "$@" --region "$AWS_REGION"
}

# neptune_host: the cluster endpoint the hosted app may name (NEPTUNE_ENDPOINT of aws/config.env), or nothing. Only the
# writer endpoint of the project's own cluster tmkg-neptune counts (the host timer of user_data.sh.tmpl reads and wakes that
# cluster by name); anything else, the example value of config.example.env included, renders as empty with one note on stderr.
neptune_host() {
  local ep="${NEPTUNE_ENDPOINT:-}"
  if [ -z "$ep" ]; then return 0; fi
  if [[ "$ep" =~ ^tmkg-neptune\.cluster-[a-z0-9]+\.[a-z0-9-]+\.neptune\.amazonaws\.com$ ]]; then
    printf '%s' "$ep"
  else
    echo "# NEPTUNE_ENDPOINT '$ep' is not the endpoint of the cluster tmkg-neptune: the app is launched without Amazon Neptune" >&2
  fi
}
# neptune_wake_min: NEPTUNE_WAKE_MINUTES when it is a whole number of minutes (1 to 99), else nothing
neptune_wake_min() {
  local m="${NEPTUNE_WAKE_MINUTES:-}"
  if [[ "$m" =~ ^[1-9][0-9]?$ ]]; then printf '%s' "$m"; fi
}

# render FILE: the placeholders below filled in. NEPTUNE_HOST / NEPTUNE_WAKE_MIN (user_data.sh.tmpl only): when the Neptune
# host is empty, every block from a line '# >>> PK14 A2 ...' to the next '# <<< PK14 A2' is left out except its first
# (comment) line, so a launch without Neptune gets the same boot script as before (PK14 A2). The NEPTUNE_WAKE_MINUTES name
# itself is protected first, because it begins with the placeholder NEPTUNE_WAKE_MIN.
render() {
  local nhost="" nwake=""
  if grep -q 'NEPTUNE_HOST' "$1"; then
    nhost="$(neptune_host)"
    nwake="$(neptune_wake_min)"
  fi
  awk -v keep="${nhost:+1}" '
    skip && /^[[:space:]]*# <<< PK14 A2/ { skip = 0; next }
    skip { next }
    /^[[:space:]]*# >>> PK14 A2/ && keep != "1" { print; skip = 1; next }
    { print }' "$1" |
  sed -e "s/BUCKET_NAME/${TMKG_BUCKET}/g" -e "s/ACCOUNT_ID/${ACCOUNT_ID}/g" -e "s/REGION/${AWS_REGION}/g" \
      -e "s/TAG_VALUE/${TAG_VALUE}/g" -e "s/TLS_MODE/${TMKG_TLS:-cloudfront}/g" \
      -e "s/PUBLIC_HOST/${TMKG_PUBLIC_HOST:-}/g" -e "s/ACME_EMAIL/${TMKG_ACME_EMAIL:-}/g" \
      -e "s/NEPTUNE_WAKE_MINUTES/@TMKG_WAKE_NAME@/g" -e "s/NEPTUNE_WAKE_MIN/${nwake}/g" \
      -e "s/@TMKG_WAKE_NAME@/NEPTUNE_WAKE_MINUTES/g" -e "s/NEPTUNE_HOST/${nhost}/g"
}

# render FILE to a private temp file and print its path (removed by the caller's trap or at exit)
render_tmp() {
  local f
  f="$(mktemp)"
  chmod 600 "$f"
  render "$1" > "$f"
  echo "$f"
}

confirm() {   # confirm "question": passes with --yes or --dry-run, else asks
  if [ "$YES" = 1 ] || [ "$DRY_RUN" = 1 ]; then return 0; fi
  printf '%s [y/N] ' "$1" >&2
  read -r answer
  [ "$answer" = "y" ]
}
