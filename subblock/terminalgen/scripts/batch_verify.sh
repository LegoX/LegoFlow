#!/bin/bash
# Batch-verify: drive create_domain.sh per domain until each reaches a target
# number of verified tasks (or a per-domain candidate cap is hit). Cost-controlled
# — generation happens in fixed-size chunks and stops as soon as the target is met.
#
# Usage:
#   bash scripts/batch_verify.sh <target_per_domain> [domain ...]
#     target_per_domain   desired verified tasks per domain (e.g. 5)
#     domain ...          domains to run; default: all enabled domains in config.yaml
#
# Env knobs:
#   CHUNK       candidates generated per round (default 6)
#   CAND_CAP    max candidates to generate per domain before giving up (default 24)
#   SCRAPE_COUNT  questions to scrape if buckets are missing (default 300)
#
# Verified tasks land in artifacts/terminal_tasks/<domain>-tl/ with a
# verifiable_tasks.txt manifest. Run scripts/extract_verified_tasks.py afterwards
# to materialize the harbor-1.1 merged export.
set -euo pipefail

cd "$(dirname "$0")/.." || { echo "ERROR: cannot cd to block root"; exit 1; }

# shellcheck disable=SC1091
source scripts/load_runtime_env.sh
load_runtime_env

TARGET="${1:?usage: batch_verify.sh <target_per_domain> [domain ...]}"
shift || true

PY="artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

CHUNK="${CHUNK:-6}"
CAND_CAP="${CAND_CAP:-24}"
SCRAPE_COUNT="${SCRAPE_COUNT:-300}"

# Resolve domain list (args, else all enabled domains from config.yaml).
if [[ "$#" -gt 0 ]]; then
    DOMAINS="$*"
else
    DOMAINS="$("$PY" - <<'PY'
import yaml
cfg = yaml.safe_load(open("config.yaml")) or {}
doms = cfg.get("runtime_info", {}).get("input", {}).get("domains", {})
print(" ".join(d for d, v in doms.items() if v.get("enabled", True)))
PY
)"
fi

bucket_size() {
    "$PY" - "$1" <<'PY' 2>/dev/null || echo 0
import json, sys
try:
    print(len(json.load(open(sys.argv[1])).get("questions", [])))
except Exception:
    print(0)
PY
}

verified_count() {
    local m="artifacts/terminal_tasks/$1-tl/verifiable_tasks.txt"
    [[ -f "$m" ]] && grep -c . "$m" 2>/dev/null || echo 0
}

# Ensure question buckets exist (scrape one round if none present).
if ! ls artifacts/collected_questions/*_so_data.json >/dev/null 2>&1; then
    round=1
    while [[ -d "artifacts/collected_questions/_raw_r${round}" ]]; do round=$((round+1)); done
    echo "[batch] no question buckets; scraping round ${round} (count=${SCRAPE_COUNT})"
    bash scripts/scrape_so_questions.sh "$round" "$SCRAPE_COUNT"
fi

echo "[batch] target=${TARGET}/domain  chunk=${CHUNK}  cap=${CAND_CAP}"
echo "[batch] domains: ${DOMAINS}"
declare -A RESULT
for domain in $DOMAINS; do
    bucket="artifacts/collected_questions/${domain}_so_data.json"
    if [[ ! -f "$bucket" ]]; then
        echo "[batch] ${domain}: no bucket, skipping"
        RESULT[$domain]="0 (no bucket)"
        continue
    fi
    bsize="$(bucket_size "$bucket")"
    start=0
    while :; do
        have="$(verified_count "$domain")"
        if [[ "$have" -ge "$TARGET" ]]; then
            echo "[batch] ${domain}: reached ${have}/${TARGET}"
            break
        fi
        if [[ "$start" -ge "$bsize" ]]; then
            echo "[batch] ${domain}: bucket exhausted (${bsize} questions), have ${have}/${TARGET}"
            break
        fi
        if [[ "$start" -ge "$CAND_CAP" ]]; then
            echo "[batch] ${domain}: candidate cap ${CAND_CAP} hit, have ${have}/${TARGET}"
            break
        fi
        echo "[batch] ${domain}: have ${have}/${TARGET}; generating chunk start=${start} limit=${CHUNK}"
        bash scripts/create_domain.sh "$domain" "$CHUNK" "$start" || echo "[batch] ${domain}: chunk failed (continuing)"
        start=$((start + CHUNK))
    done
    RESULT[$domain]="$(verified_count "$domain")/${TARGET}"
done

echo "============================================================"
echo "[batch] summary (verified/target):"
for domain in $DOMAINS; do
    printf '  %-24s %s\n' "$domain" "${RESULT[$domain]:-0}"
done
echo "============================================================"
echo "[batch] next: python scripts/extract_verified_tasks.py   # → harbor 1.1 merged export"
