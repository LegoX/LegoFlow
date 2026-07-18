#!/usr/bin/env bash
# CI test 02: repos/harbor at the pinned commit, origin matches, worktree clean.
# Evaluator uses a single managed Harbor submodule; unlike tracer there is no
# swe_data_process checkout. The Harbor worktree is set read-only after checkout
# (repositories.harbor.readonly: true), which does not affect git status.

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

normalize_git_url() {
  local url="${1%/}"
  url="${url%.git}"
  case "$url" in
    git@*:* ) url="${url#git@}"; url="${url/:/\/}" ;;
    ssh://git@* ) url="${url#ssh://git@}" ;;
    https://* ) url="${url#https://}" ;;
    http://* ) url="${url#http://}" ;;
  esac
  printf '%s\n' "$url"
}

abspath() {
  if [[ "$1" = /* ]]; then printf '%s\n' "$1"; else printf '%s\n' "$BLOCK_DIR/$1"; fi
}

check_repo() {
  local name="$1" url commit path abs
  url="$(cfg "meta_info.repositories.$name.url")"
  commit="$(cfg "meta_info.repositories.$name.commit")"
  path="$(cfg "meta_info.repositories.$name.path")"
  abs="$(abspath "$path")"

  [[ -e "$abs/.git" ]] || { echo "FAIL: $path/.git missing — run scripts/update_repos.sh"; return 1; }

  local origin head
  origin="$(git -C "$abs" remote get-url origin 2>/dev/null || true)"
  if [[ "$origin" != "$url" \
      && "$(normalize_git_url "$origin")" != "$(normalize_git_url "$url")" ]]; then
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

if check_repo "harbor"; then
  echo "PASS: repos/harbor pinned and clean"
else
  exit 1
fi

# An explicit --ref must override (not be overwritten by) the configured commit.
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
mkdir -p "$TMP_DIR/bin"
cat >"$TMP_DIR/config.yaml" <<YAML
meta_info:
  repositories:
    harbor:
      url: https://example.invalid/harbor.git
      branch: main
      commit: pinned-commit
      path: $TMP_DIR/harbor
      readonly: false
YAML
cat >"$TMP_DIR/bin/git" <<'SH'
#!/usr/bin/env bash
echo "$*" >>"$FAKE_GIT_LOG"
if [[ "$1" == "clone" ]]; then
  mkdir -p "$3/.git"
  exit 0
fi
if [[ "$1" == "-C" && "$3" == "rev-parse" && "$4" == "--is-inside-work-tree" ]]; then
  exit 1
fi
if [[ "$1" == "-C" && "$3" == "rev-parse" && "$4" == "HEAD" ]]; then
  echo resolved-feature
fi
exit 0
SH
chmod +x "$TMP_DIR/bin/git"
FAKE_GIT_LOG="$TMP_DIR/git.log" \
PATH="$TMP_DIR/bin:$PATH" \
EVAL_CONFIG="$TMP_DIR/config.yaml" \
  bash "$BLOCK_DIR/scripts/update_repos.sh" --ref feature >/dev/null
grep -Fq "checkout --detach feature" "$TMP_DIR/git.log" || {
  echo "FAIL: update_repos.sh --ref did not override configured commit"; exit 1;
}
if grep -Fq "checkout --detach pinned-commit" "$TMP_DIR/git.log"; then
  echo "FAIL: update_repos.sh --ref still checked out configured commit"
  exit 1
fi
echo "PASS: update_repos.sh --ref overrides configured commit"
