#!/usr/bin/env bash
# CI test 02: repos/ pins.
# For each managed repo (LLaMA-Factory, swe_data_process):
#   - assert repos/<path>/.git exists (submodule initialised / checked out)
#   - if commit is non-null, assert git HEAD matches the pin
# swe_data_process is additionally asserted to be an installable src-layout
# package (the shape dryrun.sh and install_env.sh depend on).

set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f: d = yaml.safe_load(f) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

fail=0

check_repo() {
  local key="$1"
  local path_rel commit repo_path head
  path_rel="$(cfg "meta_info.repositories.${key}.path")"
  commit="$(cfg "meta_info.repositories.${key}.commit")"
  repo_path="$BLOCK_DIR/$path_rel"

  if [[ ! -e "$repo_path/.git" ]]; then
    echo "FAIL: $key: $path_rel/.git missing — run /trainer:setup (git submodule update --init)"
    fail=$((fail+1)); return
  fi

  if [[ -n "$commit" && "$commit" != "null" ]]; then
    head="$(git -C "$repo_path" rev-parse HEAD 2>/dev/null || true)"
    if [[ "$head" != "$commit" ]]; then
      echo "FAIL: $key: HEAD=$head does not match config commit=$commit"
      fail=$((fail+1)); return
    fi
    echo "INFO: $key pinned at $commit ($path_rel)"
  else
    echo "INFO: $key commit is null (treated as latest); skipping pin check"
  fi
}

check_repo llama_factory
check_repo swe_data_process

# swe_data_process must be an installable src-layout package.
SDP_REL="$(cfg meta_info.repositories.swe_data_process.path)"
SDP_PATH="$BLOCK_DIR/$SDP_REL"
if [[ ! -f "$SDP_PATH/pyproject.toml" || ! -d "$SDP_PATH/src/swe_data_process" ]]; then
  echo "FAIL: swe_data_process is not a src-layout package (need pyproject.toml + src/swe_data_process/)"
  fail=$((fail+1))
fi

if [[ "$fail" -gt 0 ]]; then
  echo "FAIL: $fail repo check(s) failed"
  exit 1
fi
echo "PASS: repos at pinned commits"
