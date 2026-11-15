#!/bin/bash
# The frozen benchmark, flags from scripts/frozen_flags.txt (the one place they are written).
#   bash scripts/benchmark_frozen.sh <graphs, e.g. G0,G2,G3> <tag>
# then: python -m pipelines.linkpred.compare --base G0@<tag>
# Needs outputs/<g>/heterodata.pt for every graph (python pipelines/make_graphs.py). ~10.5 h per graph on a Mac, ~2 h on an H100.
set -eu
if [ $# -ne 2 ]; then sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; fi
R=$(cd "$(dirname "$0")/.." && pwd)
PY=${PY:-$R/.venv/bin/python}
FLAGS=$(grep -v '^#' "$R/scripts/frozen_flags.txt" | tr '\n' ' ')
cd "$R"
# shellcheck disable=SC2086
exec "$PY" -m pipelines.linkpred.run --graphs "$1" $FLAGS --tag "$2"
