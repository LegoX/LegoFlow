#!/usr/bin/env bash
# Root case 15: keep Curator pinned to the latest commit on its upstream main branch.

set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SUBMODULE_PATH="blocks/curator/repos/legoflow-curator"
SUBMODULE_URL="$(git -C "$ROOT_DIR" config -f .gitmodules --get "submodule.$SUBMODULE_PATH.url")"
[[ -n "$SUBMODULE_URL" ]] || { echo "FAIL: missing Curator submodule URL"; exit 1; }

GITLINK_COMMIT="$(git -C "$ROOT_DIR" ls-tree HEAD -- "$SUBMODULE_PATH" | awk '{print $3}')"
[[ "$GITLINK_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "FAIL: Curator gitlink is missing or invalid: $GITLINK_COMMIT"
  exit 1
}

CONFIG_COMMIT="$(python3 - "$ROOT_DIR/blocks/curator/config.yaml" <<'PY'
import re
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"commit_id:\s*([0-9a-f]{40})", text)
if not match:
    raise SystemExit(1)
print(match.group(1))
PY
)" || {
  echo "FAIL: Curator config has no 40-character commit_id"
  exit 1
}

if [[ "$GITLINK_COMMIT" != "$CONFIG_COMMIT" ]]; then
  echo "FAIL: Curator gitlink ($GITLINK_COMMIT) != config commit_id ($CONFIG_COMMIT)"
  exit 1
fi

REMOTE_ARGS=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  REMOTE_ARGS=(-c "http.extraheader=Authorization: Bearer ${GITHUB_TOKEN}")
fi
set +e
LATEST_COMMIT="$(git "${REMOTE_ARGS[@]}" ls-remote "$SUBMODULE_URL" refs/heads/main 2>/dev/null | awk 'NR == 1 {print $1}')"
REMOTE_RC=$?
set -e
if [[ "$REMOTE_RC" -ne 0 || ! "$LATEST_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "SKIP: unable to query Curator upstream main ($SUBMODULE_URL)"
  exit 77
fi

if [[ "$GITLINK_COMMIT" != "$LATEST_COMMIT" ]]; then
  echo "FAIL: Curator is not pinned to latest upstream main"
  echo "      pinned: $GITLINK_COMMIT"
  echo "      latest: $LATEST_COMMIT"
  exit 1
fi

echo "PASS: Curator gitlink/config point to latest upstream main ($LATEST_COMMIT)"
