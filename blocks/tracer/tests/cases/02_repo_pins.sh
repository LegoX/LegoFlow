#!/usr/bin/env bash
# CI test 02: every managed repo sits at the commit config.yaml pins.
#
# The pin is the assertion. Around it:
#   - the checkout must name the same repo as config (identity, not transport:
#     ssh://git@host/o/r.git, git@host:o/r.git and https://host/o/r are one
#     repo, and a local clone may legitimately use a different transport)
#   - the worktree must be clean, or HEAD does not describe what is on disk
#   - when the path is a tracked submodule, the superproject gitlink must agree
#     with config too, so a bumped pin cannot be half-applied

set -euo pipefail
BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_ROOT="$(git -C "$BLOCK_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
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

# https://host/owner/repo.git | ssh://git@host/owner/repo.git | git@host:owner/repo
#   -> host/owner/repo
norm_url() {
  local u="${1%.git}"
  u="${u#ssh://}"; u="${u#git+ssh://}"; u="${u#https://}"; u="${u#http://}"
  u="${u#*@}"
  echo "${u/:/\/}"
}

check_repo() {
  local name="$1" url commit path abs origin head gitlink
  url="$(cfg "meta_info.repositories.$name.url")"
  commit="$(cfg "meta_info.repositories.$name.commit")"
  path="$(cfg "meta_info.repositories.$name.path")"
  abs="$BLOCK_DIR/$path"

  [[ -e "$abs/.git" ]] || {
    echo "FAIL: $path/.git missing — run scripts/update_repos.sh --repo $name"; return 1; }

  head="$(git -C "$abs" rev-parse HEAD 2>/dev/null || true)"
  if [[ "$head" != "$commit" ]]; then
    echo "FAIL: $path HEAD=$head does not match config commit=$commit"; return 1
  fi

  origin="$(git -C "$abs" remote get-url origin 2>/dev/null || true)"
  if [[ "$(norm_url "$origin")" != "$(norm_url "$url")" ]]; then
    echo "FAIL: $path origin=$origin is a different repo than config url=$url"; return 1
  fi

  if [[ -n "$(git -C "$abs" status --porcelain 2>/dev/null || true)" ]]; then
    echo "FAIL: $path has local modifications — HEAD no longer describes the worktree"
    return 1
  fi

  if [[ -n "$REPO_ROOT" ]]; then
    gitlink="$(git -C "$REPO_ROOT" ls-files -s "${abs#"$REPO_ROOT"/}" 2>/dev/null | awk '$1=="160000"{print $2}')"
    if [[ -n "$gitlink" && "$gitlink" != "$commit" ]]; then
      echo "FAIL: $path superproject gitlink=$gitlink does not match config commit=$commit"
      return 1
    fi
  fi

  echo "INFO: $name pinned at $commit ($path)"
}

fail=0
for r in harbor swe_data_process; do
  check_repo "$r" || fail=$((fail+1))
done

if [[ "$fail" -gt 0 ]]; then
  echo "FAIL: $fail repo check(s) failed"
  exit 1
fi
echo "PASS: repos at pinned commits"
