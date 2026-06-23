#!/bin/bash
# Scrape StackOverflow via terminal-lego's scraper, then bucket by domain tag_filter.
# Usage: bash scripts/scrape_so_questions.sh <round> [count]
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

# shellcheck disable=SC1091
source scripts/load_runtime_env.sh
load_runtime_env

ROUND="${1:?usage: scrape_so_questions.sh <round> [count]}"
COUNT="${2:-200}"

TL="repos/terminal-lego"
if [[ ! -f "${TL}/scraper/so_scraper.py" ]]; then
    echo "ERROR: ${TL}/scraper/so_scraper.py missing — run: git submodule update --init ${TL}" >&2
    exit 1
fi

PY="artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

RAW_DIR="artifacts/collected_questions/_raw_r${ROUND}"
mkdir -p "$RAW_DIR" artifacts/logs/terminalgen-create

echo "[scrape] round=${ROUND} count=${COUNT} (SO_API_KEY $([ -n "${SO_API_KEY:-}" ] && echo set || echo UNSET → 300/day))"
"$PY" "${TL}/scraper/so_scraper.py" \
    --round "$ROUND" \
    --output "$RAW_DIR" \
    --count "$COUNT" \
    --api-key "${SO_API_KEY:-}" \
    2>&1 | tee "artifacts/logs/terminalgen-create/scrape_r${ROUND}.txt"

RAW_JSON="${RAW_DIR}/so_data_r${ROUND}.json"
if [[ ! -f "$RAW_JSON" ]]; then
    echo "ERROR: scraper produced no ${RAW_JSON} (likely rate-limited; see log)" >&2
    exit 2
fi

echo "[bucket] splitting ${RAW_JSON} by domain tag_filter"
"$PY" scripts/bucket_questions.py \
    --raw "$RAW_JSON" \
    --config config.yaml \
    --out artifacts/collected_questions
