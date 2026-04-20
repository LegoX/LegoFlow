#!/bin/bash
cd "$(dirname "$0")/.."
# Run all 8 create scripts in background.

set -euo pipefail
source swegen-env2/bin/activate
source scripts/load_runtime_env.sh

load_runtime_env

mkdir -p logs/swegen-create

echo "Starting create scripts..."

start_one() {
    local lang="$1"
    nohup bash "scripts/create_${lang}.sh" > /dev/null 2>&1 &
    echo "${lang} PID: $!"
}

for lang in py go ts js c cpp java rust; do
    start_one "$lang"
done

echo "All create scripts started. Check logs/swegen-create/cc_*_March.txt"
