#!/usr/bin/env bash
# CI test 03: every GitHub token in GITHUB_TOKENS / gh_token.txt returns 200
# on GET /rate_limit.

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

if [[ -z "${GITHUB_TOKENS:-}" ]]; then
  echo "FAIL: GITHUB_TOKENS is empty (no env var and no usable gh_token.txt)"
  exit 1
fi

IFS=',' read -r -a tokens <<<"$GITHUB_TOKENS"
bad=0
ok=0
index=0
for tok in "${tokens[@]}"; do
  index=$((index+1))
  tok="${tok// /}"
  [[ -z "$tok" ]] && continue
  code="$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: token $tok" \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: legoflow-curator-ci/1.0" \
    --max-time 15 \
    https://api.github.com/rate_limit || echo 000)"
  if [[ "$code" == "200" ]]; then
    ok=$((ok+1))
  else
    bad=$((bad+1))
    echo "FAIL: token #$index returned HTTP $code"
  fi
done

if [[ "$bad" -gt 0 ]]; then
  echo "FAIL: $bad of $((ok+bad)) tokens unhealthy"
  exit 1
fi
echo "PASS: $ok GitHub token(s) authenticated"
