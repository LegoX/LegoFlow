#!/bin/bash

load_runtime_env() {
    local block_root
    block_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

    local exported=""
    local exported_global=""
    exported="$(
        bash -ic '
            export -p | grep -E "declare -x (GITHUB_TOKEN|GITHUB_TOKENS|OPENAI_API_KEY|OPENAI_API_BASE|OPENAI_API_BASE_URL|ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|CLAUDE_CODE_OAUTH_TOKEN|HF_HOME|HF_TOKEN|WANDB_API_KEY|SWEBENCH_API_KEY|PATH|LD_LIBRARY_PATH|CUDA_HOME)="
        ' 2>/dev/null || true
    )"
    if [ -n "$exported" ]; then
        # Inside a function, `declare -x` becomes local; rewrite to global `export`.
        exported_global="$(printf '%s\n' "$exported" | sed 's/^declare -x /export /')"
        eval "$exported_global"
    fi

    # Source local .env AFTER interactive shell inheritance so .env values win over
    # stale shell values (e.g. old ANTHROPIC_BASE_URL from a previous session).
    if [[ -f "${block_root}/.env" ]]; then
        source "${block_root}/.env"
    fi

    # Last-resort hydration from config.yaml.runtime_info.input.llm_api:
    # only fills vars still unset after env + .env, so priority stays env > .env > config.yaml.
    local hydrate_py=""
    if [[ -x "${block_root}/artifacts/envs/swegen-env/bin/python" ]]; then
        hydrate_py="${block_root}/artifacts/envs/swegen-env/bin/python"
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
llm = (((cfg.get("runtime_info") or {}).get("input") or {}).get("llm_api") or {})
mapping = {
    "OPENAI_API_KEY":       llm.get("api_key"),
    "OPENAI_API_BASE_URL":  llm.get("api_base_url"),
    "OPENAI_MODEL":         llm.get("pr_model"),
    "ANTHROPIC_MODEL":      llm.get("task_model"),
    # Also mirror to ANTHROPIC_AUTH_TOKEN so the Claude CLI sends
    # `Authorization: Bearer <key>` in addition to its default `x-api-key`.
    # Required by third-party endpoints like llm10 that reject x-api-key but accept Bearer.
    "ANTHROPIC_AUTH_TOKEN": llm.get("api_key"),
}
for k, v in mapping.items():
    if v and not os.environ.get(k):
        print(f"export {k}={shlex.quote(str(v))}")
PY
        )"
        if [[ -n "$hydrate_exports" ]]; then
            eval "$hydrate_exports"
        fi
    fi

    if [ -z "${GITHUB_TOKENS:-}" ]; then
        for token_file in \
            "$PWD/gh_token.txt" \
            "$HOME/gh_token.txt" \
            "$HOME/harbor/gh_token.txt"
        do
            if [ -f "$token_file" ]; then
                GITHUB_TOKENS="$(grep -vE '^[[:space:]]*(#|$)' "$token_file" | paste -sd, -)"
                export GITHUB_TOKENS
                break
            fi
        done
    fi

    if [ -z "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_TOKENS:-}" ]; then
        GITHUB_TOKEN="${GITHUB_TOKENS%%,*}"
        export GITHUB_TOKEN
    fi

    # Restore ~/.claude.json from backup if missing (Claude Code may move it).
    local claude_cfg="${HOME}/.claude.json"
    if [[ ! -f "${claude_cfg}" ]]; then
        local latest_backup
        latest_backup="$(ls -t "${HOME}"/.claude/backups/.claude.json.backup.* 2>/dev/null | head -n 1 || true)"
        if [[ -n "${latest_backup}" && -f "${latest_backup}" ]]; then
            cp "${latest_backup}" "${claude_cfg}"
            echo "restored ${claude_cfg} from ${latest_backup}"
        else
            echo "warn: ${claude_cfg} missing and no backup found; Claude Code may fail"
        fi
    fi
}
