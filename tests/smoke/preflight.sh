#!/usr/bin/env bash
# Root-smoke RESOURCE preflight — run before any stage launches.
#
# run_pipeline.sh calls this as a HARD GATE right before the chain starts. It is
# separate from each block's own scripts/dryrun.sh (which validates config/repos/
# env): this one answers "does the hardware/network we are about to lean on
# actually have headroom right now?" — the failure modes that have actually
# bitten this smoke:
#   - GPU pod's 8 GPUs already held by someone else's job  -> sft OOM
#     (the pod is SHARED; we never kill others' work, we abort and tell the user)
#   - disk full on the CI runner or the pod's storage dir   -> mid-run write fail
#   - runner low on memory / Docker daemon down             -> swegen/trajgen die
#   - upstream LLM endpoint unreachable                     -> 0 verified tasks
#
# Scope-aware: GPU/pod checks only run when sft or eval is in the [from..to]
# window; Docker/LLM checks only when a stage that needs them is in-window.
#
# Usage:  bash tests/smoke/preflight.sh [<from-stage>] [<to-stage>]
#         (defaults: swegen .. eval — i.e. the whole chain)
#
# Exit 0 = clear to launch (FAILs == 0; WARNs are advisory).
# Exit 1 = do NOT launch (one or more hard checks failed).

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SFT_CFG="$ROOT_DIR/tests/smoke/sft/config.yaml"
SWEGEN_CFG="$ROOT_DIR/tests/smoke/swegen/config.yaml"
EVAL_CFG="$ROOT_DIR/tests/smoke/eval/config.yaml"

STAGES=(swegen trajgen sft eval)
FROM="${1:-swegen}"
TO="${2:-eval}"

# --- thresholds (override via env if a host legitimately needs different) ----
CI_DISK_MIN_GB="${CI_DISK_MIN_GB:-15}"      # hard floor on the runner workspace fs
CI_DISK_WARN_GB="${CI_DISK_WARN_GB:-40}"
CI_MEM_MIN_GB="${CI_MEM_MIN_GB:-2}"         # hard floor on available RAM
CI_MEM_WARN_GB="${CI_MEM_WARN_GB:-6}"
POD_DISK_MIN_GB="${POD_DISK_MIN_GB:-20}"    # checkpoints + datasets land here
POD_DISK_WARN_GB="${POD_DISK_WARN_GB:-60}"
GPU_FREE_USED_MIB="${GPU_FREE_USED_MIB:-4000}"  # a GPU with < this MiB in use is "available"

OK=0; WARN=0; FAIL=0
ok()   { echo "  [OK]   $1"; OK=$((OK+1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

cfg() {  # cfg <file> <dotted-key>
  python3 - "$1" "$2" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

in_window() {  # in_window <stage>
  local stage="$1" i from_i=-1 to_i=-1 s_i=-1
  for i in "${!STAGES[@]}"; do
    [[ "${STAGES[$i]}" == "$FROM" ]] && from_i=$i
    [[ "${STAGES[$i]}" == "$TO" ]] && to_i=$i
    [[ "${STAGES[$i]}" == "$stage" ]] && s_i=$i
  done
  (( s_i >= from_i && s_i <= to_i ))
}

echo "============================================================"
echo " ROOT SMOKE PREFLIGHT  (resource gate, window: $FROM..$TO)"
echo "============================================================"

# =================================================== CI runner (local host) ==
echo "[1] CI runner — disk / memory / Docker"

# disk on the workspace filesystem
DISK_AVAIL_KB="$(df -Pk "$ROOT_DIR" 2>/dev/null | awk 'NR==2{print $4}')"
if [[ -n "$DISK_AVAIL_KB" ]]; then
  DISK_GB=$(( DISK_AVAIL_KB / 1024 / 1024 ))
  if   (( DISK_GB < CI_DISK_MIN_GB ));  then fail "runner disk: ${DISK_GB}GB free < ${CI_DISK_MIN_GB}GB floor ($ROOT_DIR) — clean artifacts/docker before launching"
  elif (( DISK_GB < CI_DISK_WARN_GB )); then warn "runner disk: ${DISK_GB}GB free (< ${CI_DISK_WARN_GB}GB) — tight for a full chain"
  else ok "runner disk: ${DISK_GB}GB free on workspace fs"; fi
else
  warn "runner disk: could not read df for $ROOT_DIR"
fi

# available memory
MEM_AVAIL_KB="$(awk '/MemAvailable/{print $2}' /proc/meminfo 2>/dev/null)"
if [[ -n "$MEM_AVAIL_KB" ]]; then
  MEM_GB=$(( MEM_AVAIL_KB / 1024 / 1024 ))
  if   (( MEM_GB < CI_MEM_MIN_GB ));  then fail "runner memory: ${MEM_GB}GB available < ${CI_MEM_MIN_GB}GB floor"
  elif (( MEM_GB < CI_MEM_WARN_GB )); then warn "runner memory: ${MEM_GB}GB available (< ${CI_MEM_WARN_GB}GB)"
  else ok "runner memory: ${MEM_GB}GB available"; fi
else
  warn "runner memory: could not read /proc/meminfo"
fi

# Docker — swegen (image build), trajgen + eval (harbor containers) all need it
if in_window swegen || in_window trajgen || in_window eval; then
  if docker info >/dev/null 2>&1; then
    ok "docker daemon reachable"
  else
    fail "docker daemon NOT reachable (\`docker info\` failed) — swegen/trajgen/eval cannot run containers"
  fi
fi

# =========================================== upstream LLM endpoint (swegen) ===
if in_window swegen || in_window trajgen; then
  echo "[2] Upstream LLM endpoint (swegen/trajgen)"
  LLM_URL="$(cfg "$SWEGEN_CFG" runtime_info.input.llm_api.api_base_url)"
  if [[ -z "$LLM_URL" ]]; then
    warn "no llm_api.api_base_url in $SWEGEN_CFG"
  else
    # Reachability only: the real path goes through the local CC proxy (:4010),
    # and this endpoint is Cloudflare-gated — a 401/403/404/5xx still proves
    # DNS+TCP+TLS work, so anything that returns an HTTP code is OK; only a hard
    # connection/DNS failure (code 000) is a WARN (advisory, never blocks).
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 12 "${LLM_URL%/}/models" 2>/dev/null)"
    if [[ -n "$CODE" && "$CODE" != "000" ]]; then
      ok "LLM endpoint reachable: $LLM_URL (HTTP $CODE)"
    else
      warn "LLM endpoint did not respond: $LLM_URL (curl code ${CODE:-none}) — may be CF-gated from here; verify on the runner if swegen gets 0 verified"
    fi
  fi
fi

# ===================================================== GPU pod (sft / eval) ===
if in_window sft || in_window eval; then
  echo "[3] GPU pod — reachability / GPUs / disk / memory"
  R_IP="$(cfg "$SFT_CFG" meta_info.resources.ip)"
  R_USER="$(cfg "$SFT_CFG" meta_info.resources.user)"
  R_KEY="$(cfg "$SFT_CFG" meta_info.resources.key)"
  R_PORT="$(cfg "$SFT_CFG" meta_info.resources.port)"
  R_DIR="$(cfg "$SFT_CFG" meta_info.resources.directory)"

  if [[ -z "$R_IP" || "$R_IP" == "local" || "$R_IP" == "null" ]]; then
    ok "sft/eval configured local (ip=$R_IP) — no remote GPU pod to probe"
  else
    remote() {
      ssh -i "$R_KEY" -p "$R_PORT" \
          -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 \
          "$R_USER@$R_IP" "$@"
    }

    # How many GPUs does this window need? sft trains across n_gpus_per_node;
    # eval-only serving needs just the vLLM tensor-parallel size.
    REQ_GPUS=1
    if in_window sft; then
      N="$(cfg "$SFT_CFG" runtime_info.input.training.n_gpus_per_node)"; REQ_GPUS="${N:-8}"
    elif in_window eval; then
      N="$(cfg "$EVAL_CFG" runtime_info.input.serving.vllm.tensor_parallel_size)"; REQ_GPUS="${N:-1}"
    fi

    if ! remote "echo ok" >/dev/null 2>&1; then
      fail "GPU pod unreachable over SSH: $R_USER@$R_IP:$R_PORT (key $R_KEY) — sft/eval cannot run"
    else
      ok "GPU pod reachable: $R_USER@$R_IP:$R_PORT"

      # --- GPU availability (SHARED pod: count free GPUs, never kill others) ---
      GPU_CSV="$(remote "nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits" 2>/dev/null | tr -d '\r')"
      if [[ -z "$GPU_CSV" ]]; then
        fail "GPU pod: nvidia-smi returned nothing — driver/GPU problem"
      else
        TOTAL_GPUS=0; AVAIL_GPUS=0; BUSY=""
        while IFS=',' read -r idx used total; do
          idx="$(echo "$idx" | tr -d ' ')"; used="$(echo "$used" | tr -d ' ')"
          [[ -z "$idx" ]] && continue
          TOTAL_GPUS=$((TOTAL_GPUS+1))
          if [[ "$used" =~ ^[0-9]+$ ]] && (( used < GPU_FREE_USED_MIB )); then
            AVAIL_GPUS=$((AVAIL_GPUS+1))
          else
            BUSY="$BUSY #$idx(${used}MiB)"
          fi
        done <<< "$GPU_CSV"
        if (( AVAIL_GPUS >= REQ_GPUS )); then
          ok "GPU pod: ${AVAIL_GPUS}/${TOTAL_GPUS} GPUs free (need ${REQ_GPUS})${BUSY:+; busy:$BUSY}"
        else
          fail "GPU pod: only ${AVAIL_GPUS}/${TOTAL_GPUS} GPUs free, need ${REQ_GPUS} — someone else holds${BUSY:- GPUs}. Do NOT launch; the pod is SHARED, wait or coordinate (never batch-kill)."
        fi
      fi

      # --- pod disk on the storage directory ---
      if [[ -n "$R_DIR" ]]; then
        POD_DISK_KB="$(remote "df -Pk '$R_DIR' 2>/dev/null || df -Pk \$(dirname '$R_DIR') 2>/dev/null" | awk 'NR==2{print $4}' | tr -d '\r')"
        if [[ "$POD_DISK_KB" =~ ^[0-9]+$ ]]; then
          POD_DISK_GB=$(( POD_DISK_KB / 1024 / 1024 ))
          if   (( POD_DISK_GB < POD_DISK_MIN_GB ));  then fail "GPU pod disk: ${POD_DISK_GB}GB free < ${POD_DISK_MIN_GB}GB floor at $R_DIR — checkpoint write will fail"
          elif (( POD_DISK_GB < POD_DISK_WARN_GB )); then warn "GPU pod disk: ${POD_DISK_GB}GB free (< ${POD_DISK_WARN_GB}GB) at $R_DIR"
          else ok "GPU pod disk: ${POD_DISK_GB}GB free at $R_DIR"; fi
        else
          warn "GPU pod disk: could not read df for $R_DIR"
        fi
      fi

      # --- pod memory (advisory) ---
      POD_MEM_KB="$(remote "awk '/MemAvailable/{print \$2}' /proc/meminfo" 2>/dev/null | tr -d '\r')"
      if [[ "$POD_MEM_KB" =~ ^[0-9]+$ ]]; then
        POD_MEM_GB=$(( POD_MEM_KB / 1024 / 1024 ))
        if (( POD_MEM_GB < 8 )); then warn "GPU pod memory: ${POD_MEM_GB}GB available (low for data staging/tokenization)"
        else ok "GPU pod memory: ${POD_MEM_GB}GB available"; fi
      fi
    fi
  fi
fi

echo "============================================================"
echo " PREFLIGHT  OK: $OK   WARN: $WARN   FAIL: $FAIL"
echo "============================================================"
if (( FAIL > 0 )); then
  echo "ABORT: $FAIL hard check(s) failed — not clear to launch the smoke."
  exit 1
fi
echo "CLEAR TO LAUNCH${WARN:+ (with $WARN advisory warning(s))}."
exit 0
