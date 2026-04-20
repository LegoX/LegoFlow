#!/bin/bash

load_runtime_env() {
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

    if [ -z "${GITHUB_TOKENS:-}" ]; then
        for token_file in \
            "$PWD/gh_token.txt" \
            "$HOME/gh_token.txt" \
            "/home/ywxzml3j/ywxzml3juser23/harbor/gh_token.txt"
        do
            if [ -f "$token_file" ]; then
                GITHUB_TOKENS="$(grep -v '^[[:space:]]*$' "$token_file" | paste -sd, -)"
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
