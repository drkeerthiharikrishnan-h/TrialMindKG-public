#!/usr/bin/env bash
# E008 (exploratory, pre-registered 2026-11-05 in loop/ledger.tsv): GNN + path features (pipelines/linkpred/gnnpath.py)
# on G0 then G2, frozen flags, 1M edge cap. Run from the owner's Mac Terminal (3-4 h; caffeinate keeps the Mac awake):
#     cd ~/projects/TrialMindKG && bash scripts/e008_run.sh
# Progress: grep -h "fold\|->" logs/linkpred_gnnpath_1m.log
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD:$PWD/pipelines"
TAG=diag_gnnpath_1m
LOG=logs/linkpred_gnnpath_1m.log
echo "E008 $TAG started $(date '+%F %T %Z') on the Mac (owner Terminal); log $LOG" > logs/RUNNING_gnnpath_1m.txt
caffeinate -i .venv/bin/python -u -m pipelines.linkpred.gnnpath -- \
  --graphs G0,G2 --tasks B --splits kfold --seeds 1 --epochs 300 --hidden 64 --reverse-edges --learn-embeddings \
  --num-bases 30 --neg-pool training --positives assertions --baselines --metapaths --metapath-len 3 \
  --neg-match degree --strata --test-levels knowledge_assertion --drop-levels text_co_occurrence \
  --max-edges 1000000 --tag "$TAG" 2>&1 | tee "$LOG"
echo "FINISHED $(date '+%F %T %Z') exit ${PIPESTATUS[0]}" >> logs/RUNNING_gnnpath_1m.txt
