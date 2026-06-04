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

check_remote_git_repo() {
  local label="$1"
  local path="$2"
  local hint="$3"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [DRY-RUN] check remote git repo ${path} (${label})"
    return 0
  fi
  if ! ssh_run "$REMOTE_HOST" "git -C '${path}' rev-parse --is-inside-work-tree >/dev/null 2>&1"; then
    echo "ERROR: remote ${label} checkout is missing at ${path}" >&2
    echo "       ${hint}" >&2
    exit 1
  fi
}

echo "=== Starting Block: swe_lego_live ==="
echo ""

# ── Read remote config ────────────────────────────────────────────────────────
REMOTE_IP="$(cfg "meta_info.resources.ip")"
REMOTE_USER="$(cfg "meta_info.resources.user")"
REMOTE_DIR="$(cfg "meta_info.resources.directory")"

if [[ -z "$REMOTE_IP" || "$REMOTE_IP" == "null" ]]; then
  echo "ERROR: meta_info.resources.ip not set in subblock/swegen/config.yaml" >&2
  exit 1
fi
if [[ -z "$REMOTE_DIR" || "$REMOTE_DIR" == "null" ]]; then
  echo "ERROR: meta_info.resources.directory not set in subblock/swegen/config.yaml" >&2
  exit 1
fi

REMOTE_HOST="${REMOTE_USER}@${REMOTE_IP}"
REMOTE_REPO="${REMOTE_DIR%/}/SWE-Lego-Live"

echo "Remote node : ${REMOTE_HOST}"
echo "Remote path : ${REMOTE_REPO}"
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
    "${ROOT_DIR}/" "${REMOTE_HOST}:${REMOTE_REPO}/"
  echo "  synced to ${REMOTE_HOST}:${REMOTE_REPO}"
  echo ""
fi

# Managed repos are intentionally not rsynced with the Live source tree. They
# must be provisioned on the remote host before starting block sessions.
echo "Step 2b: Checking remote managed repos ..."
check_remote_git_repo \
  "SWE-gen" \
  "${REMOTE_REPO}/subblock/swegen/repos/swegen" \
  "Run on remote: mkdir -p '${REMOTE_REPO}/subblock/swegen/repos' && git clone https://github.com/SWE-Lego/SWE-Lego-Live-SWEgen.git '${REMOTE_REPO}/subblock/swegen/repos/swegen' && git -C '${REMOTE_REPO}/subblock/swegen/repos/swegen' checkout e804af92aad81f42928453959e24e3f5dc666c44"
check_remote_git_repo \
  "Harbor" \
  "${REMOTE_REPO}/subblock/trajgen/repos/harbor" \
  "Run on remote: cd '${REMOTE_REPO}/subblock/trajgen' && bash scripts/update_repos.sh"
echo ""

# ── 3. Start swegen ───────────────────────────────────────────────────────────
if [[ $START_SWEGEN -eq 1 ]]; then
  echo "Step 3: Starting swegen ..."
  SWEGEN_SESSION="swegen-py"
  SWEGEN_DIR="${REMOTE_REPO}/subblock/swegen"

  # Check if session already exists
  if [[ $DRY_RUN -eq 0 ]] && \
     ssh_run "$REMOTE_HOST" "tmux has-session -t '${SWEGEN_SESSION}'" 2>/dev/null; then
    echo "  [SKIP] tmux session '${SWEGEN_SESSION}' already exists on remote"
    echo "         attach with: ssh ${REMOTE_HOST} -t tmux attach -t ${SWEGEN_SESSION}"
  else
    SWEGEN_CMD="cd '${SWEGEN_DIR}' && bash scripts/create_py.sh"
    ssh_run "$REMOTE_HOST" \
      "tmux new-session -d -s '${SWEGEN_SESSION}' -x 220 -y 50 && \
       tmux send-keys -t '${SWEGEN_SESSION}' '${SWEGEN_CMD}' Enter"
    echo "  started tmux session '${SWEGEN_SESSION}'"
    echo "  attach with: ssh ${REMOTE_HOST} -t tmux attach -t ${SWEGEN_SESSION}"
  fi
  echo ""
fi

# ── 4. Start trajgen ──────────────────────────────────────────────────────────
if [[ $START_TRAJGEN -eq 1 ]]; then
  echo "Step 4: Starting trajgen ..."
  TRAJGEN_SESSION="trajgen"
  TRAJGEN_DIR="${REMOTE_REPO}/subblock/trajgen"

  if [[ $DRY_RUN -eq 0 ]] && \
     ssh_run "$REMOTE_HOST" "tmux has-session -t '${TRAJGEN_SESSION}'" 2>/dev/null; then
    echo "  [SKIP] tmux session '${TRAJGEN_SESSION}' already exists on remote"
    echo "         attach with: ssh ${REMOTE_HOST} -t tmux attach -t ${TRAJGEN_SESSION}"
  else
    TRAJGEN_CMD="cd '${TRAJGEN_DIR}' && bash scripts/prepare_tasks.sh && bash scripts/start.sh"
    ssh_run "$REMOTE_HOST" \
      "tmux new-session -d -s '${TRAJGEN_SESSION}' -x 220 -y 50 && \
       tmux send-keys -t '${TRAJGEN_SESSION}' '${TRAJGEN_CMD}' Enter"
    echo "  started tmux session '${TRAJGEN_SESSION}'"
    echo "  attach with: ssh ${REMOTE_HOST} -t tmux attach -t ${TRAJGEN_SESSION}"
  fi
  echo ""
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo "=== Done ==="
echo ""
echo "Monitor sessions on the remote node:"
echo "  ssh ${REMOTE_HOST}"
echo "  tmux ls"
if [[ $START_SWEGEN -eq 1 ]]; then
  echo "  tmux attach -t swegen-py    # swegen task generation"
fi
if [[ $START_TRAJGEN -eq 1 ]]; then
  echo "  tmux attach -t trajgen      # trajgen trajectory generation"
fi
