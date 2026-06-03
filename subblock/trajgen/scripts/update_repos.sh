#!/usr/bin/env bash
# Clone or update local-only repos used by trajgen.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/update_repos.sh
  bash scripts/update_repos.sh --ref <branch|tag|commit>

Updates repos/harbor from config.yaml. If repositories.harbor.commit is set,
that exact commit is checked out after fetching the configured branch/ref. The
Harbor worktree must be clean before an existing checkout is updated.
EOF
}

REF_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
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

command -v git >/dev/null 2>&1 || { echo "ERROR: git is required" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "ERROR: config.yaml not found at $CONFIG" >&2; exit 1; }

HARBOR_URL="$(cfg meta_info.repositories.harbor.url)"
HARBOR_BRANCH="$(cfg meta_info.repositories.harbor.branch)"
HARBOR_REF="$(cfg meta_info.repositories.harbor.ref)"
if [[ -z "$HARBOR_REF" ]]; then
  HARBOR_REF="$HARBOR_BRANCH"
fi
if [[ -n "$REF_OVERRIDE" ]]; then
  HARBOR_REF="$REF_OVERRIDE"
fi
HARBOR_COMMIT="$(cfg meta_info.repositories.harbor.commit)"
HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
READONLY="$(cfg meta_info.repositories.harbor.readonly)"

[[ -n "$HARBOR_URL" ]] || { echo "ERROR: meta_info.repositories.harbor.url is empty" >&2; exit 1; }
[[ -n "$HARBOR_REF" || -n "$HARBOR_COMMIT" ]] || { echo "ERROR: meta_info.repositories.harbor.branch/ref or commit is required" >&2; exit 1; }
[[ -n "$HARBOR_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.harbor.path is empty" >&2; exit 1; }

HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
mkdir -p "$(dirname "$HARBOR_DIR")"

echo "=== trajgen repo update ==="
echo "Harbor URL:  $HARBOR_URL"
echo "Harbor ref:  ${HARBOR_REF:-<none>}"
echo "Harbor pin:  ${HARBOR_COMMIT:-<none>}"
echo "Harbor path: $HARBOR_PATH_RAW"

if git -C "$HARBOR_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  set_tree_writable "$HARBOR_DIR"

  CURRENT_URL="$(git -C "$HARBOR_DIR" remote get-url origin)"
  if [[ "$CURRENT_URL" != "$HARBOR_URL" ]]; then
    echo "ERROR: repos/harbor origin is '$CURRENT_URL', expected '$HARBOR_URL'" >&2
    exit 1
  fi

  if [[ -n "$(git -C "$HARBOR_DIR" status --porcelain)" ]]; then
    echo "ERROR: repos/harbor has local modifications; clean it before updating" >&2
    git -C "$HARBOR_DIR" status --short >&2
    exit 1
  fi

  git -C "$HARBOR_DIR" fetch --prune origin
else
  if [[ -e "$HARBOR_DIR" ]]; then
    echo "ERROR: $HARBOR_PATH_RAW exists but is not a git repo" >&2
    exit 1
  fi
  git clone "$HARBOR_URL" "$HARBOR_DIR"
  git -C "$HARBOR_DIR" fetch --prune origin
fi

TARGET="$HARBOR_COMMIT"
OUTPUT_REF="$HARBOR_REF"
if [[ -z "$TARGET" ]]; then
  TARGET="$HARBOR_REF"
  if git -C "$HARBOR_DIR" rev-parse --verify --quiet "${HARBOR_REF}^{commit}" >/dev/null; then
    TARGET="$HARBOR_REF"
  elif git -C "$HARBOR_DIR" rev-parse --verify --quiet "origin/${HARBOR_REF}^{commit}" >/dev/null; then
    TARGET="origin/$HARBOR_REF"
  fi
else
  OUTPUT_REF="${HARBOR_REF:-$HARBOR_COMMIT}"
fi

git -C "$HARBOR_DIR" checkout --detach "$TARGET"
git -C "$HARBOR_DIR" submodule update --init --recursive

COMMIT="$(git -C "$HARBOR_DIR" rev-parse HEAD)"

if [[ "$READONLY" == "true" ]]; then
  set_tree_readonly "$HARBOR_DIR"
  echo "Set Harbor working tree read-only (excluding .git)."
fi

echo "Harbor ready at $HARBOR_PATH_RAW"
echo "Commit: $COMMIT"
