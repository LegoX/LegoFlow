#!/bin/bash
# Start the terminalgen pipeline: scrape (if pool low) → create all enabled domains → archive.
set -e
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Archive this run when start.sh exits (success, error, or signal).
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
_archive_run_on_exit() {
    local rc=$?
    bash "$BLOCK_DIR/scripts/archive_run.sh" "$rc" "$RUN_STARTED_AT" || true
    exit $rc
}
trap _archive_run_on_exit EXIT

cd "$BLOCK_DIR"

# Scrape a fresh round if no question buckets exist yet. Round number is derived
# from existing raw scrape dirs to avoid clobbering.
if ! ls artifacts/collected_questions/*_so_data.json >/dev/null 2>&1; then
    NEXT_ROUND=1
    while [[ -d "artifacts/collected_questions/_raw_r${NEXT_ROUND}" ]]; do
        NEXT_ROUND=$((NEXT_ROUND + 1))
    done
    echo "[start] no question buckets found; scraping round ${NEXT_ROUND}"
    bash scripts/scrape_so_questions.sh "$NEXT_ROUND" 200
fi

bash scripts/create_all_bg.sh
