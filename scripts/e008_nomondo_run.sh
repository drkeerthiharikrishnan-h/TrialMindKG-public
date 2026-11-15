#!/usr/bin/env bash
# E008 control (pre-registered with E008): GNN + path features on G2 WITHOUT the MONDO disease hierarchy, frozen flags,
# 1M cap. Compared with G0 and full-G2 of tag diag_gnnpath_1m. Owner's Terminal (~7-8 h; caffeinate keeps the Mac awake):
#     cd ~/projects/TrialMindKG && bash scripts/e008_nomondo_run.sh
# Waits for a running card-index rebuild to finish first (one heavy job at a time).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD:$PWD/pipelines"
until [ ! -f logs/RUNNING_pharos_index.txt ] || grep -q FINISHED logs/RUNNING_pharos_index.txt; do sleep 60; done
TAG=diag_gnnpath_1m_nomondo
LOG=logs/linkpred_gnnpath_1m_nomondo.log
echo "E008 control $TAG started $(date '+%F %T %Z') on the Mac (owner Terminal); log $LOG" > logs/RUNNING_gnnpath_nomondo.txt
caffeinate -i .venv/bin/python -u -m pipelines.linkpred.gnnpath -- \
  --graphs G2 --drop-layers mondo_hierarchy --tasks B --splits kfold --seeds 1 --epochs 300 --hidden 64 --reverse-edges \
  --learn-embeddings --num-bases 30 --neg-pool training --positives assertions --baselines --metapaths --metapath-len 3 \
  --neg-match degree --strata --test-levels knowledge_assertion --drop-levels text_co_occurrence \
  --max-edges 1000000 --tag "$TAG" 2>&1 | tee "$LOG"
echo "FINISHED $(date '+%F %T %Z') exit ${PIPESTATUS[0]}" >> logs/RUNNING_gnnpath_nomondo.txt
