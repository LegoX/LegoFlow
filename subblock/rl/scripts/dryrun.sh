#!/bin/bash
# Health-check: verify configs, paths, and GPU availability before launching training.
set -e

BLOCK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$BLOCK_DIR/repos/harbor-verl-train"
CONFIG="$BLOCK_DIR/config.yaml"

cfg() {
    python3 - "$CONFIG" "$1" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
for k in sys.argv[2].split("."):
    v = v[k] if v is not None and k in v else None
    if v is None: break
print(v if v is not None else "")
PY
}
abspath() {
    local p="$1"
    if [[ -z "$p" ]]; then echo ""; elif [[ "$p" = /* ]]; then echo "$p"; else echo "$BLOCK_DIR/$p"; fi
}

ok=0; missing=0
check() {
    local label="$1" path="$2"
    if [[ -z "$path" ]]; then
        echo "  EMPTY    $label"; missing=$((missing+1))
    elif [ -e "$path" ]; then
        echo "  OK       $label  ($path)"; ok=$((ok+1))
    else
        echo "  MISSING  $label  ($path)"; missing=$((missing+1))
    fi
}
# Generic emitters for non-file checks (pin drift, reachability, editable paths).
# FAIL is counted like MISSING; WARN is informational and never blocks.
pass_line() { echo "  OK       $1"; ok=$((ok+1)); }
fail_line() { echo "  FAIL     $1"; missing=$((missing+1)); }

# --- shared block-contract validation (schema, deps, fill markers) ------------
REPO_ROOT="$(cd "$BLOCK_DIR/../.." && pwd)"
if [[ -f "$REPO_ROOT/scripts/validate_config.py" ]]; then
  if VAL_OUT="$(python3 "$REPO_ROOT/scripts/validate_config.py" --block "$BLOCK_DIR" 2>&1)"; then
    echo "  OK       validate_config: block contract OK"; ok=$((ok+1))
  else
    echo "$VAL_OUT" | sed 's/^/    /'
    fail_line "validate_config reported failures (see lines above)"
  fi
else
  echo "  WARN     shared validator not found — skipping contract validation"
fi
warn_line() { echo "  WARN     $1"; }

echo "[rl/dryrun] config.yaml is parseable..."
python3 -c "import yaml; yaml.safe_load(open('$CONFIG'))" && echo "  OK       $CONFIG"

echo "[rl/dryrun] Submodule repos present..."
check "harbor-verl-train" "$REPO"
check "harbor"            "$BLOCK_DIR/repos/harbor"
check "verl"              "$BLOCK_DIR/repos/verl"

echo "[rl/dryrun] Submodule pinned commits..."
while IFS='|' read -r rname rpin; do
    [[ -z "$rname" ]] && continue
    d="$BLOCK_DIR/repos/$rname"
    if ! git -C "$d" rev-parse --git-dir >/dev/null 2>&1; then
        fail_line "$rname not a git repo — git submodule update --init repos/$rname"
        continue
    fi
    head="$(git -C "$d" rev-parse HEAD 2>/dev/null || echo "")"
    if [[ -z "$rpin" ]]; then
        br="$(git -C "$d" symbolic-ref --short -q HEAD 2>/dev/null || echo DETACHED)"
        pass_line "$rname branch-tracking @ ${head:0:9} ($br)"
    elif [[ "$head" == "$rpin"* ]]; then
        pass_line "$rname pin-ok $rpin -> ${head:0:13}"
    else
        fail_line "$rname pin-drift: config $rpin -> HEAD ${head:0:9}  (git -C repos/$rname checkout $rpin)"
    fi
done < <(python3 - "$CONFIG" <<'PY'
import sys, yaml
repos = ((yaml.safe_load(open(sys.argv[1])).get('meta_info') or {}).get('repos') or {})
for _, m in repos.items():
    m = m or {}
    print(f"{m.get('name','')}|{m.get('pinned_commit','')}")
PY
)

echo "[rl/dryrun] Upstream launch script..."
check "sync_1node_cc.sh" "$REPO/scripts/sync_1node_cc.sh"
check "verl_patch config" "$REPO/src/verl_patch/config/harbor_verl_sync.yaml"
check "agent_loop_config_cc" "$REPO/src/verl_patch/config/agent_loop_config_cc.yaml"

echo "[rl/dryrun] Launch script syntax (bash -n)..."
for s in "$BLOCK_DIR/scripts/train_1node_cc.sh" "$REPO/scripts/sync_1node_cc.sh"; do
    [[ -f "$s" ]] || continue
    if synerr="$(bash -n "$s" 2>&1)"; then
        pass_line "syntax ok: $(basename "$s")"
    else
        fail_line "syntax error in $(basename "$s"): $(printf '%s' "$synerr" | tr '\n' ' ' | cut -c1-140)"
    fi
done

echo "[rl/dryrun] Python venv..."
VENV_FROM_CFG="$(abspath "$(cfg runtime_info.input.environment.venv_path)")"
if [[ -n "$VENV_FROM_CFG" ]]; then
    VENV_PATH="$VENV_FROM_CFG"
    VENV_MODE="custom"
else
    VENV_PATH="$REPO/.venv"
    VENV_MODE="default"
fi
check "venv/bin/python ($VENV_MODE)" "$VENV_PATH/bin/python"

if [[ -x "$VENV_PATH/bin/python" ]]; then
    echo "[rl/dryrun] venv editable-install paths..."
    EDITABLE="$("$VENV_PATH/bin/python" - "$BLOCK_DIR/repos" <<'PY' 2>/dev/null || echo "IMPORT_ERROR unknown"
import os, sys
import importlib.util as iu
# Resolve where each editable module WOULD load from, without executing it.
# find_spec locates the package and returns its origin/search-path but does NOT
# run __init__.py — so `import verl` never drags in torch/vllm. Sub-second vs minutes.
roots = os.path.realpath(sys.argv[1])
def loc(spec):
    if spec is None:
        return None
    if spec.origin and spec.origin not in ("namespace", "built-in", "frozen"):
        return spec.origin
    locs = list(spec.submodule_search_locations or [])   # namespace pkg → its dir
    return locs[0] if locs else None
mods = ["harbor", "verl", "verl_patch", "harbor_patch"]
notfound, bad = [], []
for n in mods:
    try:
        p = loc(iu.find_spec(n))
    except Exception as e:
        print("IMPORT_ERROR", f"{n}: {type(e).__name__}: {str(e)[:100]}"); sys.exit(0)
    if not p:
        notfound.append(n)
    elif not os.path.realpath(p).startswith(roots):
        bad.append(n)
if notfound:
    print("IMPORT_ERROR", "not found: " + ",".join(notfound)); sys.exit(0)
print("OUTSIDE" if bad else "OK", ",".join(bad))
PY
)"
    if [[ "$EDITABLE" == IMPORT_ERROR* ]]; then
        fail_line "venv import error ($VENV_MODE): ${EDITABLE#IMPORT_ERROR } — re-run setup_env.sh"
    elif [[ "$EDITABLE" == OUTSIDE* ]]; then
        if [[ "$VENV_MODE" == custom ]]; then
            warn_line "venv ($VENV_MODE) editable modules resolve OUTSIDE repos/: ${EDITABLE#OUTSIDE } — verify this is intended"
        else
            fail_line "venv editable modules resolve OUTSIDE repos/: ${EDITABLE#OUTSIDE } — re-run setup_env.sh"
        fi
    else
        pass_line "venv editable installs resolve under repos/ ($VENV_MODE)"
    fi
fi

echo "[rl/dryrun] Input paths from config.yaml..."
check "model_path"            "$(abspath "$(cfg runtime_info.input.model.model_path)")"
check "train_index"           "$(abspath "$(cfg runtime_info.input.data.train_index)")"
check "val_index"             "$(abspath "$(cfg runtime_info.input.data.val_index)")"
check "trajectory_logger_src" "$(abspath "$(cfg runtime_info.input.experiment.trajectory_logger_src)")"

ENV_IMPORT="$(cfg runtime_info.input.harbor_agent.environment_import_path)"
DOCKER_HOST_CFG="$(cfg runtime_info.input.harbor_agent.docker_host)"

if [[ "$ENV_IMPORT" == *"docker"* ]]; then
    echo "[rl/dryrun] Environment: Docker mode"
    if [[ "$DOCKER_HOST_CFG" == tcp://* ]]; then
        echo "  REMOTE   docker_host=$DOCKER_HOST_CFG"
        DOCKER_IP=$(echo "$DOCKER_HOST_CFG" | sed -E 's|^tcp://||; s|:.*||')
        DOCKER_PORT=$(echo "$DOCKER_HOST_CFG" | sed -E 's|.*:||')
        if [[ "$DOCKER_PORT" == "2375" ]]; then
            echo "  WARN     port 2375 is unencrypted (root-equivalent). Consider TLS on :2376."
        fi
        if timeout 2 bash -c "echo >/dev/tcp/$DOCKER_IP/$DOCKER_PORT" 2>/dev/null; then
            echo "  OK       remote Docker daemon reachable"; ok=$((ok+1))
        else
            echo "  MISSING  cannot reach $DOCKER_HOST_CFG (firewall or daemon not running)"; missing=$((missing+1))
        fi
    elif [[ "$DOCKER_HOST_CFG" == unix://* ]]; then
        SOCK="${DOCKER_HOST_CFG#unix://}"
        echo "  LOCAL    docker_host=$DOCKER_HOST_CFG"
        if [[ -S "$SOCK" ]]; then
            echo "  OK       socket exists: $SOCK"; ok=$((ok+1))
        else
            echo "  MISSING  socket not found: $SOCK"; missing=$((missing+1))
        fi
    else
        # Empty or unrecognized → local Docker daemon via default socket
        echo "  LOCAL    docker_host is empty — Docker defaults to unix:///var/run/docker.sock"
        if command -v docker >/dev/null 2>&1; then
            if docker info >/dev/null 2>&1; then
                echo "  OK       docker daemon is running"; ok=$((ok+1))
            else
                echo "  MISSING  docker daemon not running (try: systemctl start docker)"; missing=$((missing+1))
            fi
        else
            echo "  MISSING  docker CLI not found"; missing=$((missing+1))
        fi
    fi
    # Harbor manages containers via the Python docker SDK (docker.DockerClient),
    # not the CLI — verify it imports and connects from the venv.
    SDK_BASE="${DOCKER_HOST_CFG:-unix:///var/run/docker.sock}"
    if [[ -x "$VENV_PATH/bin/python" ]]; then
        if SDK_OUT="$("$VENV_PATH/bin/python" -c "from docker import DockerClient; print(DockerClient(base_url='$SDK_BASE').info()['ServerVersion'])" 2>&1)"; then
            pass_line "docker SDK ok (server $SDK_OUT, base=$SDK_BASE)"
        else
            fail_line "docker SDK check failed: $(printf '%s' "$SDK_OUT" | tail -1) — uv pip install --python $VENV_PATH/bin/python docker (watch for a docker/ dir on sys.path shadowing it)"
        fi
    else
        warn_line "venv python missing — docker SDK import not checked yet (setup_env.sh creates it)"
    fi
else
    echo "[rl/dryrun] Environment: K8s mode"
    KUBECONFIG_PATH="$(abspath "$(cfg runtime_info.input.k8s.kubeconfig)")"
    check "k8s.kubeconfig"    "$KUBECONFIG_PATH"
    if [[ -f "$KUBECONFIG_PATH" ]] && command -v kubectl >/dev/null 2>&1; then
        if KUBECONFIG="$KUBECONFIG_PATH" kubectl get nodes --no-headers --request-timeout=10s >/tmp/_dryrun_nodes 2>/tmp/_dryrun_kerr; then
            nready=$(grep -cw Ready /tmp/_dryrun_nodes 2>/dev/null || echo 0)
            if [[ "$nready" -ge 1 ]]; then
                pass_line "k8s reachable ($nready node(s) Ready)"
            else
                fail_line "k8s reachable but 0 nodes Ready"
            fi
        else
            fail_line "k8s unreachable: $(tr '\n' ' ' </tmp/_dryrun_kerr | cut -c1-160)"
        fi
        rm -f /tmp/_dryrun_nodes /tmp/_dryrun_kerr
        if KUBECONFIG="$KUBECONFIG_PATH" kubectl auth can-i create pods --request-timeout=10s >/dev/null 2>&1; then
            pass_line "k8s RBAC: can create pods"
        else
            fail_line "k8s RBAC: cannot create pods — Harbor needs pod create/delete; check role bindings"
        fi
    elif [[ -f "$KUBECONFIG_PATH" ]]; then
        warn_line "kubectl not installed on this host — cluster reachability not checked (pod-side uses in-cluster kubectl)"
    fi
fi

echo "[rl/dryrun] Host port occupancy (head node)..."
for spec in "ray:$(cfg runtime_info.input.infrastructure.ray_port)" \
            "dashboard:$(cfg runtime_info.input.infrastructure.ray_dashboard_port)" \
            "litellm:$(cfg runtime_info.input.infrastructure.litellm_port)"; do
    svc="${spec%%:*}"; P="${spec##*:}"; P="${P:-?}"
    portline="$(ss -lntp 2>/dev/null | awk -v p=":${P}\$" '$4 ~ p {print}' | head -1)"
    if [[ -z "$portline" ]]; then
        pass_line "port $P free ($svc)"
    else
        pcomm="$(printf '%s' "$portline" | grep -oP 'users:\(\("\K[^"]+' | head -1)"
        ppid="$(printf '%s' "$portline" | grep -oP 'pid=\K[0-9]+' | head -1)"
        warn_line "port $P in use by ${pcomm:-?} pid ${ppid:-?} ($svc) — /block:check classifies current-run vs conflict"
    fi
done

echo "[rl/dryrun] Output dir write permissions..."
for d in "$REPO/logs" "$REPO/checkpoints"; do
    probe="$d"; [[ -d "$probe" ]] || probe="$(dirname "$probe")"
    if [[ -w "$probe" ]]; then
        pass_line "writable: ${d#"$BLOCK_DIR"/}"
    else
        fail_line "not writable: ${d#"$BLOCK_DIR"/} — need write access to create logs/checkpoints"
    fi
done

echo "[rl/dryrun] WANDB_API_KEY..."
WANDB_FROM_CFG="$(cfg runtime_info.input.credentials.wandb_api_key)"
WANDB_FROM_ENV="${WANDB_API_KEY:-}"
WANDB_MODE_CFG="$(cfg runtime_info.input.credentials.wandb_mode)"
if [[ "$WANDB_MODE_CFG" == "disabled" || "$WANDB_MODE_CFG" == "offline" ]]; then
    echo "  OK       wandb_mode=$WANDB_MODE_CFG — key not required"
elif [[ -n "$WANDB_FROM_CFG" || -n "$WANDB_FROM_ENV" ]]; then
    echo "  OK       wandb_api_key set ($([[ -n "$WANDB_FROM_CFG" ]] && echo config || echo env))"
else
    echo "  MISSING  WANDB_API_KEY — wandb_mode=online needs a key. export it in your shell before launch (do NOT hardcode it in config.yaml). To opt out, set credentials.wandb_mode: disabled (or offline)."
    missing=$((missing+1))
fi

echo "[rl/dryrun] GPU availability..."
if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv,noheader | sed 's/^/  /'
    # #8 enough GPUs present vs ngpus_per_node
    NGPU_PRESENT="$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l)"
    NGPU_REQ="$(cfg runtime_info.input.infrastructure.ngpus_per_node)"
    if [[ -n "$NGPU_REQ" ]]; then
        if [[ "$NGPU_PRESENT" -ge "$NGPU_REQ" ]]; then
            pass_line "GPU count $NGPU_PRESENT >= ngpus_per_node $NGPU_REQ"
        else
            fail_line "GPU count $NGPU_PRESENT < ngpus_per_node $NGPU_REQ — not enough GPUs to launch"
        fi
    fi
    # #4/#6 GPUs busy? list compute processes — /block:check classifies mine vs foreign
    apps="$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null || true)"
    if [[ -z "$apps" ]]; then
        pass_line "GPUs idle (no compute processes)"
    else
        while IFS=, read -r gpid gmem; do
            gpid="$(printf '%s' "$gpid" | tr -dc '0-9')"; gmem="$(printf '%s' "$gmem" | xargs)"
            [[ -z "$gpid" ]] && continue
            gcomm="$(ps -p "$gpid" -o comm= 2>/dev/null | xargs || true)"
            warn_line "gpu in use by pid $gpid (${gcomm:-?}) ${gmem} — /block:check classifies mine vs foreign"
        done <<< "$apps"
    fi
else
    echo "  nvidia-smi not available"
fi

echo "[rl/dryrun] Disk space for checkpoints..."
CKPT_PROBE="$REPO/checkpoints"; [[ -d "$CKPT_PROBE" ]] || CKPT_PROBE="$REPO"
DISK_AVAIL_GB="$(df -BG --output=avail "$CKPT_PROBE" 2>/dev/null | tail -1 | tr -dc '0-9')"
if [[ -n "$DISK_AVAIL_GB" ]]; then
    if [[ "$DISK_AVAIL_GB" -lt 50 ]]; then
        warn_line "only ${DISK_AVAIL_GB}G free on checkpoints volume — FSDP shards are large; consider freeing space"
    else
        pass_line "checkpoints volume has ${DISK_AVAIL_GB}G free"
    fi
fi

echo "[rl/dryrun] Cache / temp residue..."
residue=()
[[ -n "$(ls -A /tmp/ray 2>/dev/null || true)" ]] && residue+=("/tmp/ray")
compgen -G "/tmp/litellm_cc_*" >/dev/null 2>&1 && residue+=("/tmp/litellm_cc_*")
[[ -L /tmp/trajectory_logger.py ]] && residue+=("/tmp/trajectory_logger.py")
[[ -n "$(ls -A "$REPO/harbor_trials" 2>/dev/null || true)" ]] && residue+=("repos/harbor-verl-train/harbor_trials/")
if [[ ${#residue[@]} -eq 0 ]]; then
    pass_line "no leftover ray/litellm/trials cache"
else
    warn_line "leftover cache present: ${residue[*]} — run scripts/clean.sh (add --trials to clear harbor_trials)"
fi

KV_HEADS_FILE="$(abspath "$(cfg runtime_info.input.model.model_path)")/config.json"
GEN_TP="$(cfg runtime_info.input.vllm.gen_tp)"
if [[ -f "$KV_HEADS_FILE" && -n "$GEN_TP" ]]; then
    KV_HEADS=$(python3 -c "import json; print(json.load(open('$KV_HEADS_FILE'))['num_key_value_heads'])" 2>/dev/null || echo "")
    if [[ -n "$KV_HEADS" ]]; then
        if (( KV_HEADS % GEN_TP == 0 )); then
            echo "[rl/dryrun] vllm.gen_tp=$GEN_TP divides num_key_value_heads=$KV_HEADS — OK"
        else
            echo "[rl/dryrun] WARN: vllm.gen_tp=$GEN_TP does NOT divide num_key_value_heads=$KV_HEADS"
            echo "           This will crash with CUDA illegal memory access at first forward pass."
            missing=$((missing+1))
        fi
    fi
fi

echo
echo "================================================================"
echo "[rl/dryrun] Run Configuration Summary"
echo "================================================================"
echo "  Model:        $(cfg runtime_info.input.model.model_path)"
echo "  Served as:    $(cfg runtime_info.input.model.served_model_name)"
echo "  Train data:   $(cfg runtime_info.input.data.train_index)"
echo "  Val data:     $(cfg runtime_info.input.data.val_index)"
echo "  Backend:      $([[ "$ENV_IMPORT" == *docker* ]] && echo "Docker ($DOCKER_HOST_CFG)" || echo "K8s ($(cfg runtime_info.input.k8s.kubeconfig))")"
echo "  Parallelism:  $(cfg runtime_info.input.harbor_runtime.num_workers) workers"
echo "  Batch size:   $(cfg runtime_info.input.training.train_batch_size) prompts × $(cfg runtime_info.input.training.n_resp_per_prompt) responses = $(( $(cfg runtime_info.input.training.train_batch_size) * $(cfg runtime_info.input.training.n_resp_per_prompt) )) trials/step"
echo "  Context:      prompt=$(cfg runtime_info.input.training.max_prompt_length) + response=$(cfg runtime_info.input.training.max_response_length)"
echo "  vLLM:         TP=$(cfg runtime_info.input.vllm.gen_tp)  max_model_len=$(cfg runtime_info.input.vllm.max_model_length)  gpu_mem=$(cfg runtime_info.input.vllm.gpu_memory_utilization)"
echo "  Algorithm:    $(cfg runtime_info.input.algorithm.adv_estimator) / $(cfg runtime_info.input.algorithm.policy_loss_mode)  lr=$(cfg runtime_info.input.algorithm.learning_rate)"
echo "  Epochs:       $(cfg runtime_info.input.training.total_epochs)  save_freq=$(cfg runtime_info.input.training.save_freq)  test_freq=$(cfg runtime_info.input.training.test_freq)"
echo "  Experiment:   project=$(cfg runtime_info.input.experiment.project_name)  exp=$(cfg runtime_info.input.experiment.exp_name || echo '<auto>')"
echo "  wandb:        $(cfg runtime_info.input.credentials.wandb_mode)"
echo "  Agent:        $(cfg runtime_info.input.harbor_agent.agent_name)  timeout=$(cfg runtime_info.input.harbor_agent.max_timeout_sec)s  retries=$(cfg runtime_info.input.harbor_agent.max_retries)"
echo "================================================================"
echo
echo "[rl/dryrun] Done. ok=$ok missing/empty=$missing"
[[ $missing -eq 0 ]] || exit 1
