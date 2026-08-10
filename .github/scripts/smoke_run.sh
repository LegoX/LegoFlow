#!/usr/bin/env bash
# CI smoke runner. Invoked by .github/workflows/ci.yml as:
#   bash cicd/smoke/run.sh <block> [budget_seconds]
#
# Three phases per block:
#   PREP   — block-specific staging (e.g. tracer's prepare_tasks.sh).
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
#            up to <budget> seconds. curator/trainer exit on the first artifact
#            (single output); tracer/evaluator wait the full budget (multi-task
#            runs where exiting on the first result would skip trials 2-N).

set -uo pipefail

BLOCK="${1:?usage: bash cicd/smoke/run.sh <block> [budget_seconds]}"
BUDGET="${2:-1800}"

case "$BLOCK" in
  curator)  TERMINAL_GLOB="artifacts/swe_tasks/py-cc-smoke/verifiable_tasks.txt"; WAIT_POLICY="first" ;;
  tracer) TERMINAL_GLOB="artifacts/jobs/smoke/*/*/result.json"; WAIT_POLICY="full" ;;
  evaluator)    TERMINAL_GLOB="artifacts/jobs/smoke/*/result.json"; WAIT_POLICY="full" ;;
  trainer)     TERMINAL_GLOB="artifacts/model/_smoke_train_ci/train_results.json"; WAIT_POLICY="first" ;;
  *) echo "FAIL: unknown block '$BLOCK'" >&2; exit 1 ;;
esac

REPO_ROOT="${GITHUB_WORKSPACE:-$(pwd)}"
BLOCK_DIR="$REPO_ROOT/blocks/$BLOCK"
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

# Detect remote-execution mode (currently only used by trainer). When
# meta_info.resources.ip is a real IP, the smoke runs on the remote host over
# SSH. The runner stays the orchestrator: launches via ssh, polls via ssh,
# scps the terminal artifact back into the local workspace so verify.sh keeps
# operating on the same paths it does for the local case.
REMOTE_IP=""
if [[ "$BLOCK" == "trainer" ]]; then
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
  evaluator)
    # The verifier requires a run-scoped start marker and must never accept a
    # result left by an earlier workspace. Fail immediately if root-owned
    # residue cannot be removed instead of polling stale output for 40 minutes.
    if ! rm -rf artifacts/jobs/smoke; then
      echo "FAIL: could not clear stale evaluator smoke results" >&2
      exit 1
    fi
    mkdir -p artifacts/jobs/smoke
    date +%s > artifacts/jobs/smoke/.run-start
    ;;
  trainer)
    if [[ -n "$REMOTE_IP" ]]; then
      echo "INFO: trainer remote mode → $REMOTE_USER@$REMOTE_IP:$REMOTE_PORT$REMOTE_DIR"
      if ! remote 'echo OK; nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | wc -l' >/tmp/trainer-remote-probe 2>&1; then
        echo "WARNING: trainer SSH probe failed — falling back to SKIP"
        cat /tmp/trainer-remote-probe
        exit 0
      fi
      cat /tmp/trainer-remote-probe
    else
      # Local mode and no GPU: SKIP fast so verify.sh maps to ::warning::
      # (the legoflow-gpu runner isn't online and there's no remote SSH
      # config either).
      if ! command -v nvidia-smi >/dev/null 2>&1; then
        echo "INFO: trainer SKIP-fast — no remote SSH config and nvidia-smi absent"
        exit 0
      fi
    fi
    ;;
esac

case "$BLOCK" in
  tracer)
    echo "INFO: tracer — running prepare_tasks.sh (dryrun.sh requires staged tasks)"
    if ! bash scripts/prepare_tasks.sh; then
      echo "FAIL: prepare_tasks.sh exited non-zero — terminal artifact will not appear"
    fi
    ;;
esac

case "$BLOCK" in
  tracer|evaluator)
    # Warm the networked-filesystem cache for Harbor's CLI: the first import
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
# LAUNCH_IS_REMOTE distinguishes "runs elsewhere, no local pid" from
# "pgrep found nothing" — the old code spelled both as LAUNCH_PID=0, so a
# launcher that died before pgrep ran was mistaken for a remote run and the
# poller waited out the whole budget.
LAUNCH_IS_REMOTE=0
LAUNCH_PAT=""

# `kill -0 0` signals the caller's own process group and always succeeds, so a
# pid of 0 must be treated as "not found" explicitly rather than probed.
launcher_alive() {
  (( LAUNCH_IS_REMOTE == 1 )) && return 0
  [[ -n "${LAUNCH_PID:-}" && "$LAUNCH_PID" != "0" ]] || return 1
  kill -0 "$LAUNCH_PID" 2>/dev/null
}
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
  HOME="${LEGOFLOW_CLI_HOME:-$HOME}" timeout 900 claude -p \
"CI smoke for ${label}. The smoke config has been overlaid at blocks/${BLOCK}/config.yaml. cwd is already blocks/${BLOCK}.

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
  curator)
    # Drive via claude SDK. The smoke config is the source of truth — it
    # specifies the PR list (smoke.input_prs) and all legoflow-curator create flags
    # under runtime_info.input.smoke.*. We materialize the PR list into a
    # tempfile and read the flags via the `cfg` helper, then hand a single
    # composed legoflow-curator-create command to claude headless. Claude's job is
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
    SMOKE_IDS_FILE="$BLOCK_DIR/$SWE_TASKS_BASE/.legoflow-curator-smoke-input-prs.txt"
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
    SMOKE_CMD="source scripts/load_runtime_env.sh && load_runtime_env >/dev/null 2>&1 ; source artifacts/envs/legoflow-curator-env/bin/activate ; nohup legoflow-curator create --input-ids-file ${SMOKE_IDS_FILE#${BLOCK_DIR}/} --max-pr ${SMOKE_MAX_PR} --n-concurrent ${SMOKE_NCONC} --output ${SMOKE_OUT#${BLOCK_DIR}/} --state-dir ${SMOKE_STATE#${BLOCK_DIR}/} --timeout ${SMOKE_TO} --cc-timeout ${SMOKE_CCTO} --no-require-issue --min-source-files ${SMOKE_MINSF} --max-source-files ${SMOKE_MAXSF} --docker-prune-batch ${SMOKE_DPB} --verbose >> artifacts/logs/smoke-launch.log 2>&1 &"
    # curator's (legoflow-curator CLI) openai_proxy CC verification path (the half that writes
    # verifiable_tasks.txt) needs a local LiteLLM proxy on cc_proxy_port. This
    # runner builds its own legoflow-curator-create, so it must start the proxy itself —
    # otherwise PREFLIGHT_LOCAL (dryrun.sh) /health check fails and verification
    # silently banks 0 tasks. No-op when cc_provider_mode != openai_proxy; the
    # EXIT trap keeps it up through the WAIT phase and tears it down on exit.
    # shellcheck source=/dev/null
    source "$BLOCK_DIR/scripts/cc_proxy_lib.sh"
    trap cc_proxy_stop EXIT
    cc_proxy_start "$BLOCK_DIR" "$BLOCK_DIR/config.yaml" \
      || { echo "FAIL: CC LiteLLM proxy did not start (openai_proxy mode)"; exit 1; }
    claude_launch "$SETUP_CHECK_LOCAL" "$PREFLIGHT_LOCAL" "$SMOKE_CMD" \
                  "legoflow-curator create --input-ids-file" "curator"
    LAUNCH_PAT='legoflow-curator create --input-ids-file'
    LAUNCH_PID=$(pgrep -f "$LAUNCH_PAT" | head -1)
    LAUNCH_PID=${LAUNCH_PID:-0}
    ;;
  tracer|evaluator)
    # claude SDK drives 3 phases: setup-check, dryrun.sh, then nohup start.sh.
    # Phases gate each other; start.sh only launches if both earlier phases
    # exited 0. The trailing & on phase 3 detaches start.sh so claude can exit.
    SMOKE_CMD="nohup bash scripts/start.sh >> artifacts/logs/smoke-launch.log 2>&1 &"
    claude_launch "$SETUP_CHECK_LOCAL" "$PREFLIGHT_LOCAL" "$SMOKE_CMD" \
                  "bash scripts/start.sh" "$BLOCK"
    LAUNCH_PAT='bash scripts/start.sh'
    LAUNCH_PID=$(pgrep -f "$LAUNCH_PAT" | head -1)
    LAUNCH_PID=${LAUNCH_PID:-0}
    ;;
  trainer)
    if [[ -n "$REMOTE_IP" ]]; then
      # Remote trainer: all 3 phases run on the GPU host. Stage 3 small SSH
      # wrappers under artifacts/logs/ so claude only has to do 3 simple
      # Bash calls. Phase 1 also fast-forwards the remote checkout to the
      # current branch + overlays the smoke config (this IS the setup-step,
      # which is intentional: dryrun.sh in phase 2 must read the smoke
      # config, not whatever was on the remote before).
      BRANCH="${GITHUB_REF_NAME:-$(git -C "$REPO_ROOT" branch --show-current)}"
      BRANCH="${BRANCH:-main}"
      REMOTE_LOG="$REMOTE_DIR/blocks/trainer/artifacts/logs/smoke-launch.log"
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
      _ssh_wrap "set -e; cd '$REMOTE_DIR'; /usr/bin/git.real fetch origin '$BRANCH' --quiet; /usr/bin/git.real reset --hard 'origin/$BRANCH' --quiet; cp blocks/trainer/tests/smoke/config.yaml blocks/trainer/config.yaml; mkdir -p blocks/trainer/artifacts/logs; rm -rf blocks/trainer/artifacts/model/_smoke_train_ci; test -d blocks/trainer/artifacts/env && echo 'remote setup OK'" \
        > "$BLOCK_DIR/$SETUP_SH"

      # Phase 2: dryrun.sh on the remote trainer dir.
      _ssh_wrap "set -e; cd '$REMOTE_DIR/blocks/trainer'; PATH=/root/.local/bin:\\\$PATH bash scripts/dryrun.sh" \
        > "$BLOCK_DIR/$CHECK_SH"

      # Phase 3: backgrounded start.sh, SSH returns once disown completes.
      _ssh_wrap "set -e; cd '$REMOTE_DIR/blocks/trainer'; PATH=/root/.local/bin:\\\$PATH nohup bash scripts/start.sh > '$REMOTE_LOG' 2>&1 & disown; echo \\\"REMOTE_LAUNCH_PID=\\\$!\\\"" \
        > "$BLOCK_DIR/$RUN_SH"

      chmod +x "$BLOCK_DIR/$SETUP_SH" "$BLOCK_DIR/$CHECK_SH" "$BLOCK_DIR/$RUN_SH"

      SETUP_CMD="bash $SETUP_SH"
      PREFLIGHT_CMD="bash $CHECK_SH"
      SMOKE_CMD="bash $RUN_SH 2>&1 | tee -a artifacts/logs/smoke-launch.log"
      claude_launch "$SETUP_CMD" "$PREFLIGHT_CMD" "$SMOKE_CMD" \
                    "REMOTE_LAUNCH_PID=" "trainer (remote $REMOTE_IP)"
      LAUNCH_IS_REMOTE=1   # runs on the GPU host; no local pid to watch
      LAUNCH_PID=0
    else
      SMOKE_CMD="nohup bash scripts/start.sh >> artifacts/logs/smoke-launch.log 2>&1 &"
      claude_launch "$SETUP_CHECK_LOCAL" "$PREFLIGHT_LOCAL" "$SMOKE_CMD" \
                    "bash scripts/start.sh" "trainer (local)"
      LAUNCH_PAT='bash scripts/start.sh'
      LAUNCH_PID=$(pgrep -f "$LAUNCH_PAT" | head -1)
      LAUNCH_PID=${LAUNCH_PID:-0}
    fi
    ;;
esac

echo "STARTED $BLOCK smoke (launch_pid=$LAUNCH_PID, log=$LAUNCH_LOG)"
# Give it a beat to fail-fast if start.sh dies in its first second of execution.
sleep 5
if ! launcher_alive; then
  echo "WARNING: launcher not running 5s after launch (pid=$LAUNCH_PID). Tail of launch log:"
  tail -30 "$LAUNCH_LOG" 2>/dev/null || true
fi

# ---------- WAIT ----------
echo "INFO: polling for $TERMINAL_GLOB (budget ${BUDGET}s, policy=$WAIT_POLICY)"
START=$(date +%s)
DEADLINE=$((START + BUDGET))
LAST_COUNT=0
POLL_END_REASON="budget exhausted"

# When trainer is running remotely, the terminal artifact lives on the GPU host.
# Poll the remote and scp the result.json + trainer_state.json back as soon
# as they appear so verify.sh (which runs on the local CI workspace) sees
# the same files at the same paths it would for a local run.
fetch_sft_remote_if_ready() {
  [[ -n "$REMOTE_IP" ]] || return 1
  remote "test -f '$REMOTE_DIR/blocks/trainer/artifacts/model/_smoke_train_ci/train_results.json'" 2>/dev/null || return 1
  echo "INFO: remote train_results.json appeared — fetching"
  mkdir -p "$BLOCK_DIR/artifacts/model/_smoke_train_ci"
  scp -i "$REMOTE_KEY" -P "$REMOTE_PORT" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
      "$REMOTE_USER@$REMOTE_IP:$REMOTE_DIR/blocks/trainer/artifacts/model/_smoke_train_ci/train_results.json" \
      "$BLOCK_DIR/artifacts/model/_smoke_train_ci/train_results.json" 2>&1 | tail -3
  scp -i "$REMOTE_KEY" -P "$REMOTE_PORT" \
      -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
      "$REMOTE_USER@$REMOTE_IP:$REMOTE_DIR/blocks/trainer/artifacts/model/_smoke_train_ci/trainer_state.json" \
      "$BLOCK_DIR/artifacts/model/_smoke_train_ci/trainer_state.json" 2>&1 | tail -3 || true
  return 0
}

while (( $(date +%s) < DEADLINE )); do
  # Remote trainer: poll over SSH and scp on success.
  if [[ -n "$REMOTE_IP" ]]; then
    if fetch_sft_remote_if_ready; then
      elapsed=$(($(date +%s) - START))
      echo "INFO: remote terminal artifact retrieved after ${elapsed}s"
      LAST_COUNT=1
      POLL_END_REASON="artifact retrieved"
      break
    fi
    sleep 30
    continue
  fi

  # The launcher dying is terminal: nothing else writes the artifact, so the
  # remaining budget can only be spent waiting for something that will never
  # appear. start.sh refuses to launch AFTER its dryrun (well past the 5s
  # fail-fast probe above), and that refusal used to cost the full budget and
  # then surface as verify.sh's "no result.json" — which hides the real reason.
  # A local launcher we can no longer see is terminal. Re-pgrep first: the
  # initial probe can miss a launcher that took a moment to exec, and adopting
  # its pid late is cheaper than waiting out the budget on a false negative.
  if ! launcher_alive && [[ -n "$LAUNCH_PAT" ]]; then
    # The first probe can miss a launcher that took a moment to exec. Re-check,
    # but never adopt this script, its parent, or the CI wrapper: their command
    # lines contain the pattern as literal text, and adopting one would keep the
    # poll alive for the full budget on a launcher that is already gone.
    _repid=$(pgrep -f "$LAUNCH_PAT" 2>/dev/null \
             | grep -vx -e "$$" -e "$PPID" \
             | while read -r _p; do
                 tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null | grep -q 'smoke_run\.sh' || echo "$_p"
               done | head -1)
    if [[ -n "$_repid" ]]; then
      LAUNCH_PID="$_repid"
      echo "INFO: adopted launcher pid=$LAUNCH_PID (started after the first probe)"
    fi
  fi
  if ! launcher_alive; then
    elapsed=$(($(date +%s) - START))
    if compgen -G "$TERMINAL_GLOB" > /dev/null; then
      # shellcheck disable=SC2086  # glob expansion is intentional
      LAST_COUNT=$(ls $TERMINAL_GLOB 2>/dev/null | wc -l)
    fi
    if (( LAST_COUNT > 0 )); then
      echo "INFO: launcher exited after ${elapsed}s with ${LAST_COUNT} artifact(s); stopping the poll"
      POLL_END_REASON="launcher exited"
    else
      echo "ERROR: launcher (pid=$LAUNCH_PID) exited after ${elapsed}s without producing $TERMINAL_GLOB"
      echo "       the failure is in the launch log below, not in verify.sh"
      POLL_END_REASON="launcher exited without artifacts"
    fi
    break
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
      POLL_END_REASON="first artifact present"
      break
    fi
  fi
  sleep 30
done

elapsed=$(($(date +%s) - START))
echo "INFO: poll ended — ${POLL_END_REASON} (${elapsed}s); verify.sh will scan artifacts (count=$LAST_COUNT)"
if (( LAST_COUNT == 0 )); then
  echo "INFO: 0 artifacts — full launch log:"
  cat "$LAUNCH_LOG" 2>/dev/null || true
fi

# Harbor's claude-code agent docker containers write agent/sessions/ as
# root:root mode 700. The shared filesystem root-squashes chown from Docker
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

# Remote trainer: drop the throwaway _smoke_train_ci run dir + dataset entry.
# verify.sh's trap does the equivalent on its local copy; we mirror it here
# for the GPU host so successive CI runs don't accumulate stale state.
if [[ "$BLOCK" == "trainer" && -n "$REMOTE_IP" ]]; then
  echo "INFO: cleanup — remote run dir + dataset_info entry"
  remote "
    cd '$REMOTE_DIR/blocks/trainer' || exit 0
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
