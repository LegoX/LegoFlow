#!/usr/bin/env bash
# Create or refresh the uv environment for repos/harbor.
# The env lives outside the read-only repo at the path configured in
# meta_info.environment.harbor_uv (default: artifacts/env/harbor-uv).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/setup_harbor_env.sh

Runs `uv sync` into the uv project environment at meta_info.environment.harbor_uv.
The repo itself stays read-only; uv writes only into the external venv path.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

cfg() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr); sys.exit(2)
config_path, dotted_key = sys.argv[1], sys.argv[2]
with open(config_path, encoding="utf-8") as fh: data = yaml.safe_load(fh) or {}
value = data
for part in dotted_key.split("."):
    if not isinstance(value, dict): value = None; break
    value = value.get(part)
print("" if value is None else ("true" if isinstance(value, bool) and value else ("false" if isinstance(value, bool) else value)))
PY
}

abspath() { local p="$1"; [[ "$p" = /* ]] && echo "$p" || echo "$BLOCK_DIR/$p"; }

command -v uv >/dev/null 2>&1 || { echo "ERROR: uv is required (install via: curl -LsSf https://astral.sh/uv/install.sh | sh)" >&2; exit 1; }

HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
HARBOR_UV_RAW="$(cfg meta_info.environment.harbor_uv)"

[[ -n "$HARBOR_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.harbor.path is empty" >&2; exit 1; }
[[ -n "$HARBOR_UV_RAW" ]] || { echo "ERROR: meta_info.environment.harbor_uv is empty" >&2; exit 1; }

HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
HARBOR_UV_ABS="$(abspath "$HARBOR_UV_RAW")"

[[ -d "$HARBOR_DIR" ]] || { echo "ERROR: $HARBOR_DIR not found; run scripts/update_repos.sh first" >&2; exit 1; }
case "$HARBOR_UV_ABS" in "$HARBOR_DIR"/*)
  echo "ERROR: harbor_uv ($HARBOR_UV_RAW) is inside the read-only repo. Set it outside repos/harbor." >&2; exit 1 ;;
esac

mkdir -p "$(dirname "$HARBOR_UV_ABS")"

echo "=== trajgen: setup harbor uv env ==="
echo "Repo:    $HARBOR_PATH_RAW"
echo "Env:     $HARBOR_UV_RAW"

# Same chmod dance as swe_data_process: update_repos.sh chmods the worktree
# read-only; uv sync needs to write uv.lock and .venv-cache entries during a
# resolve, so temporarily restore write perms then re-lock on exit.
HARBOR_OWNED_BY_US=1
if [[ ! -w "$HARBOR_DIR" ]] && [[ "$(stat -c '%U' "$HARBOR_DIR")" != "$(id -un)" ]]; then
  HARBOR_OWNED_BY_US=0
fi
relock_harbor() {
  if [[ "$HARBOR_OWNED_BY_US" == "1" ]] && [[ -d "$HARBOR_DIR" ]]; then
    find "$HARBOR_DIR" -path "$HARBOR_DIR/.git" -prune -o -print0 \
      | xargs -0 -r chmod a-w 2>/dev/null || true
    chmod u+w "$HARBOR_DIR" 2>/dev/null || true
  fi
}
trap relock_harbor EXIT
if [[ "$HARBOR_OWNED_BY_US" == "1" ]]; then
  chmod -R u+w "$HARBOR_DIR"
fi

(
  cd "$HARBOR_DIR"
  UV_PROJECT_ENVIRONMENT="$HARBOR_UV_ABS" uv sync
)

if [[ -x "$HARBOR_UV_ABS/bin/python" ]] && "$HARBOR_UV_ABS/bin/python" -c "import harbor" >/dev/null 2>&1; then
  echo "OK: harbor importable from $HARBOR_UV_ABS/bin/python"
else
  echo "ERROR: harbor not importable from $HARBOR_UV_ABS/bin/python after uv sync" >&2
  exit 1
fi
