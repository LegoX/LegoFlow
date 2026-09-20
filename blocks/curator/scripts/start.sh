#!/bin/bash
# Single entry point for SWE task generation (all 8 languages, in background).
#
# On the FIRST invocation (LEGOFLOW_CURATOR_LAUNCHER_ACTIVE unset) start.sh selects the
# correct provider launcher from config.yaml -> llm_api.cc_provider_mode:
#   * openai_proxy -> scripts/start_with_openai_api.sh   (starts a local LiteLLM
#                     proxy first, so Claude Code verification cannot fail silently)
#   * native       -> scripts/start_with_anthropic_api.sh (no proxy)
# The launcher re-invokes this script with LEGOFLOW_CURATOR_LAUNCHER_ACTIVE=1, and that
# second pass runs the real generation (archive trap + create_all_bg). The guard
# makes `bash scripts/start.sh` and `/root:run curator` safe in every mode —
# neither can accidentally skip the proxy that openai_proxy providers require.
set -e
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BLOCK_DIR"

if [ -z "${LEGOFLOW_CURATOR_LAUNCHER_ACTIVE:-}" ]; then
    PY_BIN="${PY_BIN:-artifacts/envs/legoflow-curator-env/bin/python}"
    [ -x "$PY_BIN" ] || PY_BIN=python3
    MODE="$("$PY_BIN" -c "import yaml;c=yaml.safe_load(open('config.yaml'));print((c.get('runtime_info',{}).get('input',{}).get('llm_api',{}) or {}).get('cc_provider_mode',''))" 2>/dev/null || true)"
    export LEGOFLOW_CURATOR_LAUNCHER_ACTIVE=1
    case "$MODE" in
        openai_proxy) exec bash "$BLOCK_DIR/scripts/start_with_openai_api.sh" "$@" ;;
        native)       exec bash "$BLOCK_DIR/scripts/start_with_anthropic_api.sh" "$@" ;;
        *)
            echo "ERROR: config.yaml llm_api.cc_provider_mode must be 'openai_proxy' or 'native' (got '${MODE}')." >&2
            echo "  Set it in config.yaml -> runtime_info.input.llm_api.cc_provider_mode." >&2
            exit 2 ;;
    esac
fi

# --- guard set: this pass does the real work, invoked by a provider launcher ---
# Archive this run when start.sh exits (success, error, or signal).
RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
_archive_run_on_exit() {
    local rc=$?
    bash "$BLOCK_DIR/scripts/archive_run.sh" "$rc" "$RUN_STARTED_AT" || true
    exit $rc
}
trap _archive_run_on_exit EXIT

bash scripts/create_all_bg.sh

# create_all_bg.sh detaches its eight workers and returns, so this script is
# about to exit while tasks keep landing for hours. Detach an aggregator that
# publishes verified tasks into output.merged_tasks_dir as they appear — that
# directory is what tracer consumes, and nothing else ever populates it. It
# stops on its own once the workers are gone.
AGG_LOG="$BLOCK_DIR/artifacts/logs/aggregate_verified_$(date -u +%Y%m%d_%H%M%S).log"
mkdir -p "$BLOCK_DIR/artifacts/logs"
setsid nohup bash "$BLOCK_DIR/scripts/aggregate_verified_bg.sh" > "$AGG_LOG" 2>&1 < /dev/null &
echo "Verified-task aggregator PID: $! (log: ${AGG_LOG#$BLOCK_DIR/})"
