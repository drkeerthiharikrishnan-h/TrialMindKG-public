#!/bin/bash
# Fresh-clone check (CC-R11): does the repository build and verify from nothing?
#
#   bash scripts/fresh_clone_check.sh <repo path or URL>       clone (git clone --depth 1) into a temp dir, then check
#   bash scripts/fresh_clone_check.sh --no-clone <checkout>    use an existing checkout (sandboxes that cannot clone)
#   options: --skip-demo, --skip-check (one step only), --keep (do not delete the temp dir)
#
# Steps, each printed as PASS or FAIL:  clone -> venv (new, in a temp dir) -> install (requirements.lock.txt if present,
# else requirements.txt, plus requirements-ml.txt) -> pip install -e . (pyproject.toml, FIX6 M7) -> make check -> make demo.
# The repository is a required argument (FIX6 S6: give the public URL; no default). Exit 0 when every step passed,
# 1 otherwise, 2 on bad arguments. Logs: <temp dir>/<step>.log (path printed at the end).
set -u
usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }
SRC=""; CLONE=1; DEMO=1; CHECK=1; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --no-clone) CLONE=0; SRC="${2:-}"; shift ;;
    --skip-demo) DEMO=0 ;;
    --skip-check) CHECK=0 ;;
    --keep) KEEP=1 ;;
    -*) echo "unknown option $1" >&2; usage >&2; exit 2 ;;
    *) SRC="$1" ;;
  esac
  shift
done
if [ -z "$SRC" ]; then echo "give the repository to check (the public URL, or --no-clone <checkout>)" >&2; usage >&2; exit 2; fi
if [ "$CLONE" = 0 ] && [ ! -f "$SRC/Makefile" ]; then echo "--no-clone needs a checkout with a Makefile: '$SRC'" >&2; exit 2; fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/tmkg_fresh.XXXXXX"); FAILED=0
step() {   # step <name> <command...>: runs the command, logs to $TMP/<name>.log, prints PASS/FAIL with the time
  local name=$1; shift; local t0=$(date +%s)
  if "$@" > "$TMP/$name.log" 2>&1; then echo "PASS $name ($(( $(date +%s) - t0 )) s)"
  else echo "FAIL $name ($(( $(date +%s) - t0 )) s) - last lines of $TMP/$name.log:"; tail -15 "$TMP/$name.log" | sed 's/^/    /'; FAILED=1; return 1; fi
}

if [ "$CLONE" = 1 ]; then
  step clone git clone --depth 1 "$SRC" "$TMP/repo" || { echo "RESULT: FAIL (no checkout)"; exit 1; }
  REPO="$TMP/repo"
else
  REPO=$(cd "$SRC" && pwd); echo "SKIP clone (--no-clone: $REPO)"
fi
PYBIN=${PYTHON:-python3}
step venv "$PYBIN" -m venv "$TMP/venv" || { echo "RESULT: FAIL (no venv)"; exit 1; }
VPY="$TMP/venv/bin/python"
REQ="$REPO/requirements.lock.txt"; [ -f "$REQ" ] || REQ="$REPO/requirements.txt"
install() { "$VPY" -m pip install -q --upgrade pip && "$VPY" -m pip install -q -r "$REQ" && "$VPY" -m pip install -q -r "$REPO/requirements-ml.txt"; }
step install install || { echo "RESULT: FAIL (install)"; exit 1; }
step pip_install_e "$VPY" -m pip install -q -e "$REPO" && step trialmindkg_help "$TMP/venv/bin/trialmindkg" --help
[ "$CHECK" = 1 ] && step make_check make -C "$REPO" check PY="$VPY"
if [ "$DEMO" = 1 ]; then
  step make_demo make -C "$REPO" demo PY="$VPY" && sed 's/^/    /' "$REPO/outputs/demo/SUMMARY.txt"
fi
echo "logs: $TMP"
if [ "$FAILED" = 0 ]; then echo "RESULT: PASS"; else echo "RESULT: FAIL"; fi
[ "$KEEP" = 1 ] || [ "$FAILED" = 1 ] || rm -rf "$TMP"
exit $FAILED
