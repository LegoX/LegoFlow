#!/usr/bin/env bash
# CI smoke runner. Invoked by .github/workflows/ci.yml as:
#   bash cicd/smoke/run.sh <block> [budget_seconds]
#
# Three phases per block:
#   PREP   — block-specific staging (e.g. trajgen's prepare_tasks.sh).
#   LAUNCH — kick off the long-running smoke in the background. EVERY block
#            uses claude SDK as the launcher: a single narrow `claude -p`
#            prompt that says "run this exact bash command, confirm the
#            child process is alive, reply STARTED, exit". The bash command
#            is always wrapped in `nohup ... &` so the backgrounded smoke
#            survives claude's exit (and works around the 10-min Bash-tool
#            cap in headless mode). `--dangerously-skip-permissions` + the
#            prompt's "do not ask for confirmation" line bypass the
#            "check → confirm → run" step that the block CLAUDE.mds require
#            for interactive runs.
#   WAIT   — poll the filesystem for the block's terminal artifact every 30s
#            up to <budget> seconds. swegen/sft exit on the first artifact
#            (single output); trajgen/eval wait the full budget (multi-task
#            runs where exiting on the first result would skip trials 2-N).

set -uo pipefail

BLOCK="${1:?usage: bash cicd/smoke/run.sh <block> [budget_seconds]}"
BUDGET="${2:-1800}"

case "$BLOCK" in
  swegen)  TERMINAL_GLOB="artifacts/swe_tasks/py-cc-smoke/verifiable_tasks.txt"; WAIT_POLICY="first" ;;
  trajgen) TERMINAL_GLOB="artifacts/jobs/smoke/*/*/result.json"; WAIT_POLICY="full" ;;
  eval)    TERMINAL_GLOB="artifacts/jobs/smoke/*/result.json"; WAIT_POLICY="full" ;;
  sft)     TERMINAL_GLOB="artifacts/model/_smoke_train_ci/train_results.json"; WAIT_POLICY="first" ;;
  *) echo "FAIL: unknown block '$BLOCK'" >&2; exit 1 ;;
esac

REPO_ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
BLOCK_DIR="$REPO_ROOT/subblock/$BLOCK"
[[ -d "$BLOCK_DIR" ]] || { echo "FAIL: $BLOCK_DIR missing" >&2; exit 1; }

cd "$BLOCK_DIR"
mkdir -p artifacts/logs

# Read a dotted yaml path from the overlaid config.yaml.
cfg() {
  python3 - "$BLOCK_DIR/config.yaml" "$1" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1])) or {}
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

# Detect remote-execution mode (currently only used by sft). When
# meta_info.resources.ip is a real IP, the smoke runs on the remote host over
# SSH. The runner stays the orchestrator: launches via ssh, polls via ssh,
# scps the terminal artifact back into the local workspace so verify.sh keeps
# operating on the same paths it does for the local case.
REMOTE_IP=""
if [[ "$BLOCK" == "sft" ]]; then
  ip="$(cfg meta_info.resources.ip)"
  case "$ip" in
    ""|null|local) ;;  # local mode
    *)
      REMOTE_IP="$ip"
      REMOTE_USER="$(cfg meta_info.resources.user)"
      REMOTE_KEY="$(cfg meta_info.resources.key)"
      REMOTE_PORT="$(cfg meta_info.resources.port)"
      REMOTE_DIR="$(cfg meta_info.resources.directory)"
      ;;
  esac
fi

# Wrapper for an SSH call with the configured key/port/user/host.
remote() {
  ssh -i "$REMOTE_KEY" -p "$REMOTE_PORT" \
      -o StrictHostKeyChecking=accept-new \
      -o BatchMode=yes \
      -o ConnectTimeout=20 \
      "$REMOTE_USER@$REMOTE_IP" "$@"
}

# ---------- PREP ----------
case "$BLOCK" in
  eval)
    # The verifier requires a run-scoped start marker and must never accept a
    # result left by an earlier workspace. Fail immediately if root-owned
    # residue cannot be removed instead of polling stale output for 40 minutes.
    if ! rm -rf artifacts/jobs/smoke; then
      echo "FAIL: could not clear stale eval smoke results" >&2
      exit 1
    fi
    mkdir -p artifacts/jobs/smoke
    date +%s > artifacts/jobs/smoke/.run-start
    ;;
  sft)
    if [[ -n "$REMOTE_IP" ]]; then
      echo "INFO: sft remote mode → $REMOTE_USER@$REMOTE_IP:$REMOTE_PORT$REMOTE_DIR"
      if ! remote 'echo OK; nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | wc -l' >/tmp/sft-remote-probe 2>&1; then
        echo "WARNING: sft SSH probe failed — falling back to SKIP"
        cat /tmp/sft-remote-probe
        exit 0
      fi
      cat /tmp/sft-remote-probe
    else
      # Local mode and no GPU: SKIP fast so verify.sh maps to ::warning::
      # (the swe-lego-gpu runner isn't online and there's no remote SSH
      # config either).
      if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "INFO: sft SKIP-fast — no remote SSH config and nvidia-smi absent"
        exit 0
      fi
    fi
    ;;
esac

case "$BLOCK" in
  trajgen)
    echo "INFO: trajgen — running prepare_tasks.sh (dryrun.sh requires staged tasks)"
    if ! bash scripts/prepare_tasks.sh; then
      echo "FAIL: prepare_tasks.sh exited non-zero — terminal artifact will not appear"
    fi
    ;;
esac

case "$BLOCK" in
  trajgen|eval)
    # Warm the cpfs/aliyun-alinas-efc cache for harbor's CLI: the first import
    # takes ~20s (pydantic/asyncio cold pages). Warming keeps preflight and
    # launch latency predictable; the second invocation is ~9s.
    # Done AFTER prepare_tasks (which reads 200 task dirs and can evict
    # harbor's pages from the page cache) so the warm sticks until start.sh.
    if [[ -x artifacts/env/harbor-uv/bin/harbor ]]; then
      echo "INFO: warming harbor CLI cache (1/2)"
      artifacts/env/harbor-uv/bin/harbor --help >/dev/null 2>&1 || true
      echo "INFO: warming harbor CLI cache (2/2)"
      artifacts/env/harbor-uv/bin/harbor --help >/dev/null 2>&1 || true
    fi
    ;;
esac

# ---------- LAUNCH ----------
echo "INFO: launching $BLOCK smoke (budget ${BUDGET}s)"
LAUNCH_LOG="$BLOCK_DIR/artifacts/logs/smoke-launch.log"
: > "$LAUNCH_LOG"

# Common claude-SDK driver. Drives 3 gated phases per block:
#   1. setup    — env dirs + repos are populated (CI's "Link runtime state"
#                 should have materialized these; this phase is a fast
#                 confirmation, not a (re)provision).
#   2. check    — scripts/dryrun.sh exits 0 (config schema, endpoints, paths).
#   3. run      — backgrounds the long-running smoke via nohup; claude
#                 confirms the child process is alive, then exits.
# A failing earlier phase aborts the chain (claude replies FAILED <phase>
# and does NOT proceed). Caller passes:
#   $1 setup_check — bash that must exit 0 to clear phase 1
#   $2 preflight   — bash that must exit 0 to clear phase 2
#   $3 run_cmd     — the nohup-backgrounded launch
#   $4 pgrep_pat   — post-launch sanity (one of the child's argv must match)
#   $5 label       — printed back so claude's output is greppable
claude_launch() {
  local setup_check="$1" preflight="$2" run_cmd="$3" pgrep_pat="$4" label="$5"
  HOME=/home/haoli timeout 900 claude -p \
"CI smoke for ${label}. The smoke config has been overlaid at subblock/${BLOCK}/config.yaml. cwd is already subblock/${BLOCK}.

Run 3 gated phases via the Bash tool. STOP and reply FAILED <phase-number> with the last 20 lines of output if any phase exits non-zero. Do NOT proceed past a failure.

Phase 1 (setup — confirm env dirs + repos are healthy):
  ${setup_check}

Phase 2 (check — preflight: config schema, endpoints, paths):
  ${preflight}

Phase 3 (run — background the long-running smoke under nohup; the trailing & detaches it so Bash returns immediately):
  ${run_cmd}

After Phase 3, confirm exactly one matching child process is alive with: pgrep -af '${pgrep_pat}'

Reply STARTED <pid> on success, or FAILED <phase-number> <tail> on failure, then exit. Do not wait for completion; a separate bash poller watches for the terminal artifact. Do not ask for confirmation; this is a non-interactive CI run." \
    --dangerously-skip-permissions \
    --max-turns 30 2>&1 || true
}

# Generic phase-1 / phase-2 commands — same shape across all blocks since
# the CI runner pre-populates artifacts/env (or artifacts/envs) + repos/
# via the "Link runtime state" step, and every block ships a scripts/dryrun.sh
# that exits 0 iff the block is launch-ready.
SETUP_CHECK_LOCAL='( test -d artifacts/env || test -d artifacts/envs ) && test -d repos && echo "setup OK: $(ls -d artifacts/env* repos | tr "\n" " ")"'
PREFLIGHT_LOCAL='bash scripts/dryrun.sh'

case "$BLOCK" in
  swegen)
    # Drive via claude SDK. The smoke config is the source of truth — it
    # specifies the PR list (smoke.input_prs) and all swegen create flags
    # under runtime_info.input.smoke.*. We materialize the PR list into a
    # tempfile and read the flags via the `cfg` helper, then hand a single
    # composed swegen-create command to claude headless. Claude's job is
    # narrow: nohup the command in the background and exit (the headless
    # `claude -p` Bash tool caps at 10 min and has no harness callback, so
    # it cannot wait the 60-min budget). The bash WAIT phase below polls
    # the on-disk manifest.
    # Read from the schema-compatible smoke config:
    #   - runtime_info.output.swe_tasks_dir.path      (base dir, prod field)
    #   - runtime_info.input.smoke.output_subdir      (smoke-only)
    #   - runtime_info.input.smoke.state_subdir       (smoke-only)
    #   - runtime_info.input.languages.py.params.*    (prod field, smoke overrides)
    #   - runtime_info.input.smoke.{max_pr, min_source_files, max_source_files,
    #                               docker_prune_batch, input_prs}
    SWE_TASKS_BASE="$(cfg runtime_info.output.swe_tasks_dir.path)"
    SMOKE_OUT="$BLOCK_DIR/$SWE_TASKS_BASE/$(cfg runtime_info.input.smoke.output_subdir)"
    SMOKE_STATE="$BLOCK_DIR/$SWE_TASKS_BASE/$(cfg runtime_info.input.smoke.state_subdir)"
    SMOKE_IDS_FILE="$BLOCK_DIR/$SWE_TASKS_BASE/.swegen-smoke-input-prs.txt"
    mkdir -p "$SMOKE_OUT" "$SMOKE_STATE" "$(dirname "$SMOKE_IDS_FILE")"
    python3 - "$BLOCK_DIR/config.yaml" "$SMOKE_IDS_FILE" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
prs = (cfg.get("runtime_info",{}).get("input",{}).get("smoke",{}) or {}).get("input_prs") or []
with open(sys.argv[2], "w", encoding="utf-8") as fh:
    fh.write("\n".join(prs) + "\n")
PY
    SMOKE_MAX_PR="$(cfg runtime_info.input.smoke.max_pr)"
    SMOKE_NCONC="$(cfg runtime_info.input.languages.py.params.n_concurrent)"
    SMOKE_TO="$(cfg runtime_info.input.languages.py.params.timeout)"
    SMOKE_CCTO="$(cfg runtime_info.input.languages.py.params.cc_timeout)"
    SMOKE_MINSF="$(cfg runtime_info.input.smoke.min_source_files)"
    SMOKE_MAXSF="$(cfg runtime_info.input.smoke.max_source_files)"
    SMOKE_DPB="$(cfg runtime_info.input.smoke.docker_prune_batch)"
    echo "INFO: smoke PR list ($(wc -l <"$SMOKE_IDS_FILE") entries):"
    sed 's/^/         /' "$SMOKE_IDS_FILE"
    SMOKE_CMD="source scripts/load_runtime_env.sh && load_runtime_env >/dev/null 2>&1 ; source artifacts/envs/swegen-env/bin/activate ; nohup swegen create --input-ids-file ${SMOKE_IDS_FILE#${BLOCK_DIR}/} --max-pr ${SMOKE_MAX_PR} --n-concurrent ${SMOKE_NCONC} --output ${SMOKE_OUT#${BLOCK_DIR}/} --state-dir ${SMOKE_STATE#${BLOCK_DIR}/} --timeout ${SMOKE_TO} --cc-timeout ${SMOKE_CCTO} --no-require-issue --min-source-files ${SMOKE_MINSF} --max-source-files ${SMOKE_MAXSF} --docker-prune-batch ${SMOKE_DPB} --verbose >> artifacts/logs/smoke-launch.log 2>&1 &"
    claude_launch "$SETUP_CHECK_LOCAL" "$PREFLIGHT_LOCAL" "$SMOKE_CMD" \
                  "swegen create --input-ids-file" "swegen"
    LAUNCH_PID=$(pgrep -f 'swegen create --input-ids-file' | head -1)
    LAUNCH_PID=${LAUNCH_PID:-0}
    ;;
  trajgen|eval)
    # claude SDK drives 3 phases: setup-check, dryrun.sh, then nohup start.sh.
    # Phases gate each other; start.sh only launches if both earlier phases
    # exited 0. The trailing & on phase 3 detaches start.sh so claude can exit.
    SMOKE_CMD="nohup bash scripts/start.sh >> artifacts/logs/smoke-launch.log 2>&1 &"
    claude_launch "$SETUP_CHECK_LOCAL" "$PREFLIGHT_LOCAL" "$SMOKE_CMD" \
                  "bash scripts/start.sh" "$BLOCK"
    LAUNCH_PID=$(pgrep -f 'bash scripts/start.sh' | head -1)
    LAUNCH_PID=${LAUNCH_PID:-0}
    ;;
  sft)
    if [[ -n "$REMOTE_IP" ]]; then
      # Remote sft: all 3 phases run on the GPU host. Stage 3 small SSH
      # wrappers under artifacts/logs/ so claude only has to do 3 simple
      # Bash calls. Phase 1 also fast-forwards the remote checkout to the
      # current branch + overlays the smoke config (this IS the setup-step,
      # which is intentional: dryrun.sh in phase 2 must read the smoke
      # config, not whatever was on the remote before).
      BRANCH="${GITHUB_REF_NAME:-haoli/ci-cd}"
      REMOTE_LOG="$REMOTE_DIR/subblock/sft/artifacts/logs/smoke-launch.log"
      SETUP_SH="artifacts/logs/.smoke-remote-setup.sh"
      CHECK_SH="artifacts/logs/.smoke-remote-check.sh"
      RUN_SH="artifacts/logs/.smoke-remote-run.sh"

      _ssh_wrap() {
        cat <<EOS
#!/usr/bin/env bash
set -e
ssh -i '$REMOTE_KEY' -p '$REMOTE_PORT' \\
    -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20 \\
    '$REMOTE_USER@$REMOTE_IP' "$@"
EOS
      }
      # Phase 1: fast-forward + overlay + env sanity. Exits 0 if the env
      # dir exists and the smoke config is in place.
      _ssh_wrap "set -e; cd '$REMOTE_DIR'; /usr/bin/git.real fetch origin '$BRANCH' --quiet; /usr/bin/git.real reset --hard 'origin/$BRANCH' --quiet; cp subblock/sft/tests/smoke/config.yaml subblock/sft/config.yaml; mkdir -p subblock/sft/artifacts/logs; rm -rf subblock/sft/artifacts/model/_smoke_train_ci; test -d subblock/sft/artifacts/env && echo 'remote setup OK'" \
        > "$BLOCK_DIR/$SETUP_SH"

      # Phase 2: dryrun.sh on the remote sft dir.
      _ssh_wrap "set -e; cd '$REMOTE_DIR/subblock/sft'; PATH=/root/.local/bin:\\\$PATH bash scripts/dryrun.sh" \
        > "$BLOCK_DIR/$CHECK_SH"

      # Phase 3: backgrounded start.sh, SSH returns once disown completes.
      _ssh_wrap "set -e; cd '$REMOTE_DIR/subblock/sft'; PATH=/root/.local/bin:\\\$PATH nohup bash scripts/start.sh > '$REMOTE_LOG' 2>&1 & disown; echo \\\"REMOTE_LAUNCH_PID=\\\$!\\\"" \
        > "$BLOCK_DIR/$RUN_SH"

      chmod +x "$BLOCK_DIR/$SETUP_SH" "$BLOCK_DIR/$CHECK_SH" "$BLOCK_DIR/$RUN_SH"

      SETUP_CMD="bash $SETUP_SH"
      PREFLIGHT_CMD="bash $CHECK_SH"
      SMOKE_CMD="bash $RUN_SH 2>&1 | tee -a artifacts/logs/smoke-launch.log"
      claude_launch "$SETUP_CMD" "$PREFLIGHT_CMD" "$SMOKE_CMD" \
                    "REMOTE_LAUNCH_PID=" "sft (remote $REMOTE_IP)"
      LAUNCH_PID=0   # remote — nothing local to kill -0
    else
      SMOKE_CMD="nohup bash scripts/start.sh >> artifacts/logs/smoke-launch.log 2>&1 &"
      claude_launch "$SETUP_CHECK_LOCAL" "$PREFLIGHT_LOCAL" "$SMOKE_CMD" \
                    "bash scripts/start.sh" "sft (local)"
      LAUNCH_PID=$(pgrep -f 'bash scripts/start.sh' | head -1)
      LAUNCH_PID=${LAUNCH_PID:-0}
    fi
    ;;
esac

echo "STARTED $BLOCK smoke (launch_pid=$LAUNCH_PID, log=$LAUNCH_LOG)"
# Give it a beat to fail-fast if start.sh dies in its first second of execution.
sleep 5
if ! kill -0 "$LAUNCH_PID" 2>/dev/null; then
  echo "WARNING: launch_pid=$LAUNCH_PID exited within 5s. Tail of launch log:"
  tail -30 "$LAUNCH_LOG" 2>/dev/null || true
fi

# ---------- WAIT ----------
echo "INFO: polling for $TERMINAL_GLOB (budget ${BUDGET}s, policy=$WAIT_POLICY)"
START=$(date +%s)
DEADLINE=$((START + BUDGET))
LAST_COUNT=0

# When sft is running remotely, the terminal artifact lives on the GPU host.
# Poll the remote and scp the result.json + trainer_state.json back as soon
# as they appear so verify.sh (which runs on the local CI workspace) sees
# the same files at the same paths it would for a local run.
fetch_sft_remote_if_ready() {
  [[ -n "$REMOTE_IP" ]] || return 1
  remote "test -f '$REMOTE_DIR/subblock/sft/artifacts/model/_smoke_train_ci/train_results.json'" 2>/dev/null || return 1
  echo "INFO: remote train_results.json appeared — fetching"
  mkdir -p "$BLOCK_DIR/artifacts/model/_smoke_train_ci"
  scp -i "$REMOTE_KEY" -P "$REMOTE_PORT" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
      "$REMOTE_USER@$REMOTE_IP:$REMOTE_DIR/subblock/sft/artifacts/model/_smoke_train_ci/train_results.json" \
      "$BLOCK_DIR/artifacts/model/_smoke_train_ci/train_results.json" 2>&1 | tail -3
  scp -i "$REMOTE_KEY" -P "$REMOTE_PORT" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
      "$REMOTE_USER@$REMOTE_IP:$REMOTE_DIR/subblock/sft/artifacts/model/_smoke_train_ci/trainer_state.json" \
      "$BLOCK_DIR/artifacts/model/_smoke_train_ci/trainer_state.json" 2>&1 | tail -3 || true
  return 0
}

while (( $(date +%s) < DEADLINE )); do
  # Remote sft: poll over SSH and scp on success.
  if [[ -n "$REMOTE_IP" ]]; then
    if fetch_sft_remote_if_ready; then
      elapsed=$(($(date +%s) - START))
      echo "INFO: remote terminal artifact retrieved after ${elapsed}s"
      LAST_COUNT=1
      break
    fi
    sleep 30
    continue
  fi

  # Local: glob the workspace.
  # shellcheck disable=SC2086  # glob expansion is intentional
  if compgen -G "$TERMINAL_GLOB" > /dev/null; then
    count=$(ls $TERMINAL_GLOB 2>/dev/null | wc -l)
    if (( count != LAST_COUNT )); then
      elapsed=$(($(date +%s) - START))
      echo "INFO: ${count} terminal artifact(s) present at ${elapsed}s"
      LAST_COUNT=$count
    fi
    if [[ "$WAIT_POLICY" == "first" ]]; then
      break
    fi
  fi
  sleep 30
done

elapsed=$(($(date +%s) - START))
echo "INFO: budget exhausted (${elapsed}s); verify.sh will scan artifacts (count=$LAST_COUNT)"
if (( LAST_COUNT == 0 )); then
  echo "INFO: 0 artifacts — full launch log:"
  cat "$LAUNCH_LOG" 2>/dev/null || true
fi

# Harbor's claude-code agent docker containers write agent/sessions/ as
# root:root mode 700. cpfs root-squashes chown from inside a docker
# container so we can't fix ownership — but docker-root CAN delete those
# dirs IF no harbor trial container is still writing to them.
#
# Step 1: kill any harbor-trial-* containers still alive — Harbor pipes
# SIGTERM up the bash chain unreliably so the budget exhaustion above
# often leaves trials running, and they hold writes to agent/sessions
# that defeat rm.
# Step 2: nuke agent/sessions (verify.sh only reads result.json + the
# agent/litellm-trajectory.jsonl which sits beside it; we don't need LLM
# session state).
# Step 3: best-effort chown of any other root-owned residue.
if command -v docker >/dev/null 2>&1; then
  echo "INFO: cleanup — killing surviving harbor trial containers"
  # Harbor names trial containers <task>__<random>-<role>-<n> (role in main, nop,
  # oracle, init), not "harbor-trial-*". Match that pattern.
  docker ps --format "{{.Names}}" 2>/dev/null \
    | grep -E "__[a-z0-9_-]+-(main|nop|oracle|init)-[0-9]+$" \
    | head -200 | xargs -r docker rm -f >/dev/null 2>&1 || true
  sleep 2  # let docker daemon release the bind mounts
  echo "INFO: cleanup — removing root-owned agent/sessions + chowning leftovers"
  docker run --rm -v "$BLOCK_DIR/artifacts:/x:rw" alpine:3 \
    sh -c "find /x -type d -name sessions -path '*agent/sessions' -exec rm -rf {} + 2>/dev/null; find /x ! -user 1000 -exec chown 1000:1000 {} + 2>/dev/null; find /x -type d -exec chmod u+rx {} + 2>/dev/null; find /x -type f -exec chmod u+r {} + 2>/dev/null" || true
fi

# Remote sft: drop the throwaway _smoke_train_ci run dir + dataset entry.
# verify.sh's trap does the equivalent on its local copy; we mirror it here
# for the GPU host so successive CI runs don't accumulate stale state.
if [[ "$BLOCK" == "sft" && -n "$REMOTE_IP" ]]; then
  echo "INFO: cleanup — remote run dir + dataset_info entry"
  remote "
    cd '$REMOTE_DIR/subblock/sft' || exit 0
    rm -rf artifacts/model/_smoke_train_ci
    python3 - <<'PY' 2>/dev/null || true
import fcntl, json, os
from pathlib import Path
p = Path('artifacts/data/lf_data/dataset_info.json')
if p.is_file():
    lock = p.with_suffix(p.suffix + '.lock')
    with open(lock, 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        info = json.loads(p.read_text(encoding='utf-8') or '{}')
        if info.pop('_smoke_train_ci', None) is not None:
            p.write_text(json.dumps(info, indent=4, ensure_ascii=False) + '\n', encoding='utf-8')
PY
  " 2>/dev/null || true
fi

exit 0
