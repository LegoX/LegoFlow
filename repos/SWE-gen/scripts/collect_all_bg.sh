#!/bin/bash
cd "$(dirname "$0")/.."

# Launch the in-repo PR collector as a self-contained background job.
# Default output is SWE-gen/collected_prs.
#
# Example cron:
#   0 */6 * * * cd /home/ywxzml3j/ywxzml3juser23/SWE-gen && bash scripts/collect_all_bg.sh >> logs/collect_scheduler.log 2>&1

set -euo pipefail

source swegen-env2/bin/activate
source scripts/load_runtime_env.sh

load_runtime_env

uv pip install -e . >/dev/null

mkdir -p logs collected_prs

REPO_NUM="${REPO_NUM:-5000}"
MAX_PRS_PER_REPO="${MAX_PRS_PER_REPO:-100}"
OUTPUT_DIR="${OUTPUT_DIR:-/home/ywxzml3j/ywxzml3juser23/SWE-gen/collected_prs}"
DISABLE_PROGRESS_BAR="${DISABLE_PROGRESS_BAR:---disable_progress_bar}"
STAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="logs/collect_all_${STAMP}.log"

nohup python3 tools/collect_prs_wo_image.py \
  --repo_num "${REPO_NUM}" \
  --max_prs_per_repo "${MAX_PRS_PER_REPO}" \
  --output_dir "${OUTPUT_DIR}" \
  ${DISABLE_PROGRESS_BAR} \
  > "${LOG_FILE}" 2>&1 < /dev/null &

echo "collect PID: $!"
echo "log: ${LOG_FILE}"
