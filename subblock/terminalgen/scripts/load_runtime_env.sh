#!/bin/bash
# Hydrate terminalgen runtime env vars in priority order: shell > .env > config.yaml.
# Sourced by other scripts, then `load_runtime_env` is called.

load_runtime_env() {
    local block_root
    block_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

    # 1) Inherit relevant vars from the user's interactive shell (login profile).
    local exported=""
    exported="$(
        bash -ic '
            export -p | grep -E "declare -x (OPENAI_API_KEY|OPENAI_API_BASE|OPENAI_API_BASE_URL|MODEL_NAME|SO_API_KEY|DOCKER_HOST|PATH)="
        ' 2>/dev/null || true
    )"
    if [ -n "$exported" ]; then
        eval "$(printf '%s\n' "$exported" | sed 's/^declare -x /export /')"
    fi

    # 2) Source local .env AFTER shell inheritance so .env values win over stale shell values.
    if [[ -f "${block_root}/.env" ]]; then
        # shellcheck disable=SC1090
        source "${block_root}/.env"
    fi

    # 3) Last-resort hydration from config.yaml.runtime_info.input — only fills vars
    #    still unset after env + .env, so priority stays env > .env > config.yaml.
    local hydrate_py=""
    if [[ -x "${block_root}/artifacts/envs/terminalgen-env/bin/python" ]]; then
        hydrate_py="${block_root}/artifacts/envs/terminalgen-env/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        hydrate_py="python3"
    fi
    if [[ -n "$hydrate_py" && -f "${block_root}/config.yaml" ]]; then
        local hydrate_exports
        hydrate_exports="$(
            "$hydrate_py" - "${block_root}/config.yaml" <<'PY' 2>/dev/null || true
import os, sys, shlex
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    with open(sys.argv[1]) as f:
        cfg = yaml.safe_load(f) or {}
except Exception:
    sys.exit(0)
inp = ((cfg.get("runtime_info") or {}).get("input") or {})
llm = inp.get("llm_api") or {}
mapping = {
    "OPENAI_API_KEY":      llm.get("api_key"),
    "OPENAI_API_BASE_URL": llm.get("api_base_url"),
    "MODEL_NAME":          llm.get("gen_model"),
}
# so_api_key in config is usually the literal "human" placeholder; only use a real value.
so = inp.get("so_api_key")
if so and so != "human":
    mapping["SO_API_KEY"] = so
for k, v in mapping.items():
    if v and not os.environ.get(k):
        print(f"export {k}={shlex.quote(str(v))}")
PY
        )"
        if [[ -n "$hydrate_exports" ]]; then
            eval "$hydrate_exports"
        fi
    fi

    # terminal-lego's generator reads OPENAI_API_BASE_URL; mirror OPENAI_API_BASE if only
    # the latter is set, so either spelling works.
    if [ -z "${OPENAI_API_BASE_URL:-}" ] && [ -n "${OPENAI_API_BASE:-}" ]; then
        export OPENAI_API_BASE_URL="${OPENAI_API_BASE}"
    fi

    export MODEL_NAME="${MODEL_NAME:-deepseek-v4-flash}"
}
