#!/usr/bin/env bash
# Graph-embedding and GNN + paths runs on an NVIDIA GPU host (Brev), inside NVIDIA's NGC PyG container.
# Which GPU ran which run (loop/ledger.tsv; consistent with docs/SUBMISSION.md): L40S for E006, the RotatE runs of 5 Nov
# (rows 'E006 ... Brev L40S', 5 Nov); H100 for E009 and E008 G3 on 6 Nov (rows 'RESULT, Brev H100'), the release run of
# 8 Nov ('D GNN+paths', `release`) and the CS-9 run of 9 Nov behind the headline number ('D (CS-9 graphs)', `cs9`).
# Layout on the host: ~/tmkg/{pipelines,config,tests,...} from `git archive`, plus ~/tmkg/outputs/{g0,g2,g3}/heterodata.pt.
#
#   bash scripts/gpu_kge_run.sh check     # GPU + versions + load G2 (no training)
#   bash scripts/gpu_kge_run.sh probe     # 1 fold, 2 epochs, dim 512 on G2: minutes per epoch + GPU memory (not a result)
#   bash scripts/gpu_kge_run.sh d256      # pre-registered dim-256 config, G0,G2,G3   (tag diag_kge_rotate_d256)
#   bash scripts/gpu_kge_run.sh gpu512    # pre-registered dim-512 config, G0,G2,G3   (tag diag_kge_rotate_gpu)
#   bash scripts/gpu_kge_run.sh e009      # E009 random-pruning control (amended 09:55): d256 config, 100 epochs for every graph, G2R then G3 (tag diag_kge_rotate_d256_e009)
#   bash scripts/gpu_kge_run.sh gnnpath_probe  # E008 on the GPU, G0, 2 epochs: smoke test only (not a result)
#   bash scripts/gpu_kge_run.sh gnnpath03 # E008 GNN + paths on the GPU, G0 and G3 only (no G2 upload needed), same tag
#   bash scripts/gpu_kge_run.sh release   # D (docs/archive/PLAN_FINAL_WEEK_2026-11-07.md section 3): GNN + paths on the FINAL G2 and G3, tag diag_gnnpath_1m_release
#   bash scripts/gpu_kge_run.sh gnnpath   # E008 GNN + paths on the GPU, G0,G3,G2 (G3 before G2 so the new number lands first) (tag diag_gnnpath_1m_gpu)
#   bash scripts/gpu_kge_run.sh release   # D (pre-registered 7 Nov 12:30, ledger row D): GNN + paths on the FINAL G3, G2; G0 reused
#                                         from G0_B_kfold@diag_gnnpath_1m_gpu.json (tag diag_gnnpath_1m_release); needs g0 (test pairs), g2, g3
# Graphs needed on the host: outputs/{g0,g2,g3,g2r}/heterodata.pt
#
# IMAGE: override with PYG_IMAGE=nvcr.io/nvidia/pyg:<tag> if the default tag is not available.
# torch_geometric is pinned to 2.8.1 (the version that wrote the heterodata.pt files on the Mac).
set -euo pipefail
IMAGE="${PYG_IMAGE:-nvcr.io/nvidia/pyg:25.05-py3}"
STEP="${1:-check}"
cd "$HOME/tmkg"
mkdir -p logs outputs/linkpred

FROZEN="--tasks B --splits kfold --seeds 1 --max-edges 1000000 --reverse-edges --learn-embeddings --num-bases 30 \
--neg-pool training --positives assertions --neg-match degree --strata --test-levels knowledge_assertion \
--drop-levels text_co_occurrence --device cuda"

case "$STEP" in
  check)  CMD="python -c \"import torch, torch_geometric, numpy, scipy, pandas; print('torch', torch.__version__, '| pyg', torch_geometric.__version__, '| cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0)); import sys; sys.path.insert(0, 'pipelines'); from linkpred.run import load_graph; d = load_graph('G2'); print('G2 loaded:', sum(int(d[n].num_nodes) for n in d.node_types), 'nodes,', len(d.edge_types), 'edge types')\"" ;;
  probe)  CMD="python -u scripts/kge_probe.py 2 512 G2 cuda" ;;
  d256)   CMD="python -u -m pipelines.linkpred.kge --kge rotate --kge-epochs 100 --kge-minutes 45 --kge-batch 16384 --kge-lr 0.01 --kge-margin 9 -- --graphs G0,G2,G3 $FROZEN --epochs 100 --hidden 256 --tag diag_kge_rotate_d256" ;;
  gpu512) CMD="python -u -m pipelines.linkpred.kge --kge rotate --kge-epochs 300 --kge-minutes 45 --kge-batch 16384 --kge-lr 0.01 --kge-margin 9 -- --graphs G0,G2,G3 $FROZEN --epochs 300 --hidden 512 --tag diag_kge_rotate_gpu" ;;
  e009)   CMD="python -u -m pipelines.linkpred.kge --kge rotate --kge-epochs 100 --kge-minutes 45 --kge-batch 16384 --kge-lr 0.01 --kge-margin 9 -- --graphs G2R,G3 $FROZEN --epochs 100 --hidden 256 --tag diag_kge_rotate_d256_e009" ;;
  gnnpath_probe) CMD="python -u -m pipelines.linkpred.gnnpath -- --graphs G0 $FROZEN --baselines --metapaths --metapath-len 3 --hidden 64 --epochs 2 --tag diag_gnnpath_gpu_probe" ;;
  gnnpath) CMD="python -u -m pipelines.linkpred.gnnpath -- --graphs G0,G3,G2 $FROZEN --baselines --metapaths --metapath-len 3 --hidden 64 --epochs 300 --tag diag_gnnpath_1m_gpu" ;;
  gnnpath03) CMD="python -u -m pipelines.linkpred.gnnpath -- --graphs G0,G3 $FROZEN --baselines --metapaths --metapath-len 3 --hidden 64 --epochs 300 --tag diag_gnnpath_1m_gpu" ;;
  release)   CMD="python -u -m pipelines.linkpred.gnnpath -- --graphs G2,G3 $FROZEN --baselines --metapaths --metapath-len 3 --hidden 64 --epochs 300 --tag diag_gnnpath_1m_release" ;;   # D: release-graph confirmation (final G2/G3; G0 reused from diag_gnnpath_1m_gpu)
  release) CMD="python -u -m pipelines.linkpred.gnnpath -- --graphs G3,G2 $FROZEN --baselines --metapaths --metapath-len 3 --hidden 64 --epochs 300 --tag diag_gnnpath_1m_release" ;;
  cs9) CMD="python -u -m pipelines.linkpred.gnnpath -- --graphs G3,G2 $FROZEN --baselines --metapaths --metapath-len 3 --hidden 64 --epochs 300 --tag diag_gnnpath_1m_cs9" ;;   # D re-run on the CS-9 graphs (9 Nov rebuild); own tag so the release result files are kept
  *) echo "unknown step $STEP"; exit 2 ;;
esac

# release: G2/G3 take their test pairs from outputs/g0/heterodata.pt (run.load_reference); without it they would score on
# their own pairs (test_from "self", a different digest) and D would not be comparable with G0. Refuse to start.
if [ "$STEP" = release ] || [ "$STEP" = cs9 ]; then for g in g0 g2 g3; do
  [ -f "outputs/$g/heterodata.pt" ] || { echo "release needs outputs/$g/heterodata.pt (g0 = the test pairs)"; exit 2; }; done; fi
LOG="logs/gpu_kge_${STEP}.log"
echo "RUNNING on the GPU host: step ${STEP} since $(date '+%Y-%m-%d %H:%M %Z'); image ${IMAGE}; log ${LOG}" > "logs/RUNNING_gpu_${STEP}.txt"
set +e
docker run --rm --gpus all --ipc=host -v "$PWD":/work -w /work -e PYTHONPATH=/work "$IMAGE" \
  bash -lc "pip install -q 'torch_geometric==2.8.1' pyyaml >/dev/null 2>&1; $CMD" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e
echo "FINISHED $(date '+%Y-%m-%d %H:%M %Z') exit ${RC}" >> "logs/RUNNING_gpu_${STEP}.txt"
exit "$RC"
