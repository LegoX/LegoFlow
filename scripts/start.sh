#!/usr/bin/env bash
# Launch the selected task producer, then tracer, in a named tmux session.
# The producer is tracer.task_source.local_block (curator by default).
# trainer and evaluator keep their independent execution surfaces.
# curator retains the legacy Python create_py.sh launcher; use root skills for
# the full language-aware SWE workflow.
#
# Usage: bash scripts/start.sh [--curator-only|--terminal-curator-only|--tracer-only]
#                             [--no-dryrun] [--dry-run] [--no-sync]
# --dry-run prints the ordered plan without launching or archiving anything.
# --no-sync is retained for compatibility; each block uses its existing checkout.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRACER_CONFIG="${TRAJGEN_CONFIG:-$ROOT_DIR/blocks/tracer/config.yaml}"
TERMINAL_CONFIG="${TERMINAL_CURATOR_CONFIG:-$ROOT_DIR/blocks/terminal-curator/config.yaml}"
[[ "$TRACER_CONFIG" = /* ]] || TRACER_CONFIG="$ROOT_DIR/blocks/tracer/$TRACER_CONFIG"
[[ "$TERMINAL_CONFIG" = /* ]] || TERMINAL_CONFIG="$ROOT_DIR/blocks/terminal-curator/$TERMINAL_CONFIG"
export TRAJGEN_CONFIG="$TRACER_CONFIG"
export TERMINAL_CURATOR_CONFIG="$TERMINAL_CONFIG"
RUN_ARGS=("$@")
MODE=pipeline
DO_DRYRUN=1
DRY_RUN=0
FOREGROUND=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --curator-only) MODE=curator; shift ;;
    --terminal-curator-only) MODE=terminal-curator; shift ;;
    --tracer-only) MODE=tracer; shift ;;
    --no-sync) shift ;;
    --no-dryrun) DO_DRYRUN=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --foreground) FOREGROUND=1; shift ;;
    -h|--help) sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

cfg() {
  python3 - "$1" "$2" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as fh:
    value = yaml.safe_load(fh) or {}
for part in sys.argv[2].split("."):
    value = value.get(part) if isinstance(value, dict) else None
print("" if value is None else value)
PY
}

block_config() {
  case "$1" in
    tracer) echo "$TRACER_CONFIG" ;;
    terminal-curator) echo "$TERMINAL_CONFIG" ;;
    *) echo "$ROOT_DIR/blocks/$1/config.yaml" ;;
  esac
}

remote_config_path() {
  python3 - "$ROOT_DIR/blocks/$1" "$2" "$3" <<'PY'
import os
from pathlib import Path
import posixpath
import sys

block, profile, remote_directory = sys.argv[1:4]
profile = Path(os.path.abspath(profile))
try:
    relative = profile.relative_to(Path(os.path.abspath(block))).as_posix()
except ValueError:
    print(profile)
else:
    # The worker enters resources.directory before launching the child. Keep
    # an in-block profile at the same relative location in that checkout.
    print(posixpath.join(remote_directory, relative) if posixpath.isabs(remote_directory) else relative)
PY
}

BLOCKS=()
if [[ "$MODE" == pipeline ]]; then
  PRODUCER="$(python3 "$ROOT_DIR/blocks/tracer/scripts/task_source.py" --config "$TRACER_CONFIG" --field producer)"
  [[ -z "$PRODUCER" ]] || BLOCKS+=("$PRODUCER")
  BLOCKS+=(tracer)
else
  BLOCKS+=("$MODE")
fi

echo "LegoFlow execution order: ${BLOCKS[*]}"
for block in "${BLOCKS[@]}"; do
  entrypoint=start.sh
  [[ "$block" != curator ]] || entrypoint=create_py.sh
  [[ -f "$ROOT_DIR/blocks/$block/scripts/$entrypoint" ]] || {
    echo "ERROR: missing blocks/$block/scripts/$entrypoint" >&2
    exit 1
  }
done

if [[ "$DRY_RUN" == 1 ]]; then
  for block in "${BLOCKS[@]}"; do
    config_path="$(block_config "$block")"
    host="$(cfg "$config_path" meta_info.resources.ip)"
    entrypoint=start.sh
    [[ "$block" != curator ]] || entrypoint=create_py.sh
    echo "[DRY-RUN] $block (${host:-local}): bash blocks/$block/scripts/$entrypoint"
    [[ "$block" != tracer ]] || echo "[DRY-RUN] tracer: prepare_tasks.sh stages the selected verified source before rollout"
  done
  exit 0
fi

if [[ "$DO_DRYRUN" == 1 ]]; then
  bash "$ROOT_DIR/scripts/dryrun.sh"
fi

# The launcher returns promptly; the worker remains attached to tmux and waits
# for each producer to succeed before touching its consumer.
if [[ "$FOREGROUND" != 1 ]]; then
  command -v tmux >/dev/null || { echo "ERROR: tmux is required" >&2; exit 1; }
  session="legoflow-${MODE}"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "ERROR: tmux session $session already exists; inspect it before starting another run" >&2
    exit 1
  fi
  printf -v launch '%q ' env "TRAJGEN_CONFIG=$TRACER_CONFIG" bash "$ROOT_DIR/scripts/start.sh" "${RUN_ARGS[@]}" --foreground --no-dryrun
  tmux_env=()
  for variable in PATH OPENAI_API_KEY OPENAI_API_BASE_URL OPENAI_API_BASE OPENAI_BASE_URL OPENAI_MODEL MODEL_NAME \
      ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_MODEL CLAUDE_CODE_OAUTH_TOKEN \
      STACKEXCHANGE_API_KEY SO_API_KEY SO_API_BASE_URL TERMINAL_CURATOR_CONFIG TERMINAL_CURATOR_PYTHON \
      GITHUB_TOKEN GITHUB_TOKENS DOCKER_HOST DOCKER_REGISTRY DOCKER_USERNAME DOCKER_PASSWORD DOCKER_MIRROR; do
    [[ ! -v "$variable" ]] || tmux_env+=(-e "$variable=${!variable}")
  done
  tmux new-session "${tmux_env[@]}" -d -s "$session" -x 220 -y 50 "$launch"
  echo "Started $session; attach with: tmux attach -t $session"
  exit 0
fi

RUN_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
archive_on_exit() {
  local rc=$?
  trap - EXIT
  bash "$ROOT_DIR/scripts/archive_run.sh" "$rc" "$RUN_STARTED_AT" || true
  exit "$rc"
}
trap archive_on_exit EXIT

run_block() {
  local block="$1" config host user directory command worker channel option remote_command remote_config
  config="$(block_config "$block")"
  host="$(cfg "$config" meta_info.resources.ip)"
  user="$(cfg "$config" meta_info.resources.user)"
  directory="$(cfg "$config" meta_info.resources.directory)"
  echo "Starting $block"
  if [[ -z "$host" || "$host" == local || "$host" == null ]]; then
    if [[ "$block" == tracer ]]; then
      (cd "$ROOT_DIR/blocks/tracer" && bash scripts/prepare_tasks.sh && bash scripts/start.sh)
    elif [[ "$block" == curator ]]; then
      (cd "$ROOT_DIR/blocks/curator" && bash scripts/create_py.sh)
    else
      (cd "$ROOT_DIR/blocks/$block" && bash scripts/start.sh)
    fi
  else
    [[ -n "$directory" && "$directory" != null ]] || {
      echo "ERROR: $block remote execution requires resources.directory (the block checkout)" >&2
      return 1
    }
    [[ -z "$user" || "$user" == null ]] || host="$user@$host"
    remote_config="$(remote_config_path "$block" "$config" "$directory")"
    case "$block" in
      terminal-curator)
        printf -v command 'cd %q && bash scripts/start.sh --config %q' "$directory" "$remote_config"
        ;;
      tracer)
        printf -v command 'cd %q && export TRAJGEN_CONFIG=%q && bash scripts/prepare_tasks.sh && bash scripts/start.sh' "$directory" "$remote_config"
        ;;
      *) printf -v command 'cd %q && bash scripts/create_py.sh' "$directory" ;;
    esac
    # A detached remote tmux worker reports the real block exit code. Keep it
    # alive until the caller reads the code, including on a fresh tmux server.
    channel="legoflow-$block-$$"
    option="@$channel-result"
    worker="$command; result=\$?; tmux set-option -g '$option' \"\$result\"; tmux wait-for -S '$channel'; tmux wait-for '$channel-release'; exit \"\$result\""
    printf -v remote_command 'tmux new-session -d -s %q %q && tmux wait-for %q && ' "$channel" "$worker" "$channel"
    remote_command+="result=\$(tmux show-option -gv '$option'); tmux set-option -gu '$option'; tmux wait-for -S '$channel-release'; exit \"\$result\""
    ssh -o BatchMode=yes "$host" "$remote_command"
  fi
  echo "Completed $block"
}

for block in "${BLOCKS[@]}"; do
  run_block "$block"
done
