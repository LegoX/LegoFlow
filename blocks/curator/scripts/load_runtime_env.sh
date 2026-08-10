#!/bin/bash

load_runtime_env() {
    local block_root
    block_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

    # The invoking process already supplies its exported environment. Do not
    # start an interactive shell here: shell startup files can contain stale
    # credentials/endpoints and must never override explicit per-run values.
    # A block-local ignored .env remains the only intentional override layer.
    if [[ -f "${block_root}/.env" ]]; then
        source "${block_root}/.env"
    fi

    # Last-resort hydration from config.yaml.runtime_info.input.llm_api:
    # only fills exported vars still unset after the caller environment and
    # the subsequently sourced .env.
    local hydrate_py=""
    if [[ -x "${block_root}/artifacts/envs/legoflow-curator-env/bin/python" ]]; then
        hydrate_py="${block_root}/artifacts/envs/legoflow-curator-env/bin/python"
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
    # Claude Code (Anthropic /v1/messages) path. For openai_proxy this must be the
    # local LiteLLM proxy URL; for native it is the provider's Anthropic endpoint.
    "ANTHROPIC_BASE_URL":   llm.get("anthropic_base_url"),
    # Informational: surfaced so dryrun/skills can warn when the proxy is required.
    "LEGOFLOW_CURATOR_CC_PROVIDER_MODE": llm.get("cc_provider_mode"),
    "LEGOFLOW_CURATOR_CC_PROXY_PORT":    llm.get("cc_proxy_port"),
}
for k, v in mapping.items():
    if v and not os.environ.get(k):
        print(f"export {k}={shlex.quote(str(v))}")
PY
        )"
        if [[ -n "$hydrate_exports" ]]; then
            eval "$hydrate_exports"
        fi

        # PR-collection knobs from config.yaml.runtime_info.input.pr_collection.
        # Exported as LEGOFLOW_CURATOR_PR_* / COLLECT_* / LEGOFLOW_CURATOR_COLLECT_* so
        # tools/collect_prs_wo_image.py and scripts/collect_all_bg.sh pick them
        # up. Same priority rule: only fills vars still unset after env + .env.
        local pr_exports
        pr_exports="$(
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
pc = (((cfg.get("runtime_info") or {}).get("input") or {}).get("pr_collection") or {})
if not pc:
    sys.exit(0)
out = {}
langs = pc.get("languages")
if isinstance(langs, list) and langs:
    out["LEGOFLOW_CURATOR_COLLECT_LANGUAGES"] = ",".join(str(x) for x in langs)
elif isinstance(langs, str) and langs.strip():
    out["LEGOFLOW_CURATOR_COLLECT_LANGUAGES"] = langs.strip()
if pc.get("repo_num") is not None:
    out["LEGOFLOW_CURATOR_COLLECT_REPO_NUM"] = str(pc["repo_num"])
if pc.get("max_prs_per_repo") is not None:
    out["LEGOFLOW_CURATOR_COLLECT_MAX_PRS_PER_REPO"] = str(pc["max_prs_per_repo"])
if pc.get("output_dir"):
    out["LEGOFLOW_CURATOR_COLLECT_OUTPUT_DIR"] = str(pc["output_dir"])
if pc.get("token_limit") is not None:
    out["COLLECT_TOKEN_LIMIT"] = str(pc["token_limit"])
filt = pc.get("filters") or {}
fmap = {
    "min_stars":               "LEGOFLOW_CURATOR_PR_MIN_STARS",
    "min_merged_prs":          "LEGOFLOW_CURATOR_PR_MIN_MERGED_PRS",
    "min_language_percentage": "LEGOFLOW_CURATOR_PR_MIN_LANGUAGE_PERCENTAGE",
    "max_days_since_push":     "LEGOFLOW_CURATOR_PR_MAX_DAYS_SINCE_PUSH",
    "min_issue_body_length":   "LEGOFLOW_CURATOR_PR_MIN_ISSUE_BODY_LENGTH",
    "min_files_changed":       "LEGOFLOW_CURATOR_PR_MIN_FILES_CHANGED",
    "max_files_changed":       "LEGOFLOW_CURATOR_PR_MAX_FILES_CHANGED",
    "max_lines_changed":       "LEGOFLOW_CURATOR_PR_MAX_LINES_CHANGED",
}
for k, env_name in fmap.items():
    v = filt.get(k)
    if v is not None:
        out[env_name] = str(v)
for k, v in out.items():
    if v and not os.environ.get(k):
        print(f"export {k}={shlex.quote(str(v))}")
PY
        )"
        if [[ -n "$pr_exports" ]]; then
            eval "$pr_exports"
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
