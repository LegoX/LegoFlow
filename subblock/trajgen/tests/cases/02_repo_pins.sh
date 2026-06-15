#!/usr/bin/env bash
# CI test 02: repos/harbor and repos/swe_data_process at pinned commits, clean.

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() { python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

check_repo() {
  local name="$1" url commit path abs
  url="$(cfg "meta_info.repositories.$name.url")"
  commit="$(cfg "meta_info.repositories.$name.commit")"
  path="$(cfg "meta_info.repositories.$name.path")"
  abs="$BLOCK_DIR/$path"

  [[ -e "$abs/.git" ]] || { echo "FAIL: $path/.git missing — run scripts/update_repos.sh --repo $name"; return 1; }

  local origin head
  origin="$(git -C "$abs" remote get-url origin 2>/dev/null || true)"
  if [[ "$origin" != "$url" ]]; then
    echo "FAIL: $path origin=$origin does not match config url=$url"; return 1
  fi
  head="$(git -C "$abs" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$head" != "$commit" ]]; then
    echo "FAIL: $path HEAD=$head does not match config commit=$commit"; return 1
  fi
  if [[ -n "$(git -C "$abs" status --porcelain 2>/dev/null || true)" ]]; then
    echo "FAIL: $path has local modifications"; return 1
  fi
  echo "INFO: $name pinned at $commit"
}

ok=0
for r in harbor swe_data_process; do
  if check_repo "$r"; then ok=$((ok+1)); fi
done

if [[ "$ok" == 2 ]]; then
  echo "PASS: repos pinned and clean"
else
  exit 1
fi
