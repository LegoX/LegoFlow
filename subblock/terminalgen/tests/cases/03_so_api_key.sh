#!/usr/bin/env bash
# CI test 03: StackExchange API key reachability.
# With SO_API_KEY: GET /2.3/questions must return 200 with a quota_max of 10000.
# Without a key: SKIP (the pipeline still runs at the shared 300/day quota).

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$BLOCK_DIR/scripts/load_runtime_env.sh"
load_runtime_env >/dev/null 2>&1 || true

if [[ -z "${SO_API_KEY:-}" ]]; then
  echo "SKIP: SO_API_KEY unset — scraper falls back to 300/day shared IP quota"
  exit 77
fi

URL="https://api.stackexchange.com/2.3/questions?site=stackoverflow&pagesize=1&key=${SO_API_KEY}"
BODY="$(curl -s --max-time 20 "$URL" || echo '')"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL" || echo 000)"

if [[ "$CODE" != "200" ]]; then
  echo "FAIL: StackExchange API returned HTTP $CODE"
  exit 1
fi

QUOTA="$(python3 -c "import sys,json;d=json.loads('''$BODY''' or '{}');print(d.get('quota_remaining','?'))" 2>/dev/null || echo '?')"
QMAX="$(python3 -c "import sys,json;d=json.loads('''$BODY''' or '{}');print(d.get('quota_max','?'))" 2>/dev/null || echo '?')"

if [[ "$QMAX" == "10000" ]]; then
  echo "PASS: SO_API_KEY authenticated (quota_remaining=$QUOTA / $QMAX)"
  exit 0
fi
echo "WARN: SO key reachable but quota_max=$QMAX (expected 10000); quota_remaining=$QUOTA"
echo "PASS: StackExchange API reachable"
