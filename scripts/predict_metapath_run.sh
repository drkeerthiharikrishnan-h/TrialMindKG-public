#!/usr/bin/env bash
# Meta-path link predictions (gene -> disease) on the whole pruned graph G3, for the app's "predicted links" panel.
# Runs from the owner's Terminal (CC-28: top 10 per gene and per disease, curated diseases, explained by CPUs-2 workers;
# one progress line per chunk of 2,000 pairs with ms/pair and ETA, so the log is never silent):
#     cd ~/projects/TrialMindKG && bash scripts/predict_metapath_run.sh
# Output: outputs/linkpred/predictions_metapath.tsv.gz (+ _settings.json). Then Claude Science rebuilds the card index.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD:$PWD/pipelines"
LOG=logs/predict_metapath_g3.log
echo "predict metapath G3 task B started $(date '+%F %T %Z') (owner Terminal); log $LOG" > logs/RUNNING_predict_metapath.txt
caffeinate -i .venv/bin/python -u pipelines/linkpred/predict.py --graph G3 --model metapath --tasks B \
  --positives assertions --drop-levels text_co_occurrence --top-subject 10 --top-object 10 --objects curated \
  --explain-chunk 2000 --max-len 3 --max-shapes 200 2>&1 | tee "$LOG"
echo "FINISHED $(date '+%F %T %Z') exit ${PIPESTATUS[0]}" >> logs/RUNNING_predict_metapath.txt
