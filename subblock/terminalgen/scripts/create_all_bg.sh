#!/bin/bash
# Launch create_domain.sh for every enabled domain in the background.
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source scripts/load_runtime_env.sh
load_runtime_env

PY="artifacts/envs/terminalgen-env/bin/python"
[[ -x "$PY" ]] || PY="python3"

mkdir -p artifacts/logs/terminalgen-create

# Enabled domains from config.yaml.
DOMAINS="$("$PY" - <<'PY'
import yaml
cfg = yaml.safe_load(open("config.yaml")) or {}
doms = cfg.get("runtime_info", {}).get("input", {}).get("domains", {})
print(" ".join(d for d, v in doms.items() if v.get("enabled", True)))
PY
)"

echo "Starting create scripts for domains: ${DOMAINS}"

for domain in $DOMAINS; do
    # Skip domains with no scraped questions yet.
    if [[ ! -f "artifacts/collected_questions/${domain}_so_data.json" ]]; then
        echo "  skip ${domain} (no question bucket)"
        continue
    fi
    nohup bash "scripts/create_domain.sh" "$domain" \
        > "artifacts/logs/terminalgen-create/bg_${domain}.txt" 2>&1 &
    echo "  ${domain} PID: $!"
done

echo "All create scripts started. Check artifacts/logs/terminalgen-create/tl_*.txt"
