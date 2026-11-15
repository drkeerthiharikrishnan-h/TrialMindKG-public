#!/usr/bin/env bash
# Monthly change monitor as a container / EC2 task (AWS Batch or ECS, scheduled by EventBridge - see aws/README.md).
# Needs: repo checkout with .venv, AACT flat files unzipped at $AACT_FLATFILES, aws/config.env, git push rights.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate
python pipelines/monitor.py check | tee outputs/monitor_check.txt
python pipelines/aact.py freeze
python pipelines/seed_kg.py
python pipelines/monitor.py diff
python -m pytest -q
bash aws/upload_s3.sh
echo "Review outputs/change_report.md, re-run the listed questions, then commit outputs/ + data/snapshots*/"
