#!/bin/bash
cd "$(dirname "$0")/.."

# Launch the in-repo PR collector as a self-contained background job.
# Default output is LegoFlow Curator/collected_prs.
#
# Example cron:
#   0 */6 * * * cd /path/to/LegoFlow Curator && bash scripts/collect_all_bg.sh >> logs/collect_scheduler.log 2>&1

set -euo pipefail

source artifacts/envs/legoflow-curator-env/bin/activate
source scripts/load_runtime_env.sh

load_runtime_env

pip install -e repos/legoflow-curator/ >/dev/null

mkdir -p artifacts/logs collected_prs

# Values come from config.yaml -> runtime_info.input.pr_collection via
# load_runtime_env.sh (exported as LEGOFLOW_CURATOR_COLLECT_*). Direct env vars still win.
REPO_NUM="${REPO_NUM:-${LEGOFLOW_CURATOR_COLLECT_REPO_NUM:-5000}}"
MAX_PRS_PER_REPO="${MAX_PRS_PER_REPO:-${LEGOFLOW_CURATOR_COLLECT_MAX_PRS_PER_REPO:-100}}"
OUTPUT_DIR="${OUTPUT_DIR:-${LEGOFLOW_CURATOR_COLLECT_OUTPUT_DIR:-$(pwd)/artifacts/collected_prs}}"
LANGUAGES="${LANGUAGES:-${LEGOFLOW_CURATOR_COLLECT_LANGUAGES:-}}"
DISABLE_PROGRESS_BAR="${DISABLE_PROGRESS_BAR:---disable_progress_bar}"
STAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="artifacts/logs/collect_all_${STAMP}.log"

LANG_ARG=()
if [[ -n "${LANGUAGES}" ]]; then
  LANG_ARG=(--languages "${LANGUAGES}")
fi

nohup python3 repos/legoflow-curator/tools/collect_prs_wo_image.py \
  --repo_num "${REPO_NUM}" \
  --max_prs_per_repo "${MAX_PRS_PER_REPO}" \
  --output_dir "${OUTPUT_DIR}" \
  "${LANG_ARG[@]}" \
  ${DISABLE_PROGRESS_BAR} \
  > "${LOG_FILE}" 2>&1 < /dev/null &

echo "collect PID: $!"
echo "log: ${LOG_FILE}"
