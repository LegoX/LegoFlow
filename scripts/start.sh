#!/usr/bin/env bash
# Launch swegen and trajgen on the remote node inside named tmux sessions.
# sft and rl are not yet connected and are skipped.
#
# Usage:
#   bash scripts/start.sh [options]
#
# Options:
#   --swegen-only    start only swegen (skip trajgen)
#   --trajgen-only   start only trajgen (skip swegen)
#   --no-sync        skip rsync of local code to remote node
#   --no-dryrun      skip the dryrun validation step
#   --dry-run        print SSH/tmux commands without executing
#   -h, --help       show this help
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWEGEN_CFG="$ROOT_DIR/subblock/swegen/config.yaml"

START_SWEGEN=1
START_TRAJGEN=1
DO_SYNC=1
DO_DRYRUN=1
DRY_RUN=0

usage() {
  sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --swegen-only)  START_TRAJGEN=0; shift ;;
    --trajgen-only) START_SWEGEN=0;  shift ;;
    --no-sync)      DO_SYNC=0;       shift ;;
    --no-dryrun)    DO_DRYRUN=0;     shift ;;
    --dry-run)      DRY_RUN=1;       shift ;;
    -h|--help)      usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

cfg() {
  python3 - "$SWEGEN_CFG" "$1" <<'PY'
import sys
try:
    import yaml
except ImportError:
    print("")
    sys.exit(0)
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
else:
    print(str(value))
PY
}

run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [DRY-RUN] $*"
  else
    "$@"
  fi
}

ssh_run() {
  local host="$1"; shift
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [DRY-RUN] ssh ${host} '$*'"
  else
    ssh -o BatchMode=yes -o StrictHostKeyChecking=no "$host" "$@"
  fi
}

echo "=== Starting Block: swe_lego_live ==="
echo ""

# ── Read execution-location config ───────────────────────────────────────────
# Default: run locally. Treat ip == 'local' or empty/null as local execution.
# Only opt into remote SSH+rsync if ip is a real remote host.
REMOTE_IP="$(cfg "meta_info.resources.ip")"
REMOTE_USER="$(cfg "meta_info.resources.user")"
REMOTE_DIR="$(cfg "meta_info.resources.directory")"

if [[ -z "$REMOTE_IP" || "$REMOTE_IP" == "null" || "$REMOTE_IP" == "local" ]]; then
  IS_LOCAL=1
else
  IS_LOCAL=0
fi

if [[ $IS_LOCAL -eq 1 ]]; then
  REPO_DIR="$ROOT_DIR"
  echo "Execution   : local (ip=${REMOTE_IP:-<unset>})"
  echo "Repo path   : ${REPO_DIR}"
else
  if [[ -z "$REMOTE_DIR" || "$REMOTE_DIR" == "null" ]]; then
    echo "ERROR: meta_info.resources.directory not set in subblock/swegen/config.yaml" >&2
    exit 1
  fi
  REMOTE_HOST="${REMOTE_USER}@${REMOTE_IP}"
  REPO_DIR="${REMOTE_DIR%/}/SWE-Lego-Live"
  echo "Execution   : remote"
  echo "Remote node : ${REMOTE_HOST}"
  echo "Remote path : ${REPO_DIR}"
fi
echo ""

# ── 1. Dryrun validation ──────────────────────────────────────────────────────
if [[ $DO_DRYRUN -eq 1 ]]; then
  echo "Step 1: Validating (dryrun) ..."
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [DRY-RUN] bash scripts/dryrun.sh"
  else
    bash "$ROOT_DIR/scripts/dryrun.sh" || {
      echo "ERROR: dryrun failed — fix the issues above before starting." >&2
      exit 1
    }
  fi
  echo ""
fi

# ── 2. Sync code to remote ────────────────────────────────────────────────────
if [[ $DO_SYNC -eq 1 ]]; then
  if [[ $IS_LOCAL -eq 1 ]]; then
    echo "Step 2: Skipping rsync (local execution)"
    echo ""
  else
    echo "Step 2: Syncing code to remote ..."
    run rsync -az --delete \
      --exclude='.git/' \
      --exclude='artifacts/' \
      --exclude='.claude/' \
      --exclude='subblock/swegen/repos/' \
      --exclude='subblock/trajgen/repos/' \
      --exclude='subblock/swegen/artifacts/' \
      --exclude='subblock/trajgen/artifacts/' \
      --exclude='subblock/swegen/gh_token.txt' \
      "${ROOT_DIR}/" "${REMOTE_HOST}:${REPO_DIR}/"
    echo "  synced to ${REMOTE_HOST}:${REPO_DIR}"
    echo ""
  fi
fi

# Helper: start a tmux session running CMD, locally or via SSH depending on IS_LOCAL.
tmux_start() {
  local session="$1"; shift
  local cmd="$1"; shift
  if [[ $IS_LOCAL -eq 1 ]]; then
    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  [DRY-RUN] tmux new-session -d -s '${session}' && tmux send-keys -t '${session}' '${cmd}' Enter"
    else
      tmux new-session -d -s "${session}" -x 220 -y 50
      tmux send-keys -t "${session}" "${cmd}" Enter
    fi
  else
    ssh_run "$REMOTE_HOST" \
      "tmux new-session -d -s '${session}' -x 220 -y 50 && \
       tmux send-keys -t '${session}' '${cmd}' Enter"
  fi
}

# Helper: check if a tmux session already exists (locally or remotely).
tmux_has_session() {
  local session="$1"
  if [[ $DRY_RUN -eq 1 ]]; then
    return 1   # in dry-run, never claim a session exists
  fi
  if [[ $IS_LOCAL -eq 1 ]]; then
    tmux has-session -t "${session}" 2>/dev/null
  else
    ssh_run "$REMOTE_HOST" "tmux has-session -t '${session}'" 2>/dev/null
  fi
}

# Helper: print the attach hint for a session.
attach_hint() {
  local session="$1"
  if [[ $IS_LOCAL -eq 1 ]]; then
    echo "         attach with: tmux attach -t ${session}"
  else
    echo "         attach with: ssh ${REMOTE_HOST} -t tmux attach -t ${session}"
  fi
}

# ── 3. Start swegen ───────────────────────────────────────────────────────────
if [[ $START_SWEGEN -eq 1 ]]; then
  echo "Step 3: Starting swegen ..."
  SWEGEN_SESSION="swegen-py"
  SWEGEN_DIR="${REPO_DIR}/subblock/swegen"

  if tmux_has_session "${SWEGEN_SESSION}"; then
    echo "  [SKIP] tmux session '${SWEGEN_SESSION}' already exists"
    attach_hint "${SWEGEN_SESSION}"
  else
    SWEGEN_CMD="cd '${SWEGEN_DIR}' && bash scripts/create_py.sh"
    tmux_start "${SWEGEN_SESSION}" "${SWEGEN_CMD}"
    echo "  started tmux session '${SWEGEN_SESSION}'"
    attach_hint "${SWEGEN_SESSION}"
  fi
  echo ""
fi

# ── 4. Start trajgen ──────────────────────────────────────────────────────────
if [[ $START_TRAJGEN -eq 1 ]]; then
  echo "Step 4: Starting trajgen ..."
  TRAJGEN_SESSION="trajgen"
  TRAJGEN_DIR="${REPO_DIR}/subblock/trajgen"

  if tmux_has_session "${TRAJGEN_SESSION}"; then
    echo "  [SKIP] tmux session '${TRAJGEN_SESSION}' already exists"
    attach_hint "${TRAJGEN_SESSION}"
  else
    TRAJGEN_CMD="cd '${TRAJGEN_DIR}' && bash scripts/prepare_tasks.sh && bash scripts/start.sh"
    tmux_start "${TRAJGEN_SESSION}" "${TRAJGEN_CMD}"
    echo "  started tmux session '${TRAJGEN_SESSION}'"
    attach_hint "${TRAJGEN_SESSION}"
  fi
  echo ""
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo "=== Done ==="
echo ""
if [[ $IS_LOCAL -eq 1 ]]; then
  echo "Monitor sessions locally:"
  echo "  tmux ls"
  [[ $START_SWEGEN  -eq 1 ]] && echo "  tmux attach -t swegen-py    # swegen task generation"
  [[ $START_TRAJGEN -eq 1 ]] && echo "  tmux attach -t trajgen      # trajgen trajectory generation"
else
  echo "Monitor sessions on the remote node:"
  echo "  ssh ${REMOTE_HOST}"
  echo "  tmux ls"
  [[ $START_SWEGEN  -eq 1 ]] && echo "  tmux attach -t swegen-py    # swegen task generation"
  [[ $START_TRAJGEN -eq 1 ]] && echo "  tmux attach -t trajgen      # trajgen trajectory generation"
fi
