#!/usr/bin/env bash
# Clone or update local-only repos used by tracer.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/update_repos.sh
  bash scripts/update_repos.sh [--repo <name|all>] [--ref <branch|tag|commit>]

Updates every entry under meta_info.repositories in config.yaml (default --repo
all). For each selected repo, if repositories.<name>.commit is set, that exact
commit is checked out after fetching the configured branch/ref. The worktree
must be clean before an existing checkout is updated.

Examples:
  bash scripts/update_repos.sh --repo harbor
  bash scripts/update_repos.sh --repo swe_data_process --ref main
EOF
}

REPO_FILTER="all"
REF_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || { echo "ERROR: --repo requires a value" >&2; exit 2; }
      REPO_FILTER="$2"
      shift 2
      ;;
    --ref)
      [[ $# -ge 2 ]] || { echo "ERROR: --ref requires a value" >&2; exit 2; }
      REF_OVERRIDE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required to read config.yaml", file=sys.stderr)
    sys.exit(2)

config_path, dotted_key = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

value = data
for part in dotted_key.split("."):
    if not isinstance(value, dict):
        value = None
        break
    value = value.get(part)

if value is None:
    print("")
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

list_repos() {
  python3 - "$CONFIG" <<'PY'
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required to read config.yaml", file=sys.stderr)
    sys.exit(2)

with open(sys.argv[1], encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

repos = ((data.get("meta_info") or {}).get("repositories") or {})
if not isinstance(repos, dict):
    print("ERROR: meta_info.repositories must be a mapping", file=sys.stderr)
    sys.exit(2)
for name in repos:
    print(name)
PY
}

abspath() {
  local p="$1"
  if [[ "$p" = /* ]]; then
    echo "$p"
  else
    echo "$BLOCK_DIR/$p"
  fi
}

set_tree_writable() {
  local root="$1"
  [[ -d "$root" ]] || return 0
  python3 - "$root" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
for path in [root]:
    try:
        os.chmod(path, os.lstat(path).st_mode | stat.S_IWUSR)
    except FileNotFoundError:
        pass

for dirpath, dirnames, filenames in os.walk(root):
    if ".git" in dirnames:
        dirnames.remove(".git")
    for name in dirnames + filenames:
        path = os.path.join(dirpath, name)
        try:
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                continue
            os.chmod(path, mode | stat.S_IWUSR)
        except FileNotFoundError:
            pass
PY
}

set_tree_readonly() {
  local root="$1"
  [[ -d "$root" ]] || return 0
  python3 - "$root" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH

for dirpath, dirnames, filenames in os.walk(root):
    if ".git" in dirnames:
        dirnames.remove(".git")
    for name in dirnames + filenames:
        path = os.path.join(dirpath, name)
        try:
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                continue
            os.chmod(path, mode & ~write_bits)
        except FileNotFoundError:
            pass

try:
    os.chmod(root, os.lstat(root).st_mode & ~write_bits)
except FileNotFoundError:
    pass
PY
}

update_one_repo() {
  local NAME="$1"
  local URL
  local BRANCH
  local REF
  local COMMIT
  local PATH_RAW
  local READONLY
  URL="$(cfg "meta_info.repositories.$NAME.url")"
  BRANCH="$(cfg "meta_info.repositories.$NAME.branch")"
  REF="$(cfg "meta_info.repositories.$NAME.ref")"
  if [[ -z "$REF" ]]; then
    REF="$BRANCH"
  fi
  if [[ -n "$REF_OVERRIDE" ]]; then
    REF="$REF_OVERRIDE"
  fi
  COMMIT="$(cfg "meta_info.repositories.$NAME.commit")"
  PATH_RAW="$(cfg "meta_info.repositories.$NAME.path")"
  READONLY="$(cfg "meta_info.repositories.$NAME.readonly")"

  [[ -n "$URL" ]] || { echo "ERROR: meta_info.repositories.$NAME.url is empty" >&2; exit 1; }
  [[ -n "$REF" || -n "$COMMIT" ]] || { echo "ERROR: meta_info.repositories.$NAME.branch/ref or commit is required" >&2; exit 1; }
  [[ -n "$PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.$NAME.path is empty" >&2; exit 1; }

  local REPO_DIR
  REPO_DIR="$(abspath "$PATH_RAW")"
  mkdir -p "$(dirname "$REPO_DIR")"

  echo "=== tracer repo update: $NAME ==="
  echo "URL:  $URL"
  echo "ref:  ${REF:-<none>}"
  echo "pin:  ${COMMIT:-<none>}"
  echo "path: $PATH_RAW"

  if git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    set_tree_writable "$REPO_DIR"

    local CURRENT_URL
    CURRENT_URL="$(git -C "$REPO_DIR" remote get-url origin)"
    if [[ "$CURRENT_URL" != "$URL" ]]; then
      echo "ERROR: $PATH_RAW origin is '$CURRENT_URL', expected '$URL'" >&2
      exit 1
    fi

    if [[ -n "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
      echo "ERROR: $PATH_RAW has local modifications; clean it before updating" >&2
      git -C "$REPO_DIR" status --short >&2
      exit 1
    fi

    git -C "$REPO_DIR" fetch --prune origin
  else
    if [[ -e "$REPO_DIR" ]]; then
      echo "ERROR: $PATH_RAW exists but is not a git repo" >&2
      exit 1
    fi
    git clone "$URL" "$REPO_DIR"
    git -C "$REPO_DIR" fetch --prune origin
  fi

  local TARGET="$COMMIT"
  if [[ -z "$TARGET" ]]; then
    TARGET="$REF"
    if git -C "$REPO_DIR" rev-parse --verify --quiet "${REF}^{commit}" >/dev/null; then
      TARGET="$REF"
    elif git -C "$REPO_DIR" rev-parse --verify --quiet "origin/${REF}^{commit}" >/dev/null; then
      TARGET="origin/$REF"
    fi
  fi

  git -C "$REPO_DIR" checkout --detach "$TARGET"
  git -C "$REPO_DIR" submodule update --init --recursive

  local FINAL_COMMIT
  FINAL_COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"

  if [[ "$READONLY" == "true" ]]; then
    set_tree_readonly "$REPO_DIR"
    echo "Set $PATH_RAW working tree read-only (excluding .git)."
  fi

  echo "$NAME ready at $PATH_RAW"
  echo "Commit: $FINAL_COMMIT"
  echo ""
}

command -v git >/dev/null 2>&1 || { echo "ERROR: git is required" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "ERROR: config.yaml not found at $CONFIG" >&2; exit 1; }

mapfile -t ALL_REPOS < <(list_repos)
if [[ ${#ALL_REPOS[@]} -eq 0 ]]; then
  echo "ERROR: meta_info.repositories is empty in $CONFIG" >&2
  exit 1
fi

SELECTED=()
if [[ "$REPO_FILTER" == "all" ]]; then
  SELECTED=("${ALL_REPOS[@]}")
else
  FOUND=0
  for name in "${ALL_REPOS[@]}"; do
    if [[ "$name" == "$REPO_FILTER" ]]; then
      SELECTED=("$name")
      FOUND=1
      break
    fi
  done
  if [[ "$FOUND" -ne 1 ]]; then
    echo "ERROR: --repo '$REPO_FILTER' not found in meta_info.repositories" >&2
    echo "Configured repos: ${ALL_REPOS[*]}" >&2
    exit 1
  fi
fi

if [[ -n "$REF_OVERRIDE" && ${#SELECTED[@]} -gt 1 ]]; then
  echo "ERROR: --ref requires --repo <name> (cannot apply one ref to multiple repos)" >&2
  exit 2
fi

for name in "${SELECTED[@]}"; do
  update_one_repo "$name"
done
