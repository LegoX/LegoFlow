#!/usr/bin/env bash
# Create or refresh the uv environment for repos/swe_data_process.
# The env lives outside the read-only repo at the path configured in
# meta_info.environment.swe_data_process_uv (default: artifacts/env/swe-data-process-uv).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/setup_swe_data_process_env.sh
  bash scripts/setup_swe_data_process_env.sh --no-extras

Runs `uv sync` (with the extras configured in meta_info.environment.swe_data_process_extras)
into the uv project environment at meta_info.environment.swe_data_process_uv. The repo
itself stays read-only; uv writes only into the external venv path.

Pass --no-extras to install base dependencies only.
EOF
}

USE_EXTRAS=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-extras)
      USE_EXTRAS=0
      shift
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
    print("ERROR: PyYAML is required", file=sys.stderr)
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

cfg_list() {
  python3 - "$CONFIG" "$1" <<'PY'
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required", file=sys.stderr)
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
    sys.exit(0)
if not isinstance(value, list):
    print(f"ERROR: {dotted_key} must be a list", file=sys.stderr)
    sys.exit(2)
for item in value:
    print("" if item is None else item)
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

command -v uv >/dev/null 2>&1 || { echo "ERROR: uv is required (install via: curl -LsSf https://astral.sh/uv/install.sh | sh)" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "ERROR: config.yaml not found at $CONFIG" >&2; exit 1; }

SWE_DP_PATH_RAW="$(cfg meta_info.repositories.swe_data_process.path)"
SWE_DP_UV_RAW="$(cfg meta_info.environment.swe_data_process_uv)"
SWE_DP_READONLY="$(cfg meta_info.repositories.swe_data_process.readonly)"

[[ -n "$SWE_DP_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.swe_data_process.path is empty" >&2; exit 1; }
[[ -n "$SWE_DP_UV_RAW" ]] || { echo "ERROR: meta_info.environment.swe_data_process_uv is empty" >&2; exit 1; }

SWE_DP_DIR="$(abspath "$SWE_DP_PATH_RAW")"
SWE_DP_UV_ABS="$(abspath "$SWE_DP_UV_RAW")"

[[ -e "$SWE_DP_DIR/.git" ]] || { echo "ERROR: swe_data_process repo missing at $SWE_DP_PATH_RAW; run bash scripts/update_repos.sh --repo swe_data_process" >&2; exit 1; }

case "$SWE_DP_UV_ABS" in
  "$SWE_DP_DIR"/*)
    if [[ "$SWE_DP_READONLY" == "true" ]]; then
      echo "ERROR: swe_data_process_uv ($SWE_DP_UV_RAW) is inside the read-only repo. Set it outside repos/swe_data_process." >&2
      exit 1
    fi
    ;;
esac

EXTRA_ARGS=()
if [[ "$USE_EXTRAS" == "1" ]]; then
  while IFS= read -r extra; do
    [[ -n "$extra" ]] || continue
    EXTRA_ARGS+=("--extra" "$extra")
  done < <(cfg_list meta_info.environment.swe_data_process_extras)
fi

mkdir -p "$(dirname "$SWE_DP_UV_ABS")"

# `uv sync` writes uv.lock into the repo even when readonly perms are set
# (root bypasses chmod). Add it to .git/info/exclude so dryrun's worktree-clean
# check stays green after setup. .git/ is preserved writable by update_repos.sh.
LOCAL_EXCLUDE="$SWE_DP_DIR/.git/info/exclude"
if [[ -f "$LOCAL_EXCLUDE" ]] && ! grep -qxF "uv.lock" "$LOCAL_EXCLUDE" 2>/dev/null; then
  printf '%s\n' "uv.lock" >> "$LOCAL_EXCLUDE"
fi

echo "=== trajgen: setup swe_data_process uv env ==="
echo "Repo:    $SWE_DP_PATH_RAW"
echo "Env:     $SWE_DP_UV_RAW"
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  echo "Extras:  ${EXTRA_ARGS[*]}"
else
  echo "Extras:  (none)"
fi

(
  cd "$SWE_DP_DIR"
  UV_PROJECT_ENVIRONMENT="$SWE_DP_UV_ABS" uv sync "${EXTRA_ARGS[@]}"
)

# Patch missing transitive deps that swe_data_process pyproject.toml does not list:
# - jinja2 is required by transformers.apply_chat_template, used by the LF
#   conversion step in src/swe_data_process/utils.py:convert_json_to_lf_format.
#   Without it, all convert_*_to_im.py scripts fail at lf.json write time.
uv pip install --python "$SWE_DP_UV_ABS/bin/python" --quiet jinja2

if [[ -x "$SWE_DP_UV_ABS/bin/python" ]] && "$SWE_DP_UV_ABS/bin/python" -c "import swe_data_process, jinja2" >/dev/null 2>&1; then
  echo "OK: swe_data_process importable from $SWE_DP_UV_ABS/bin/python (jinja2 patched in)"
else
  echo "ERROR: swe_data_process / jinja2 not importable from $SWE_DP_UV_ABS/bin/python after uv sync" >&2
  exit 1
fi
