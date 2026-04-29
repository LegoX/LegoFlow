#!/usr/bin/env bash
# Remove trajgen runtime outputs. Does not remove repos/harbor unless --repos is passed.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

REMOVE_REPOS=0
if [[ "${1:-}" == "--repos" ]]; then
  REMOVE_REPOS=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: bash scripts/clean.sh [--repos]" >&2
  exit 2
fi

set_tree_writable() {
  local root="$1"
  [[ -d "$root" ]] || return 0
  python3 - "$root" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
for dirpath, dirnames, filenames in os.walk(root):
    for name in dirnames + filenames:
        path = os.path.join(dirpath, name)
        try:
            os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
        except FileNotFoundError:
            pass
try:
    os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)
except FileNotFoundError:
    pass
PY
}

echo "=== trajgen clean ==="
rm -rf "$BLOCK_DIR/artifacts/tasks" \
       "$BLOCK_DIR/artifacts/jobs" \
       "$BLOCK_DIR/artifacts/litellm" \
       "$BLOCK_DIR/artifacts/logs"
echo "Removed gitignored runtime artifacts."

if [[ "$REMOVE_REPOS" == "1" ]]; then
  set_tree_writable "$BLOCK_DIR/repos"
  rm -rf "$BLOCK_DIR/repos"
  echo "Removed local managed repos."
else
  echo "Kept local managed repos. Pass --repos to remove them."
fi

echo "Clean done."
