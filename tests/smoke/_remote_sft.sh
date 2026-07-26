#!/usr/bin/env bash
# Internal helper for run_pipeline.sh stage 3 when trainer runs on a remote GPU pod.
#
# Stages the merged combined-LF dataset + the lowered smoke config onto the pod,
# launches scripts/start.sh there, polls the pod for train_results.json, and
# fetches it (plus trainer_state.json) back into the LOCAL trainer block so the
# local verify.sh sees the same artifact at the same path. The persisted
# checkpoint stays on the pod — serve_checkpoint.sh (evaluator stage) serves it there.
#
#   bash tests/smoke/_remote_sft.sh <sft_block_dir> <merged_rel> <budget> <dry_run>
#
# Reads the pod address from <sft_block_dir>/config.yaml meta_info.resources.

set -uo pipefail
FB="${1:?trainer block dir}"; MERGED_REL="${2:?merged rel path}"; BUDGET="${3:-3000}"; DRY="${4:-0}"
CFG="$FB/config.yaml"

cfg() { python3 - "$CFG" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

R_IP="$(cfg meta_info.resources.ip)"; R_USER="$(cfg meta_info.resources.user)"
R_KEY="$(cfg meta_info.resources.key)"; R_PORT="$(cfg meta_info.resources.port)"
R_DIR="$(cfg meta_info.resources.directory)"; OUT="$(cfg runtime_info.input.training.output_dir)"
REMOTE_SFT="$R_DIR/subblock/trainer"
# #3 fix: the pod SSH login is root, but the shared FS is owned by uid 1000 (the
# SAME uid as the runner's user, just named differently per host). If training
# runs as root it writes config.yaml runtime_info.output back as root:root 0600,
# which the runner (uid 1000) then cannot read — every later step dies on
# PermissionError. So we run start.sh AS THE REPO-OWNER uid via runuser, with
# HOME/HF_HOME/uv pointed at shared-FS (uid-1000) locations, so all writes come
# out owned by the runner's user. No post-hoc chown needed.

# This pod drops individual connections under load ("Connection closed by ...
# port 30977") while staying up. Every remote call therefore retries: a single
# refusal has already been mistaken for an unreachable host (probe), a failed
# training run (poll), and an unstageable config (scp). Retry here rather than
# at each call site so no future caller has to remember.
# Only a TRANSPORT failure is retried. ssh exits 255 when it cannot connect;
# any other code is the remote command's own result and must pass straight
# through — the poll loop below runs `grep -q status=failed`, whose normal
# answer is a non-zero "not found", and retrying that would add ~80s to every
# poll and eventually stall the stage.
_retry() {  # _retry <what> <transport_rc> <cmd...>
  local what="$1" trc="$2"; shift 2
  local t rc
  for t in 1 2 3 4 5; do
    "$@"; rc=$?
    [[ "$rc" -ne "$trc" ]] && return "$rc"
    [[ "$t" -lt 5 ]] && { echo "INFO: $what transport failure $t/5 — retrying in $((t * 8))s" >&2; sleep $((t * 8)); }
  done
  return "$rc"
}
_ssh_raw() { ssh -i "$R_KEY" -p "$R_PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 "$R_USER@$R_IP" "$@"; }
SSH() { _retry "ssh" 255 _ssh_raw "$@"; }
_scp_to_raw() { scp -i "$R_KEY" -P "$R_PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$1" "$R_USER@$R_IP:$2"; }
SCP_TO() { _retry "scp->pod" 1 _scp_to_raw "$1" "$2"; }
_scp_from_raw() { scp -i "$R_KEY" -P "$R_PORT" -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$R_USER@$R_IP:$1" "$2"; }
SCP_FROM() { _retry "scp<-pod" 1 _scp_from_raw "$1" "$2"; }

if [[ "$DRY" == 1 ]]; then
  echo "[DRY-RUN] scp $MERGED_REL + config.yaml -> $R_USER@$R_IP:$REMOTE_SFT ; ssh start.sh ; poll ; fetch train_results.json"
  exit 0
fi

# Probe SSH first — SKIP cleanly if the pod is unreachable. Retry: this pod
# refuses individual handshakes under load ("Connection closed by ... port
# 30977") while remaining perfectly reachable seconds later, so a single failed
# attempt is not evidence the host is down. Observed 2026-07-27: probe failed,
# a manual ssh with identical arguments succeeded immediately after.
# SSH() already retries transport failures, so one call is enough here.
if ! SSH 'echo ok' >/dev/null 2>&1; then
  echo "SKIP: trainer pod $R_IP unreachable over SSH after 5 attempts — cannot run remote training"
  exit 77
fi

# The pod SSH login is root, so SCP'd/created files land root-owned. Detect the
# repo-owner uid (== the runner's user over the shared FS) and hand it the staged
# inputs, then run training AS that uid — so config.yaml + artifacts come out
# owner-owned, never root-600-locked.
REMOTE_OWNER="$(SSH "stat -c %U '$R_DIR' 2>/dev/null" | tr -d '\r')"; REMOTE_OWNER="${REMOTE_OWNER:-root}"

echo "INFO: staging data + config onto $R_USER@$R_IP:$REMOTE_SFT (run-as uid: $REMOTE_OWNER)"
# Staging must be fatal: this script is `set -u` without `set -e`, so an
# unchecked scp/mkdir failure would fall through to the rm + remote launch below
# and train on a stale config/dataset left on the pod from an earlier run.
SSH "mkdir -p '$REMOTE_SFT/$(dirname "$MERGED_REL")' '$REMOTE_SFT/artifacts/logs' '$REMOTE_SFT/artifacts/model'" \
  || { echo "FAIL: could not create remote staging dirs on $R_IP"; exit 1; }
SCP_TO "$FB/$MERGED_REL" "$REMOTE_SFT/$MERGED_REL" \
  || { echo "FAIL: could not stage merged LF dataset to the pod"; exit 1; }
SCP_TO "$CFG" "$REMOTE_SFT/config.yaml" \
  || { echo "FAIL: could not stage trainer config to the pod"; exit 1; }
# Drop any stale run dir; give the staged inputs + artifact dirs to the run uid
# (SCP left them root-owned) so the non-root run can read config + write outputs.
# Hand ALL run-written artifact dirs to the run uid — not just model/logs: dataset
# registration writes artifacts/data/lf_data, LLaMA-Factory writes training_config,
# and any of these left root-owned by an EARLIER root-run would block the uid-1000
# run with PermissionError (root can chown here; the runner uid can't).
SSH "rm -rf '$REMOTE_SFT/artifacts/model/$OUT'; \
     chown '$REMOTE_OWNER' '$REMOTE_SFT/config.yaml' '$REMOTE_SFT/$MERGED_REL' 2>/dev/null || true; \
     chown -R '$REMOTE_OWNER' '$REMOTE_SFT/artifacts/logs' '$REMOTE_SFT/artifacts/model' \
        '$REMOTE_SFT/artifacts/data' '$REMOTE_SFT/artifacts/training_config' \
        '$REMOTE_SFT/artifacts/archives' '$REMOTE_SFT/artifacts/index.yaml' 2>/dev/null || true"

# Link the pod's prepared runtime into the block, exactly as the per-block CI
# smoke does (.github/scripts/sft_smoke_run.sh). Without this the pod has no uv
# env and no checked-out repos, so dryrun fails with "SFT uv python not found",
# "uv command not found" and empty repo HEADs, and training dies in ~30s. The
# runtime itself already exists on the pod — only the symlinks were missing,
# which is why this worked in CI and not here.
# SFT_REMOTE_RUNTIME_DIR lives in the private env file the CI runner already
# uses, outside the repo — same source .github/scripts/sft_smoke_run.sh reads.
for _envf in "${SFT_REMOTE_ENV:-}" /gpufs/haoli/cicd/shared/sft-remote.env; do
  [[ -n "$_envf" && -f "$_envf" ]] && { set -a; . "$_envf"; set +a; break; }
done
RUNTIME_DIR="${SFT_REMOTE_RUNTIME_DIR:-}"
if [[ -n "$RUNTIME_DIR" ]]; then
  SSH "set -e
    cd '$REMOTE_SFT'
    mkdir -p repos artifacts
    for l in artifacts/env repos/LLaMA-Factory repos/swe_data_process artifacts/data/examples; do
      src='$RUNTIME_DIR'/\$l
      # actions/checkout-style empty placeholders would swallow the link
      [ -L \"\$l\" ] && rm -f \"\$l\"
      # A real directory here is a husk from an earlier run: repos/* ends up
      # present but with no .git, so rev-parse returns empty and the pin check
      # fails with 'HEAD= does not match'. rmdir only clears an empty one, so
      # drop a git-less repo outright and let the link replace it.
      if [ -d \"\$l\" ] && [ ! -L \"\$l\" ]; then
        case \"\$l\" in
          repos/*) [ -d \"\$l/.git\" ] || rm -rf \"\$l\" ;;
          *) rmdir \"\$l\" 2>/dev/null || true ;;
        esac
      fi
      [ -e \"\$src\" ] && [ ! -e \"\$l\" ] && ln -s \"\$src\" \"\$l\" || true
    done
    ls -ld artifacts/env repos/LLaMA-Factory 2>/dev/null | sed 's/^/  linked: /'
  " || echo "WARN: could not link pod runtime from $RUNTIME_DIR"
else
  echo "WARN: SFT_REMOTE_RUNTIME_DIR unset — pod must already have artifacts/env and repos/"
fi

# Launcher runs on the pod AS the repo-owner uid: shared-FS HOME/HF_HOME + a uv
# the uid can execute (root's /root/.local/bin/uv is unreadable to a dropped uid;
# the runner user's uv on the shared FS is). Written on the shared FS so it is
# owner-owned and runuser can exec it — avoids nested SSH quoting.
UV_GUESS="$(dirname "$(dirname "$R_DIR")")/uv/bin/uv"
cat > "$FB/artifacts/.remote-launch.sh" <<EOF
#!/usr/bin/env bash
set -e
cd "$REMOTE_SFT" || exit 9
# HOME/caches on the running user's LOCAL disk, NOT the shared cpfs: with
# enable_liger_kernel the 8 ranks JIT-compile Triton kernels into ~/.triton/cache;
# on a fuse-mounted shared FS those concurrent .so writes/loads race and one rank
# dies with ImportError. Training OUTPUTS still land on the shared FS (uid-owned)
# via the block's own config paths — only the scratch caches go local.
MYHOME="\$(getent passwd "\$(id -un)" 2>/dev/null | cut -d: -f6)"
{ [ -n "\$MYHOME" ] && mkdir -p "\$MYHOME" 2>/dev/null && [ -w "\$MYHOME" ]; } || MYHOME="/tmp/root-smoke-home-\$(id -u)"
export HOME="\$MYHOME"; export TRITON_CACHE_DIR="\$HOME/.triton"; export HF_HOME="\$HOME/.hf"
mkdir -p "\$HOME" "\$HF_HOME" "\$TRITON_CACHE_DIR" artifacts/logs
for c in "\$(command -v uv 2>/dev/null)" "$UV_GUESS" "\$HOME/.local/bin/uv"; do
  if [ -n "\$c" ] && [ -x "\$c" ]; then export PATH="\$(dirname "\$c"):\$PATH"; break; fi
done
nohup bash scripts/start.sh > artifacts/logs/root-smoke-sft.log 2>&1 &
echo "REMOTE_PID=\$!"
EOF
chmod +x "$FB/artifacts/.remote-launch.sh"

RUN_AS=""; [[ "$REMOTE_OWNER" != "root" ]] && RUN_AS="runuser -u $REMOTE_OWNER --"
echo "INFO: launching remote start.sh as '$REMOTE_OWNER' (budget ${BUDGET}s)"
SSH "$RUN_AS bash '$REMOTE_SFT/artifacts/.remote-launch.sh'" \
  || { echo "FAIL: could not launch remote start.sh as $REMOTE_OWNER"; exit 1; }

echo "INFO: polling pod for train_results.json"
REMOTE_LOG="$REMOTE_SFT/artifacts/logs/root-smoke-sft.log"
START=$(date +%s); DEADLINE=$((START + BUDGET))
while (( $(date +%s) < DEADLINE )); do
  # Early-fail: start.sh's archive_run stamps "status=failed" on any error — don't
  # poll the whole budget for a train_results.json that will never appear.
  if SSH "grep -q 'status=failed' '$REMOTE_LOG' 2>/dev/null"; then
    echo "FAIL: remote training failed after $(( $(date +%s) - START ))s (archived status=failed). Tail:"
    SSH "tail -25 '$REMOTE_LOG' 2>/dev/null" || true
    exit 1
  fi
  if SSH "test -f '$REMOTE_SFT/artifacts/model/$OUT/train_results.json'" 2>/dev/null; then
    echo "INFO: remote train_results.json present after $(( $(date +%s) - START ))s — fetching"
    mkdir -p "$FB/artifacts/model/$OUT"
    SCP_FROM "$REMOTE_SFT/artifacts/model/$OUT/train_results.json" "$FB/artifacts/model/$OUT/train_results.json" || true
    SCP_FROM "$REMOTE_SFT/artifacts/model/$OUT/trainer_state.json" "$FB/artifacts/model/$OUT/trainer_state.json" || true
    exit 0
  fi
  sleep 30
done
echo "WARN: budget exhausted; train_results.json not present on pod. Tail of remote log:"
SSH "tail -25 '$REMOTE_SFT/artifacts/logs/root-smoke-sft.log' 2>/dev/null" || true
exit 0
