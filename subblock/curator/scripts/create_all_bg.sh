#!/bin/bash
cd "$(dirname "$0")/.."
# Run all 8 create scripts in background.

set -euo pipefail
source artifacts/envs/swegen-env/bin/activate
source scripts/load_runtime_env.sh

load_runtime_env

mkdir -p artifacts/logs/swegen-create

echo "Starting create scripts (params from config.yaml)..."

start_one() {
    local lang="$1"
    nohup bash "scripts/create_${lang}.sh" > /dev/null 2>&1 &
    echo "${lang} PID: $!"
}

for lang in py go ts js c cpp java rust; do
    start_one "$lang"
done

echo "All create scripts started. Check artifacts/logs/swegen-create/cc_*_March.txt"
