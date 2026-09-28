#!/usr/bin/env bash
# Create or refresh the uv environment for repos/LegoFlow-Trace-Crafter.
# The env lives outside the read-only repo at the path configured in
# meta_info.environment.legoflow_trace_crafter_uv (default: artifacts/env/legoflow-trace-crafter-uv).
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$BLOCK_DIR/config.yaml"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/setup_legoflow_trace_crafter_env.sh
  bash scripts/setup_legoflow_trace_crafter_env.sh --no-extras

Runs `uv sync` (with the extras configured in meta_info.environment.legoflow_trace_crafter_extras)
into the uv project environment at meta_info.environment.legoflow_trace_crafter_uv. The repo
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

SWE_DP_PATH_RAW="$(cfg meta_info.repositories.legoflow_trace_crafter.path)"
SWE_DP_UV_RAW="$(cfg meta_info.environment.legoflow_trace_crafter_uv)"
SWE_DP_READONLY="$(cfg meta_info.repositories.legoflow_trace_crafter.readonly)"

[[ -n "$SWE_DP_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.legoflow_trace_crafter.path is empty" >&2; exit 1; }
[[ -n "$SWE_DP_UV_RAW" ]] || { echo "ERROR: meta_info.environment.legoflow_trace_crafter_uv is empty" >&2; exit 1; }

SWE_DP_DIR="$(abspath "$SWE_DP_PATH_RAW")"
SWE_DP_UV_ABS="$(abspath "$SWE_DP_UV_RAW")"

[[ -e "$SWE_DP_DIR/.git" ]] || { echo "ERROR: legoflow_trace_crafter repo missing at $SWE_DP_PATH_RAW; run bash scripts/update_repos.sh --repo legoflow_trace_crafter" >&2; exit 1; }

case "$SWE_DP_UV_ABS" in
  "$SWE_DP_DIR"/*)
    if [[ "$SWE_DP_READONLY" == "true" ]]; then
      echo "ERROR: legoflow_trace_crafter_uv ($SWE_DP_UV_RAW) is inside the read-only repo. Set it outside repos/LegoFlow-Trace-Crafter." >&2
      exit 1
    fi
    ;;
esac

EXTRA_ARGS=()
if [[ "$USE_EXTRAS" == "1" ]]; then
  while IFS= read -r extra; do
    [[ -n "$extra" ]] || continue
    EXTRA_ARGS+=("--extra" "$extra")
  done < <(cfg_list meta_info.environment.legoflow_trace_crafter_extras)
fi

mkdir -p "$(dirname "$SWE_DP_UV_ABS")"

# `uv sync` writes uv.lock into the repo. update_repos.sh chmods the worktree
# read-only (preserving .git). Root bypasses chmod; non-root users do not, so
# we need to temporarily restore write perms, sync, then re-lock. We also add
# uv.lock to the repo's git exclude file so dryrun's worktree-clean check
# stays green after setup.
LOCAL_EXCLUDE="$(git -C "$SWE_DP_DIR" rev-parse --git-path info/exclude 2>/dev/null || true)"
if [[ -n "$LOCAL_EXCLUDE" ]]; then
  case "$LOCAL_EXCLUDE" in
    /*) : ;;                                  # already absolute
    *)  LOCAL_EXCLUDE="$SWE_DP_DIR/$LOCAL_EXCLUDE" ;;
  esac
  mkdir -p "$(dirname "$LOCAL_EXCLUDE")"
  [[ -f "$LOCAL_EXCLUDE" ]] || : > "$LOCAL_EXCLUDE"
  if ! grep -qxF "uv.lock" "$LOCAL_EXCLUDE" 2>/dev/null; then
    printf '%s\n' "uv.lock" >> "$LOCAL_EXCLUDE"
  fi
fi

echo "=== tracer: setup legoflow_trace_crafter uv env ==="
echo "Repo:    $SWE_DP_PATH_RAW"
echo "Env:     $SWE_DP_UV_RAW"
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
  echo "Extras:  ${EXTRA_ARGS[*]}"
else
  echo "Extras:  (none)"
fi

# Temporarily make the read-only worktree writable so uv can write uv.lock and
# any cached metadata, then restore the read-only state. Trap on EXIT so we
# don't leave the worktree writable if uv sync crashes.
SWE_DP_OWNED_BY_US=1
if [[ ! -w "$SWE_DP_DIR" ]] && [[ "$(stat -c '%U' "$SWE_DP_DIR")" != "$(id -un)" ]]; then
  SWE_DP_OWNED_BY_US=0
fi
relock_swe_dp() {
  if [[ "$SWE_DP_OWNED_BY_US" == "1" ]] && [[ -d "$SWE_DP_DIR" ]]; then
    find "$SWE_DP_DIR" -path "$SWE_DP_DIR/.git" -prune -o -print0 \
      | xargs -0 -r chmod a-w 2>/dev/null || true
    chmod u+w "$SWE_DP_DIR" 2>/dev/null || true
  fi
}
trap relock_swe_dp EXIT
if [[ "$SWE_DP_OWNED_BY_US" == "1" ]]; then
  chmod -R u+w "$SWE_DP_DIR"
fi

(
  cd "$SWE_DP_DIR"
  UV_PROJECT_ENVIRONMENT="$SWE_DP_UV_ABS" uv sync "${EXTRA_ARGS[@]}"
)

# Patch missing transitive deps that legoflow_trace_crafter pyproject.toml does not list:
# - jinja2 is required by transformers.apply_chat_template, used by the LF
#   conversion step in src/legoflow_trace_crafter/utils.py:convert_json_to_lf_format.
#   Without it, all convert_*_to_im.py scripts fail at lf.json write time.
uv pip install --python "$SWE_DP_UV_ABS/bin/python" --quiet jinja2

if [[ -x "$SWE_DP_UV_ABS/bin/python" ]] && "$SWE_DP_UV_ABS/bin/python" -c "import legoflow_trace_crafter, jinja2" >/dev/null 2>&1; then
  echo "OK: legoflow_trace_crafter importable from $SWE_DP_UV_ABS/bin/python (jinja2 patched in)"
else
  echo "ERROR: legoflow_trace_crafter / jinja2 not importable from $SWE_DP_UV_ABS/bin/python after uv sync" >&2
  exit 1
fi
