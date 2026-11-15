#!/usr/bin/env bash
# The gold-set verdict file for Pharos: one row per Pharos source row with its edge, keep/prune verdict, confidence and
# the gold-set id columns (UniProt, HGNC, MONDO, Node Normalizer). Several hours; reads only data/kg/pharos (not affected
# by the Open Targets fix). Run from the owner's Terminal AFTER E008 has finished (one heavy Mac job at a time):
#     cd ~/projects/TrialMindKG && bash scripts/verdict_run.sh
# Writes data/kg/pharos/row_verdicts.tsv.gz (git-ignored) and outputs/pharos/verdict_file_report.md (committed).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD:$PWD/pipelines"
LOG=logs/verdict_file_pharos.log
echo "verdict file pharos started $(date '+%F %T %Z') (owner Terminal); log $LOG" > logs/RUNNING_verdict_file.txt
caffeinate -i .venv/bin/python -u pipelines/verdict_file.py sources/pharos.yaml --nodenorm 2>&1 | tee "$LOG"
echo "FINISHED $(date '+%F %T %Z') exit ${PIPESTATUS[0]}" >> logs/RUNNING_verdict_file.txt
