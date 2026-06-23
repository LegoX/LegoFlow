#!/bin/bash
# Generate + Docker-validate terminal tasks for one domain.
# Usage: bash scripts/create_domain.sh <domain>
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

# shellcheck disable=SC1091
source scripts/load_runtime_env.sh
load_runtime_env

DOMAIN="${1:?usage: create_domain.sh <domain>}"

TL="repos/terminal-lego"
PY="artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

# Per-domain tuned params from config.yaml.
eval "$("$PY" scripts/read_params.py --domain "$DOMAIN" --config-yaml config.yaml)"
echo "[${DOMAIN}] GEN_WORKERS=${GEN_WORKERS} VAL_WORKERS=${VAL_WORKERS} VAL_TIMEOUT=${VAL_TIMEOUT}"

IN="artifacts/collected_questions/${DOMAIN}_so_data.json"
if [[ ! -f "$IN" ]]; then
    echo "ERROR: ${IN} missing — run scripts/scrape_so_questions.sh first" >&2
    exit 1
fi

OUT="artifacts/terminal_tasks/${DOMAIN}-tl"
CAND="${OUT}/_candidates"
LOG="artifacts/logs/terminalgen-create/tl_${DOMAIN}.txt"
mkdir -p "$CAND" "$OUT" artifacts/logs/terminalgen-create

if [[ -z "${OPENAI_API_BASE_URL:-}" ]]; then
    echo "ERROR: OPENAI_API_BASE_URL unset — terminal-lego generator needs it via --api-base" >&2
    exit 1
fi

# 1) Generate candidate tasks (terminal-lego v1.0 schema). Generator reads the
#    endpoint from --api-base, NOT from OPENAI_API_BASE.
echo "[${DOMAIN}] generating → ${CAND}"
"$PY" "${TL}/generator/task_generator.py" \
    --input "$IN" \
    --output "$CAND" \
    --workers "$GEN_WORKERS" \
    --api-base "$OPENAI_API_BASE_URL" \
    --model "$MODEL_NAME" \
    2>&1 | tee "$LOG"

# 2) Docker round-trip validation; only reward=1.0 tasks are copied to $OUT.
echo "[${DOMAIN}] validating → ${OUT}"
"$PY" "${TL}/validator/validate_tasks.py" \
    --input "$CAND" \
    --output "$OUT" \
    --workers "$VAL_WORKERS" \
    --timeout "$VAL_TIMEOUT" \
    2>&1 | tee -a "$LOG"

# 3) Refresh the authoritative manifest from the validated (copied) task dirs.
( cd "$OUT" && ls -d task_* 2>/dev/null | sort -u > verifiable_tasks.txt ) || true
N_VERIFIED="$(wc -l < "${OUT}/verifiable_tasks.txt" 2>/dev/null | tr -d ' ' || echo 0)"
echo "[${DOMAIN}] verified tasks: ${N_VERIFIED} (manifest: ${OUT}/verifiable_tasks.txt)"
