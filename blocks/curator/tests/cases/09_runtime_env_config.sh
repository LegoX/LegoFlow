#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BLOCK="$TMP/blocks/curator"
mkdir -p "$BLOCK/scripts" "$BLOCK/fixtures" "$TMP/scripts" "$TMP/home"
cp "$ROOT_DIR/blocks/curator/scripts/load_runtime_env.sh" "$BLOCK/scripts/"
cp "$ROOT_DIR/scripts/shared_credentials.sh" "$TMP/scripts/"
touch "$TMP/home/.claude.json"
printf 'configured-a\nconfigured-b\n' >"$BLOCK/fixtures/tokens.txt"
printf 'fallback-token\n' >"$BLOCK/gh_token.txt"

cat >"$TMP/config.yaml" <<'YAML'
runtime_info:
  input:
    cloudflare: {account_id: test-account, api_token: test-token}
    docker:
      registry: ""
      username: test-user
      password: test-password
      mirror: ""
      host: unix:///tmp/legoflow-test-docker.sock
YAML

cat >"$BLOCK/config.yaml" <<'YAML'
runtime_info:
  input:
    llm_api:
      api_key: test-llm-key
      api_base_url: http://127.0.0.1:8000/v1
      pr_model: test-model
      task_model: test-task-model
      cc_provider_mode: native
      anthropic_base_url: http://127.0.0.1:8000
      cc_proxy_port: 4010
    github_token: fixtures/tokens.txt
    pr_collection: {}
YAML

unset GITHUB_TOKENS GITHUB_TOKEN COLLECT_GITHUB_TOKEN_FILE
unset DOCKER_HOST DOCKER_USERNAME DOCKER_PASSWORD
unset OPENAI_API_KEY OPENAI_API_BASE_URL OPENAI_MODEL
HOME="$TMP/home"
export HOME

cd "$BLOCK"
source scripts/load_runtime_env.sh
load_runtime_env >/dev/null

[[ "$GITHUB_TOKENS" == "configured-a,configured-b" ]]
[[ "$GITHUB_TOKEN" == "configured-a" ]]
[[ "$COLLECT_GITHUB_TOKEN_FILE" == "$BLOCK/fixtures/tokens.txt" ]]
[[ "$DOCKER_HOST" == "unix:///tmp/legoflow-test-docker.sock" ]]
[[ "$DOCKER_USERNAME" == "test-user" ]]
[[ "$OPENAI_MODEL" == "test-model" ]]

echo "PASS: runtime config hydrates token file and root Docker settings"
