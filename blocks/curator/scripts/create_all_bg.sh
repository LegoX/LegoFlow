#!/bin/bash
cd "$(dirname "$0")/.."
# Run all 8 create scripts in background.

set -euo pipefail
source artifacts/envs/swegen-env/bin/activate
source scripts/load_runtime_env.sh

load_runtime_env

mkdir -p artifacts/logs/swegen-create

# Install the swegen package once, up front, so the eight parallel language
# scripts don't race on concurrent `pip install -e` into the shared venv.
python -c 'import swegen' 2>/dev/null || pip install -e repos/swegen/

echo "Starting create scripts (params from config.yaml)..."

# Only languages with `enabled: true`. Starting a disabled language would burn
# LLM and Docker budget on work the operator switched off, and the config would
# be describing something other than what runs.
ENABLED="$(python scripts/read_params.py --list-enabled --config-yaml config.yaml)"

start_one() {
    local lang="$1"
    nohup bash "scripts/create_${lang}.sh" > /dev/null 2>&1 &
    echo "${lang} PID: $!"
}

started=0
skipped=""
for lang in py go ts js c cpp java rust; do
    if grep -qx "$lang" <<<"$ENABLED"; then
        start_one "$lang"
        started=$((started+1))
    else
        skipped="${skipped} ${lang}"
    fi
done

[[ -n "$skipped" ]] && echo "Skipped (enabled: false):${skipped}"

if [[ "$started" -eq 0 ]]; then
    echo "ERROR: every language is disabled in config.yaml — nothing to run." >&2
    echo "  Set runtime_info.input.languages.<lang>.enabled: true for at least one." >&2
    exit 1
fi

echo "${started} create script(s) started. Check artifacts/logs/swegen-create/cc_*_<timestamp>.txt"
