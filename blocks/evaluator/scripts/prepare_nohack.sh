#!/usr/bin/env bash
# Ensure the local SWE-Bench Verified nohack dataset + registry exist.
#
# Wraps repos/harbor/scripts/misc/generate_swebench_verified_nohack.py.
# Used by start.sh when runtime_info.input.task_source.no_hack is true.
#
# Prints the absolute path of the nohack registry on stdout (last line).
# Diagnostics go to stderr.
set -euo pipefail

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${EVAL_CONFIG:-$BLOCK_DIR/config.yaml}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/prepare_nohack.sh                # generate dataset+registry if missing
  bash scripts/prepare_nohack.sh --limit N      # generate a limitN registry (smoke)
  bash scripts/prepare_nohack.sh --print-path   # print the registry path only; no generation
  NOHACK_REFRESH=1 bash scripts/prepare_nohack.sh   # force regenerate (same as --refresh)

Prints the absolute path of the nohack registry on stdout (last line).
Diagnostics go to stderr.
EOF
}

LIMIT=""
REFRESH="${NOHACK_REFRESH:-0}"
PRINT_PATH_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit)
      LIMIT="${2:-}"
      [[ "$LIMIT" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: --limit requires a positive integer (got '${2:-}')" >&2; exit 2; }
      shift 2
      ;;
    --refresh)
      REFRESH=1
      shift
      ;;
    --print-path)
      PRINT_PATH_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cfg() {
  local key="$1"
  python3 - "$CONFIG" "$key" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)
data = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
cur = data
for part in sys.argv[2].split("."):
    if not isinstance(cur, dict) or part not in cur:
        print("")
        sys.exit(0)
    cur = cur[part]
if cur is None:
    print("")
elif isinstance(cur, bool):
    print("true" if cur else "false")
else:
    print(cur)
PY
}

abspath() {
  local path="$1"
  if [[ "$path" = /* ]]; then
    printf '%s\n' "$path"
  else
    printf '%s\n' "$BLOCK_DIR/$path"
  fi
}

HARBOR_PATH_RAW="$(cfg meta_info.repositories.harbor.path)"
[[ -n "$HARBOR_PATH_RAW" ]] || { echo "ERROR: meta_info.repositories.harbor.path is empty" >&2; exit 1; }
HARBOR_DIR="$(abspath "$HARBOR_PATH_RAW")"
[[ -e "$HARBOR_DIR/.git" ]] || { echo "ERROR: $HARBOR_PATH_RAW missing; run bash scripts/update_repos.sh" >&2; exit 1; }

REGISTRY_BASE="$HARBOR_DIR/scripts/git_ignore/hack_control/registry.swebench_verified_nohack.json"
SOURCE_TASK_DIR="$HARBOR_DIR/datasets/swebench-verified-local"
NOHACK_TASK_DIR="$HARBOR_DIR/datasets/swebench-verified-nohack"

if [[ -n "$LIMIT" ]]; then
  REGISTRY_PATH="${REGISTRY_BASE%.json}.limit${LIMIT}.json"
else
  REGISTRY_PATH="$REGISTRY_BASE"
fi

# --print-path: single source of truth for the registry path derivation
# (dryrun.sh uses this instead of re-deriving base + limit suffix itself).
if [[ "$PRINT_PATH_ONLY" == "1" ]]; then
  printf '%s\n' "$REGISTRY_PATH"
  exit 0
fi

ROOT_REGISTRY_RAW="$(cfg runtime_info.input.task_source.registry_path)"
if [[ -z "$ROOT_REGISTRY_RAW" ]]; then
  ROOT_REGISTRY_RAW="$HARBOR_PATH_RAW/registry.json"
fi
ROOT_REGISTRY="$(abspath "$ROOT_REGISTRY_RAW")"
[[ -f "$ROOT_REGISTRY" ]] || { echo "ERROR: root registry not found at $ROOT_REGISTRY_RAW" >&2; exit 1; }

GENERATOR="$HARBOR_DIR/scripts/misc/generate_swebench_verified_nohack.py"
[[ -f "$GENERATOR" ]] || {
  echo "ERROR: nohack generator missing: $GENERATOR" >&2
  echo "       Update Harbor (bash scripts/update_repos.sh) to a pin that includes nohack support." >&2
  exit 1
}

UV_ENV_RAW="$(cfg meta_info.environment.harbor_uv)"
if [[ -n "$UV_ENV_RAW" ]]; then
  export UV_PROJECT_ENVIRONMENT="$(abspath "$UV_ENV_RAW")"
fi

if [[ ! -f "$REGISTRY_PATH" || "$REFRESH" == "1" ]]; then
  echo "=== preparing nohack dataset ===" >&2
  echo "root registry: $ROOT_REGISTRY" >&2
  echo "output registry: $REGISTRY_PATH" >&2
  echo "task dir: $NOHACK_TASK_DIR" >&2
  generator_args=(
    --root-registry-path "$ROOT_REGISTRY"
    --source-task-dir "$SOURCE_TASK_DIR"
    --task-dir "$NOHACK_TASK_DIR"
    --output-registry-path "$REGISTRY_PATH"
  )
  if [[ "$REFRESH" == "1" ]]; then
    generator_args+=(--overwrite --download-overwrite)
  fi
  if [[ -n "$LIMIT" ]]; then
    generator_args+=(--limit "$LIMIT")
  fi
  (
    cd "$HARBOR_DIR"
    uv run python "$GENERATOR" "${generator_args[@]}"
  ) >&2
else
  echo "=== nohack registry present: $REGISTRY_PATH ===" >&2
fi

[[ -f "$REGISTRY_PATH" ]] || { echo "ERROR: nohack registry still missing after prepare: $REGISTRY_PATH" >&2; exit 1; }
printf '%s\n' "$REGISTRY_PATH"
