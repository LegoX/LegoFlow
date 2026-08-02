#!/usr/bin/env bash
# ROOT end-to-end smoke orchestrator.
#
# Runs the full pipeline as ONE chain, fail-fast, the SAME way the per-block CI
# smokes run each block: a narrow `claude -p` (Claude SDK) launcher that nohups
# the long-running command and exits, plus a bash poller that waits for the
# stage's terminal artifact. Between stages it wires each block's REAL output
# into the next, and stops the moment a stage's verify fails.
#
#   stage 1 curator  : collect ~200 PRs from scratch -> generate verified tasks
#   stage 2 tracer : infer trajectories on those verified tasks -> keep reward==1
#                     -> convert to LF SFT data
#   stage 3 trainer     : train on the 512 fixture + tracer reward==1 LF (combined)
#                     -> PERSIST a checkpoint   (remote GPU pod)
#   stage 4 evaluator    : serve that checkpoint (vLLM+LiteLLM on the pod) -> evaluate
#                     on the SWE-bench Verified 100-subset
#
# Usage:
#   bash tests/smoke/run_pipeline.sh [--from <stage>] [--to <stage>]
#                                    [--budget <sec>] [--keep-serving] [--dry-run]
#
# Stages: curator tracer trainer evaluator. --from/--to bound which run (default all).
# Each stage overlays tests/smoke/<block>/config.yaml onto
# blocks/<block>/config.yaml; the originals are restored on exit.
#
# Env:
#   CLAUDE_SDK=0   launch stages with a direct nohup instead of `claude -p`
#                  (for hosts without the Claude CLI; default 1 = use the SDK).
#
# Exit 0 = full chain PASS, 77 = a stage SKIP'd on an absent prereq (chain
# stopped, not a failure), 1 = a stage FAILED.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAGES=(curator tracer trainer evaluator)
FROM="curator"; TO="evaluator"
BUDGET_OVERRIDE=""
KEEP_SERVING=0
DRY_RUN=0
CLAUDE_SDK="${CLAUDE_SDK:-1}"

# Per-stage default budgets (seconds). curator gets 4h: a genuine ~200-PR
# from-scratch collection + create-to-verified is slow. tracer/evaluator use
# policy=first early-exit so these are just upper bounds on the wait.
# trainer: 50 steps at 128K cutoff on an 8B model (DeepSpeed ZeRO-3) is ~4 min/step
# (~3.3h), plus model load + tokenization — 4h cap.
# Per-stage wall-clock ceilings. curator and tracer were 4h and 3h, which is far
# longer than a smoke should ever hold the pipeline: the point is to prove the
# chain works, not to accumulate data. Both are 2h.
declare -A BUDGET=( [curator]=7200 [tracer]=7200 [trainer]=14400 [evaluator]=5400 )

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --budget) BUDGET_OVERRIDE="$2"; shift 2 ;;
    --keep-serving) KEEP_SERVING=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
hr() { echo "================================================================="; }

cfg() {  # cfg <file> <dotted-key>
  python3 - "$1" "$2" <<'PY'
import sys, yaml
try: d = yaml.safe_load(open(sys.argv[1])) or {}
except Exception: print(""); raise SystemExit
cur = d
for p in sys.argv[2].split("."):
    if not isinstance(cur, dict): cur = None; break
    cur = cur.get(p)
print("" if cur is None else cur)
PY
}

# --- config overlay/restore (so the chain never leaves a smoke config behind) -
declare -a _BACKUPS=()
overlay() {  # overlay <block>
  local b="$1"
  local prod="$ROOT_DIR/blocks/$b/config.yaml"
  local smoke="$ROOT_DIR/tests/smoke/$b/config.yaml"
  [[ -f "$smoke" ]] || { echo "FAIL: missing $smoke"; exit 1; }
  [[ "$DRY_RUN" == 1 ]] && { log "[DRY-RUN] would overlay tests/smoke/$b/config.yaml -> blocks/$b/config.yaml"; return 0; }
  cp "$prod" "$prod.root-smoke-bak.$$"
  _BACKUPS+=("$prod")
  cp "$smoke" "$prod"
  # The smoke template carries structure only — endpoints, keys and remote hosts
  # come from the shared env file at run time. Inject into the COPY; the
  # template itself is tracked and must stay value-free.
  [[ -f /gpufs/haoli/cicd/shared/.env ]] && source /gpufs/haoli/cicd/shared/.env
  python3 "$ROOT_DIR/scripts/inject_smoke_secrets.py" "$prod" || {
    echo "FAIL: could not inject smoke secrets into $prod"; exit 1; }
  sync
  # The shared filesystem (aliyun-alinas-efc) can lag: the cp returns before the
  # new bytes are consistent for a fresh open(), so the cfg reads right after
  # overlay see a half-written/empty file and ALL collect.* params come back
  # blank (-> 0 PRs -> SKIP). Block until the copy reads back as this block.
  local _ok=0 _i
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    [[ "$(cfg "$prod" meta_info.name)" == "$b" ]] && { _ok=1; break; }
    sleep 1
  done
  [[ "$_ok" == 1 ]] || { echo "FAIL: overlaid $prod not consistent after copy (efc lag)"; exit 1; }
  log "overlaid tests/smoke/$b/config.yaml -> blocks/$b/config.yaml"
}
restore_all() {
  for prod in "${_BACKUPS[@]}"; do
    [[ -f "$prod.root-smoke-bak.$$" ]] && mv "$prod.root-smoke-bak.$$" "$prod"
  done
}
trap restore_all EXIT INT TERM

# --- Claude-SDK launcher (mirrors .github/scripts/smoke_run.sh) --------------
# Drives 3 gated phases: setup-check -> dryrun -> nohup'd run. With CLAUDE_SDK=0
# it runs the three commands directly (no `claude`), for hosts without the CLI.
claude_launch() {  # claude_launch <block> <setup_cmd> <preflight_cmd> <run_cmd> <pgrep_pat> <label>
  local block="$1" setup="$2" preflight="$3" run_cmd="$4" pgrep_pat="$5" label="$6"
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "  [DRY-RUN] setup   : $setup"
    echo "  [DRY-RUN] check   : $preflight"
    echo "  [DRY-RUN] run     : $run_cmd"
    return 0
  fi
  if [[ "$CLAUDE_SDK" == 1 ]] && command -v claude >/dev/null 2>&1; then
    HOME="${HOME:-/home/haoli}" timeout 900 claude -p \
"Root-smoke stage for ${label}. The smoke config is overlaid at blocks/${block}/config.yaml. cwd is blocks/${block}.

Run 3 gated phases via the Bash tool. STOP and reply FAILED <phase> with the last 20 lines if any phase exits non-zero. Do NOT proceed past a failure.

Phase 1 (setup): ${setup}
Phase 2 (check): ${preflight}
Phase 3 (run — background under nohup; the trailing & detaches it): ${run_cmd}

After Phase 3, confirm one matching child is alive: pgrep -af '${pgrep_pat}'
Reply STARTED <pid> or FAILED <phase> <tail>, then exit. Do not wait for completion; a bash poller watches the artifact. Do not ask for confirmation; this is a non-interactive CI run." \
      --dangerously-skip-permissions --max-turns 30 2>&1 || true
  else
    log "(CLAUDE_SDK=0) launching ${label} directly"
    bash -c "$setup" || { echo "FAILED 1 (setup)"; return 1; }
    bash -c "$preflight" || { echo "FAILED 2 (check)"; return 1; }
    bash -c "$run_cmd" || { echo "FAILED 3 (run)"; return 1; }
  fi
}

# --- poll for a terminal artifact glob ---------------------------------------
wait_for() {  # wait_for <glob> <budget> <policy first|full>
  local glob="$1" budget="$2" policy="$3"
  [[ "$DRY_RUN" == 1 ]] && { log "[DRY-RUN] would poll for $glob (budget ${budget}s, policy=$policy)"; return 0; }
  local start deadline last=0
  start=$(date +%s); deadline=$((start + budget))
  log "polling for $glob (budget ${budget}s, policy=$policy)"
  while (( $(date +%s) < deadline )); do
    # shellcheck disable=SC2086
    if compgen -G "$glob" >/dev/null 2>&1; then
      local count; count=$(ls $glob 2>/dev/null | wc -l)
      if (( count != last )); then log "  $count artifact(s) at $(( $(date +%s) - start ))s"; last=$count; fi
      [[ "$policy" == "first" ]] && return 0
    fi
    sleep 30
  done
  log "  budget exhausted ($(( $(date +%s) - start ))s, artifacts=$last)"
}

# --- wait for a harbor job to TRULY finish -----------------------------------
# harbor writes its job-level result.json BEFORE the job is actually done (and a
# killed/early-exiting orchestrator tears down the per-job LiteLLM proxy out
# from under the still-running trials -> ConnectionRefused). So for multi-trial
# harbor stages we wait for the job PROCESS to exit, not for a result.json to
# appear. Waits for the process to be SEEN running first (avoids the launch
# race), then for it to disappear. Caps at budget.
wait_job() {  # wait_job <pgrep_pattern> <budget> <progress_glob>
  local pat="$1" budget="$2" glob="$3"
  [[ "$DRY_RUN" == 1 ]] && { log "[DRY-RUN] would wait for harbor job '$pat' (budget ${budget}s)"; return 0; }
  local start deadline last=0 seen=0 absent=0
  start=$(date +%s); deadline=$((start + budget))
  log "waiting for harbor job to finish ('$pat', budget ${budget}s)"
  while (( $(date +%s) < deadline )); do
    if pgrep -f "$pat" >/dev/null 2>&1; then
      seen=1; absent=0
      # shellcheck disable=SC2086
      if compgen -G "$glob" >/dev/null 2>&1; then
        local c; c=$(ls $glob 2>/dev/null | wc -l)
        (( c != last )) && { log "  $c trial result(s) at $(( $(date +%s) - start ))s"; last=$c; }
      fi
    elif (( seen == 1 )); then
      log "  harbor job finished at $(( $(date +%s) - start ))s ($last trial result(s))"
      return 0
    else
      absent=$((absent + 1))
      # Grace before concluding "never started": the evaluator/tracer start.sh runs
      # its OWN dryrun (litellm import off a shared FS is ~2-3 min) + starts the
      # LiteLLM proxy BEFORE `harbor run` ever appears — a 3-min grace raced that
      # and false-SKIPd evaluator. 10 min comfortably covers the double-dryrun startup.
      (( absent >= 20 )) && { log "  harbor job never started (10 min) — proceeding to verify"; return 0; }
    fi
    sleep 30
  done
  log "  budget exhausted ($(( $(date +%s) - start ))s); harbor may still be running"
}

gate() {  # gate <stage>; returns the verify rc, prints verdict
  local stage="$1" rc
  [[ "$DRY_RUN" == 1 ]] && { log "[DRY-RUN] would verify stage $stage"; return 0; }
  set +e
  bash "$ROOT_DIR/tests/smoke/verify.sh" "$stage"
  rc=$?
  set -e
  return $rc
}

want() {  # want <stage> -> 0 if in [FROM..TO] window
  local stage="$1" i from_i=-1 to_i=-1 s_i=-1
  for i in "${!STAGES[@]}"; do
    [[ "${STAGES[$i]}" == "$FROM" ]] && from_i=$i
    [[ "${STAGES[$i]}" == "$TO" ]] && to_i=$i
    [[ "${STAGES[$i]}" == "$stage" ]] && s_i=$i
  done
  (( s_i >= from_i && s_i <= to_i ))
}

[[ -n "$BUDGET_OVERRIDE" ]] && for s in "${STAGES[@]}"; do BUDGET[$s]="$BUDGET_OVERRIDE"; done

# --- HARD RESOURCE GATE ------------------------------------------------------
# Before launching ANY stage, confirm the hardware/network we are about to lean
# on has headroom right now: runner disk/memory/Docker, the GPU pod's free GPUs/
# disk/memory (scope-aware — only when trainer/evaluator are in-window), and upstream LLM
# reachability. This is the failure class that has actually bitten this smoke
# (pod GPUs held by someone else -> OOM; disk full; endpoint down -> 0 verified).
# Set SKIP_PREFLIGHT=1 only to deliberately bypass (not recommended).
if [[ "$DRY_RUN" != 1 && "${SKIP_PREFLIGHT:-0}" != 1 ]]; then
  if ! bash "$ROOT_DIR/tests/smoke/preflight.sh" "$FROM" "$TO"; then
    echo "ROOT SMOKE CHAIN: ABORTED at preflight — resources not ready (see above). Nothing launched."
    exit 1
  fi
elif [[ "${SKIP_PREFLIGHT:-0}" == 1 ]]; then
  log "WARNING: SKIP_PREFLIGHT=1 — skipping the resource gate (GPU/disk/memory/API not checked)"
fi

CHAIN_RC=0
hr; log "ROOT SMOKE CHAIN  from=$FROM to=$TO  claude_sdk=$CLAUDE_SDK  dry_run=$DRY_RUN"; hr
[[ "$DRY_RUN" == 1 ]] && log "NOTE: dry-run skips the config overlay, so smoke-only launch params (max_pr, collect.*, ...) render blank below — they populate from tests/smoke/<block>/config.yaml in a real run."

# ============================================================ stage 1: curator =
if want curator; then
  hr; log "STAGE 1/4 — curator (collect ~200 PRs -> verified tasks)"; hr
  overlay curator
  SB="$ROOT_DIR/blocks/curator"; CFG="$SB/config.yaml"
  BASE="$(cfg "$CFG" runtime_info.output.swe_tasks_dir.path)"
  SUB="$(cfg "$CFG" runtime_info.input.smoke.output_subdir)"
  STATE="$(cfg "$CFG" runtime_info.input.smoke.state_subdir)"
  MAXPR="$(cfg "$CFG" runtime_info.input.smoke.max_pr)"
  NCONC="$(cfg "$CFG" runtime_info.input.languages.py.params.n_concurrent)"
  TO_="$(cfg "$CFG" runtime_info.input.languages.py.params.timeout)"
  CCTO="$(cfg "$CFG" runtime_info.input.languages.py.params.cc_timeout)"
  MINSF="$(cfg "$CFG" runtime_info.input.smoke.min_source_files)"
  MAXSF="$(cfg "$CFG" runtime_info.input.smoke.max_source_files)"
  DPB="$(cfg "$CFG" runtime_info.input.smoke.docker_prune_batch)"
  C_LANG="$(cfg "$CFG" runtime_info.input.smoke.collect.languages)"
  C_REPON="$(cfg "$CFG" runtime_info.input.smoke.collect.repo_num)"
  C_MAXPR="$(cfg "$CFG" runtime_info.input.smoke.collect.max_prs_per_repo)"
  C_TARGET="$(cfg "$CFG" runtime_info.input.smoke.collect.target_prs)"
  C_OUT="$(cfg "$CFG" runtime_info.input.smoke.collect.output_dir)"
  C_BUDGET="$(cfg "$CFG" runtime_info.input.smoke.collect.time_budget_s)"; C_BUDGET="${C_BUDGET:-1200}"
  # Never let PR collection outlast the curator stage budget: when the run is
  # dispatched with --budget (e.g. root_budget=1800), the collector must honor it
  # too, or stage 1 alone could burn the config's time_budget_s (hours) before the
  # stage budget even starts and blow past the CI job cap.
  if [[ -n "$BUDGET_OVERRIDE" ]] && (( C_BUDGET > BUDGET[curator] )); then
    log "capping PR-collection budget ${C_BUDGET}s -> curator stage budget ${BUDGET[curator]}s (--budget override)"
    C_BUDGET="${BUDGET[curator]}"
  fi
  C_MINIDS="$(cfg "$CFG" runtime_info.input.smoke.collect.min_ids)"; C_MINIDS="${C_MINIDS:-8}"
  IDS_FILE="$SB/$C_OUT/${C_LANG}_pr_ids.txt"

  # PREP: from-scratch PR collection (best-effort) + committed fallback top-up.
  if [[ "$DRY_RUN" == 1 ]]; then
    log "[DRY-RUN] collect_prs_wo_image.py --languages $C_LANG --repo_num $C_REPON --max_prs_per_repo $C_MAXPR (budget ${C_BUDGET}s) -> $IDS_FILE; top up from fallback if < $C_MINIDS; head -$C_TARGET"
  else
    mkdir -p "$SB/$C_OUT" "$SB/$BASE/$SUB" "$SB/$BASE/$STATE"
    log "from-scratch PR collection ($C_LANG), best-effort budget ${C_BUDGET}s"
    # The collector imports the swegen package and combines its token file with
    # GITHUB_TOKENS / GITHUB_TOKEN. It can futex-stall at init (0 sockets,
    # wchan=futex_wait_queue) — PYTHONUNBUFFERED=1 is the documented fix. It
    # writes pr_ids incrementally but resumes only per-QUALIFYING-REPO (not the
    # in-flight candidate scan), so a progress-cap retry would loop forever.
    # Hence ONE best-effort, time-bounded attempt; whatever real ids it writes
    # are kept, and the committed fallback tops up below if too few.
    ( cd "$SB" \
        && source scripts/load_runtime_env.sh \
        && load_runtime_env >/dev/null 2>&1 \
        && source artifacts/envs/swegen-env/bin/activate \
        && PYTHONUNBUFFERED=1 COLLECT_GITHUB_TOKEN_FILE="$SB/gh_token.txt" \
           timeout --kill-after=30s "$C_BUDGET" python3 repos/swegen/tools/collect_prs_wo_image.py \
             --languages "$C_LANG" --repo_num "$C_REPON" --max_prs_per_repo "$C_MAXPR" \
             --output_dir "$C_OUT" ) \
      || log "WARN: from-scratch collection ended non-zero/timed out — keeping what it wrote + fallback"
    n_scratch=$([[ -f "$IDS_FILE" ]] && wc -l <"$IDS_FILE" || echo 0)
    log "from-scratch collection wrote ${n_scratch} PR id(s)"
    if (( n_scratch < C_MINIDS )); then
      log "topping up from committed fallback_prs (n=${n_scratch} < min_ids=${C_MINIDS})"
      IDS_FILE="$IDS_FILE" python3 - "$CFG" <<'PY'
import sys, os, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
fb = (((cfg.get("runtime_info") or {}).get("input") or {}).get("smoke") or {}).get("collect", {}).get("fallback_prs") or []
f = os.environ["IDS_FILE"]
seen = set()
if os.path.isfile(f):
    seen = {l.strip() for l in open(f, encoding="utf-8") if l.strip()}
added = 0
with open(f, "a+", encoding="utf-8") as fh:
    for pid in fb:
        if pid and pid not in seen:
            fh.write(pid + "\n"); seen.add(pid); added += 1
print(f"appended {added} fallback PR id(s); total now {len(seen)}")
PY
    fi
    if [[ -f "$IDS_FILE" ]]; then
      head -n "$C_TARGET" "$IDS_FILE" > "$IDS_FILE.capped" && mv "$IDS_FILE.capped" "$IDS_FILE"
      log "PR id list ready: $(wc -l <"$IDS_FILE") id(s) -> ${IDS_FILE#$SB/}"
    fi
  fi

  if [[ "$DRY_RUN" != 1 && ! -s "$IDS_FILE" ]]; then
    echo "SKIP: no PR ids collected — cannot run swegen create"; CHAIN_RC=77
  else
    # curator's CC verification path (the half that writes verifiable_tasks.txt)
    # uses cc_provider_mode: openai_proxy, so it needs a local LiteLLM proxy on
    # cc_proxy_port translating Anthropic -> the upstream OpenAI endpoint. Unlike
    # the per-block smoke (10_pr_demo.sh), this orchestrator builds its own
    # swegen-create, so it must start the proxy itself — otherwise dryrun.sh's
    # /health check fails and verification SILENTLY banks 0 tasks (the failure
    # that sank the first root run). No-op when cc_provider_mode != openai_proxy;
    # Start from nothing. A smoke must prove THIS run produced tasks, so its own
    # output and state dirs are wiped first. Leaving them made the gate below
    # count leftovers from a previous smoke: on 2026-07-26 curator "banked 8
    # verified tasks" 2 minutes in, with its endpoint hard down and not one task
    # generated — the 8 were residue. Same reason the ledger is never consulted
    # here (see HARBOR_LEDGER_EXCLUDE=0 at the tracer stage): a smoke depends on
    # no history but its own.
    #
    # Agent containers write some files as root, so rm can fail on them; move the
    # dir aside instead of failing the run, and never touch anything outside the
    # smoke's own subdirs.
    if [[ "$DRY_RUN" == 1 ]]; then
      log "[DRY-RUN] would clear curator smoke output ($BASE/$SUB) and state ($BASE/$STATE)"
    else
      for _stale in "$SB/$BASE/$SUB" "$SB/$BASE/$STATE"; do
        [[ -e "$_stale" ]] || continue
        if ! rm -rf "$_stale" 2>/dev/null; then
          mv "$_stale" "${_stale}.stale-$(date +%s)" 2>/dev/null \
            || log "WARN: could not clear $_stale — the gate may count stale tasks"
        fi
      done
      log "cleared curator smoke output ($BASE/$SUB) and state ($BASE/$STATE)"
    fi

    # torn down after the curator gate. shellcheck source=/dev/null
    source "$SB/scripts/cc_proxy_lib.sh"
    [[ "$DRY_RUN" == 1 ]] || cc_proxy_start "$SB" "$CFG" \
      || log "WARN: CC LiteLLM proxy did not start — curator dryrun/verification will fail"
    RUN="source scripts/load_runtime_env.sh && load_runtime_env >/dev/null 2>&1 ; source artifacts/envs/swegen-env/bin/activate ; nohup swegen create --input-ids-file ${IDS_FILE#$SB/} --max-pr ${MAXPR} --n-concurrent ${NCONC} --output ${BASE}/${SUB} --state-dir ${BASE}/${STATE} --timeout ${TO_} --cc-timeout ${CCTO} --no-require-issue --min-source-files ${MINSF} --max-source-files ${MAXSF} --docker-prune-batch ${DPB} --verbose >> artifacts/logs/root-smoke-swegen.log 2>&1 &"
    ( cd "$SB" && mkdir -p artifacts/logs && claude_launch curator \
        '( test -d artifacts/envs || test -d artifacts/env ) && test -d repos && echo setup-ok' \
        'bash scripts/dryrun.sh' "$RUN" 'swegen create --input-ids-file' 'curator' )
    # Wait until curator banks up to max_pr verified tasks (not just the first) so
    # tracer gets a diverse pool — one hard/unsolvable task shouldn't sink the
    # reward==1 gate. Break early once `create` finishes (PRs exhausted or it hit
    # its own --max-pr) or the budget elapses.
    MAN="$SB/$BASE/$SUB/verifiable_tasks.txt"
    swdl=$(( $(date +%s) + ${BUDGET[curator]} ))
    log "waiting for curator to bank up to max_pr=$MAXPR verified task(s) (budget ${BUDGET[curator]}s)"
    while (( $(date +%s) < swdl )); do
      nver=$([[ -f "$MAN" ]] && grep -c . "$MAN" 2>/dev/null || echo 0)
      if (( nver >= MAXPR )); then log "  curator banked $nver verified task(s) (>= max_pr=$MAXPR)"; break; fi
      if ! pgrep -f 'swegen create --input-ids-file' >/dev/null 2>&1; then
        log "  swegen create finished — $nver verified task(s) banked"; break
      fi
      sleep 30
    done
    # The nohup'd `swegen create` is NOT bounded by this wait loop — once we've
    # banked enough (or hit the stage budget) it keeps grinding the remaining PRs
    # in the background. Left detached, its continuous Docker/disk writeback makes
    # the NEXT stage's overlay() `sync` block in wb_wait_for_completion and wedge
    # the whole pipeline (observed: tracer overlay `sync` stuck 44+ min behind 8
    # orphaned create workers). Stop it (and any lingering PR collector) here so
    # `sync` can settle and the chain advances.
    # Bracket-trick the patterns ('[c]reate') so pkill can never match its own
    # command line — a `pkill -f '<pat>'` whose cmdline contains <pat> SIGKILLs
    # the wrapper shell (instant exit, empty log). Convention for all smoke pkills.
    pkill -f 'curator [c]reate --input-ids-file' 2>/dev/null || true
    pkill -f 'collect_[p]rs_wo_image' 2>/dev/null || true
    if gate curator; then log "stage curator PASS"; else
      rc=$?; [[ $rc == 77 ]] && { log "stage curator SKIP"; CHAIN_RC=77; } || { log "stage curator FAIL"; CHAIN_RC=1; }
    fi
    # Tear down the curator CC proxy before the next stage (no-op if not started).
    [[ "$DRY_RUN" == 1 ]] || cc_proxy_stop
  fi
fi

# =========================================================== stage 2: tracer =
if want tracer && [[ "$CHAIN_RC" == 0 ]]; then
  hr; log "STAGE 2/4 — tracer (verified tasks -> reward==1 -> LF SFT data)"; hr
  overlay tracer
  TB="$ROOT_DIR/blocks/tracer"; CFG="$TB/config.yaml"
  # WIRE: harbor has no "run a task N times" knob, so to give tracer multiple
  # independent solve attempts (raising the reward==1 gate's odds without
  # re-running curator) we stage `smoke_attempts` replicas of each curator verified
  # task into a dir of distinct names (harbor IDs tasks by dir name; task.toml
  # has no id), then point task_source.dataset_name at that staging dir.
  # Read curator's smoke output location from the STATIC smoke config (it carries
  # the smoke-only output_subdir field) — the production curator config lacks it,
  # and with --from tracer curator is never overlaid, so reading the block
  # config would yield an empty subdir and stage 0 tasks.
  SWE_SMOKE_CFG="$ROOT_DIR/tests/smoke/curator/config.yaml"
  SWE_SUB="$(cfg "$SWE_SMOKE_CFG" runtime_info.input.smoke.output_subdir)"
  SWE_BASE="$(cfg "$SWE_SMOKE_CFG" runtime_info.output.swe_tasks_dir.path)"
  SWE_ABS="$ROOT_DIR/blocks/curator/$SWE_BASE/$SWE_SUB"
  ATTEMPTS="$(cfg "$CFG" runtime_info.input.smoke_attempts)"; ATTEMPTS="${ATTEMPTS:-1}"
  STAGE_DIR="$TB/artifacts/root-smoke-src-tasks"
  if [[ "$DRY_RUN" != 1 ]]; then
    SWE_ABS="$SWE_ABS" STAGE_DIR="$STAGE_DIR" ATTEMPTS="$ATTEMPTS" python3 - <<'PY'
import os, shutil
src, stage, n = os.environ["SWE_ABS"], os.environ["STAGE_DIR"], int(os.environ["ATTEMPTS"])
man = os.path.join(src, "verifiable_tasks.txt")
tasks = [t.strip() for t in open(man, encoding="utf-8")] if os.path.isfile(man) else []
tasks = [t for t in tasks if t]
if os.path.isdir(stage): shutil.rmtree(stage)
os.makedirs(stage, exist_ok=True)
replicas = []
for t in tasks:
    td = os.path.join(src, t)
    if not os.path.isdir(td):
        continue
    for i in range(1, max(1, n) + 1):
        name = f"{t}-r{i:02d}" if n > 1 else t
        shutil.copytree(td, os.path.join(stage, name))
        replicas.append(name)
with open(os.path.join(stage, "verifiable_tasks.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(replicas) + "\n")
print(f"staged {len(replicas)} task replica(s) ({len(tasks)} verified x {n}) -> {stage}")
PY
    python3 - "$CFG" "$STAGE_DIR" <<'PY'
import sys, yaml
p, src = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(p)) or {}
d["runtime_info"]["input"]["task_source"]["dataset_name"] = src
yaml.safe_dump(d, open(p, "w"), sort_keys=False, allow_unicode=True)
print(f"wired tracer task_source.dataset_name -> {src}")
PY
  fi
  JOBS="$(cfg "$CFG" runtime_info.input.harbor_job.jobs_dir)"
  # Clear stale job dirs + prepared-task cache from any prior smoke run, else
  # policy=first would match an old result.json instantly and verify the wrong
  # job. jobs_dir is smoke-isolated (artifacts/jobs/root-smoke). ALSO clear the
  # sft_data out_dir: stage 3 merges every `*/lf.json` under it, so a prior run's
  # converted LF would otherwise be trained on even when THIS tracer resolves
  # zero — masking a broken curator→tracer handoff.
  TRAJ_OUT="$(cfg "$CFG" runtime_info.input.sft_conversion.out_dir)"; TRAJ_OUT="${TRAJ_OUT:-artifacts/sft_data}"
  if [[ "$DRY_RUN" != 1 ]]; then
    # Agent containers write session files as root, so a plain rm -rf silently
    # leaves the dir behind and the "stale job" it was meant to prevent survives
    # (seen 2026-07-26: a previous job dir outlived three rm attempts). Fall back
    # to moving it aside, which only needs write permission on the parent.
    # NOTE: scope each entry to the smoke's OWN artifacts. `jobs_dir` is already
    # smoke-specific (artifacts/jobs/root-smoke), but `sft_conversion.out_dir` is
    # the shared artifacts/sft_data root — the converter writes per job into
    # <out_dir>/<job>/, so only those subdirs belong to the smoke. Clearing the
    # whole out_dir wipes every real converted dataset in the block (seen
    # 2026-07-27: a full artifacts/sft_data/ was destroyed by a smoke run).
    #
    # The per-job conversion dirs are named after the JOB, not after jobs_dir:
    # start.sh names each job <dataset>-<agent>-<model>-<timestamp>, so
    # <out_dir>/$(basename $JOBS) — i.e. .../sft_data/root-smoke — never exists
    # and nothing was ever cleared. Enumerate the actual job names under the
    # jobs root instead, or a repeat smoke leaves every previous conversion in
    # place and the trainer stage merges those stale trajectories into the run.
    _sft_stale=()
    if [[ -d "$TB/$JOBS" ]]; then
      for _job in "$TB/$JOBS"/*/; do
        [[ -d "$_job" ]] || continue
        _sft_stale+=("$TB/$TRAJ_OUT/$(basename "$_job")")
      done
    fi
    # NOT $STAGE_DIR: it was filled from curator moments ago by the copy above,
    # which already rmtree's it first. Clearing it here deleted this run's own
    # task source, so prepare_tasks.sh had nothing to stage and tracer's dryrun
    # failed with "Harbor tasks are not prepared" — harbor never launched and the
    # stage sat out its whole budget waiting for a job that did not exist.
    for _stale in "$TB/$JOBS" "$TB/artifacts/tasks/$(basename "$STAGE_DIR")" \
                  "${_sft_stale[@]}"; do
      [[ -e "$_stale" ]] || continue
      if ! rm -rf "$_stale" 2>/dev/null; then
        mv "$_stale" "${_stale}.stale-$(date +%s)" 2>/dev/null \
          || log "WARN: could not clear $_stale — a stale result may be picked up"
      fi
    done
    log "cleared tracer smoke jobs/tasks/sft_data"
  fi
  # A smoke deliberately re-runs its fixture tasks, and curator regenerates the
  # same task ids from the same PR pool every time. Excluding what the ledger
  # already consumed would leave harbor with nothing to do (0 trials -> SKIP),
  # so the ledger-derived half of the exclude list is off for smoke runs. The
  # hand-maintained HARBOR_EXCLUDE_TASKS still applies.
  RUN="nohup env HARBOR_LEDGER_EXCLUDE=0 bash scripts/start.sh >> artifacts/logs/root-smoke-tracer.log 2>&1 &"
  ( cd "$TB" && mkdir -p artifacts/logs && \
    { [[ "$DRY_RUN" == 1 ]] && echo "[DRY-RUN] prepare_tasks.sh" || bash scripts/prepare_tasks.sh || log "WARN: prepare_tasks.sh non-zero"; } && \
    claude_launch tracer \
      'test -d artifacts/env && test -d repos && echo setup-ok' \
      'bash scripts/dryrun.sh' "$RUN" 'bash scripts/start.sh' 'tracer' )
  # Wait for the harbor job PROCESS to exit (NOT for a result.json to appear —
  # harbor writes the job-level result.json before the trials are actually done,
  # and exiting then tears the LiteLLM proxy down under the still-running trials
  # -> ConnectionRefused). Only after the job truly finishes does verify.sh grade
  # the per-task rewards (needs >=1 reward==1 across the replicas).
  wait_job "harbor run.*$JOBS" "${BUDGET[tracer]}" "$TB/$JOBS/*/*/result.json"
  if gate tracer; then log "stage tracer PASS"; else
    rc=$?; [[ $rc == 77 ]] && { log "stage tracer SKIP"; CHAIN_RC=77; } || { log "stage tracer FAIL"; CHAIN_RC=1; }
  fi
fi

# =============================================================== stage 3: trainer =
if want trainer && [[ "$CHAIN_RC" == 0 ]]; then
  hr; log "STAGE 3/4 — trainer (512 fixture + tracer reward==1 -> trained checkpoint)"; hr
  overlay trainer
  FB="$ROOT_DIR/blocks/trainer"; CFG="$FB/config.yaml"
  FIXTURE="$(cfg "$CFG" runtime_info.input.source.fixture_lf)"
  UPDIR="$(cfg "$CFG" runtime_info.input.source.upstream_lf_dir)"
  R_IP="$(cfg "$CFG" meta_info.resources.ip)"
  # The tracer reward==1 LF lives on the CI host under blocks/tracer/<out_dir>.
  TRAJ_SFT="$ROOT_DIR/blocks/tracer/$(cfg "$ROOT_DIR/blocks/tracer/config.yaml" runtime_info.input.sft_conversion.out_dir)"

  # WIRE: merge fixture + every tracer lf.json into one combined LF, then lower
  # source.type to the trainer block's native local_lf. For a remote pod this merge
  # is staged onto the pod (where train.sh reads it); for local it stays here.
  # Write under artifacts/data/ (haoli-owned) NOT artifacts/data/examples/ —
  # that dir is often root:root residue from CI/remote runs (lf_512.json lives
  # there readable, but the dir isn't writable by us).
  MERGED_REL="artifacts/data/root_smoke_combined.json"
  if [[ "$DRY_RUN" == 1 ]]; then
    log "[DRY-RUN] merge $FIXTURE + $TRAJ_SFT/*/lf.json -> $MERGED_REL ; lower to local_lf"
  else
    MERGED_ABS="$FB/$MERGED_REL"; mkdir -p "$(dirname "$MERGED_ABS")"
    # Stage the 512 fixture if it isn't present yet.
    if [[ ! -s "$FB/$FIXTURE" ]]; then
      log "fixture $FIXTURE absent — staging via blocks/trainer/tests/smoke/prepare_smoke_data.sh"
      # The dataset repo is private, so staging needs HF_TOKEN from the shared
      # env — without it the download fails with "Invalid username or password"
      # and the merge silently proceeds with tracer output alone.
      ( [[ -f /gpufs/haoli/cicd/shared/.env ]] && { set -a; . /gpufs/haoli/cicd/shared/.env; set +a; }
        bash "$FB/tests/smoke/prepare_smoke_data.sh" "$FB/$FIXTURE" ) \
        || log "WARN: could not stage the fixture dataset"
    fi
    FIXTURE_ABS="$FB/$FIXTURE"; TRAJ_SFT_ABS="$TRAJ_SFT" MERGED="$MERGED_ABS" FIX="$FIXTURE_ABS" python3 - <<'PY'
import json, os, glob
fix = os.environ["FIX"]; updir = os.environ["TRAJ_SFT_ABS"]; out = os.environ["MERGED"]
rows = []
if os.path.isfile(fix):
    try: rows += json.load(open(fix, encoding="utf-8"))
    except Exception as e: print(f"WARN: fixture unreadable: {e}")
n_fix = len(rows)
n_up = 0
for lf in glob.glob(os.path.join(updir, "*", "lf.json")):
    try:
        up = json.load(open(lf, encoding="utf-8")); rows += up; n_up += len(up)
    except Exception as e:
        print(f"WARN: {lf} unreadable: {e}")
json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False)
print(f"merged: {n_fix} fixture + {n_up} reward==1 = {len(rows)} -> {out}")
PY
    # Lower combined_lf -> local_lf for the actual run.
    MERGED_REL="$MERGED_REL" python3 - "$CFG" <<'PY'
import sys, os, yaml
p = sys.argv[1]; d = yaml.safe_load(open(p)) or {}
src = d["runtime_info"]["input"]["source"]
src["type"] = "local_lf"; src["lf_path"] = os.environ["MERGED_REL"]
yaml.safe_dump(d, open(p, "w"), sort_keys=False, allow_unicode=True)
print("lowered trainer source.type -> local_lf")
PY
  fi

  OUT="$(cfg "$CFG" runtime_info.input.training.output_dir)"
  if [[ -z "$R_IP" || "$R_IP" == "local" || "$R_IP" == "null" ]]; then
    # Local GPU training.
    RUN="nohup bash scripts/start.sh >> artifacts/logs/root-smoke-sft.log 2>&1 &"
    ( cd "$FB" && mkdir -p artifacts/logs && claude_launch trainer \
        '( test -d artifacts/env ) && test -d repos && echo setup-ok' \
        'bash scripts/dryrun.sh' "$RUN" 'bash scripts/start.sh' 'trainer' )
    wait_for "$FB/artifacts/model/$OUT/train_results.json" "${BUDGET[trainer]}" first
  else
    # Remote GPU pod: stage the merged data + smoke config, launch start.sh over
    # SSH, poll the pod, and fetch train_results.json back so verify.sh (local)
    # sees it. The persisted checkpoint stays on the pod for serve_checkpoint.sh.
    log "trainer runs on remote pod $R_IP — staging data + launching over SSH"
    bash "$ROOT_DIR/tests/smoke/_remote_sft.sh" "$FB" "$MERGED_REL" "${BUDGET[trainer]}" "$DRY_RUN" \
      || log "WARN: remote trainer helper returned non-zero"
  fi
  if gate trainer; then log "stage trainer PASS"; else
    rc=$?; [[ $rc == 77 ]] && { log "stage trainer SKIP"; CHAIN_RC=77; } || { log "stage trainer FAIL"; CHAIN_RC=1; }
  fi
fi

# ============================================================== stage 4: evaluator =
if want evaluator && [[ "$CHAIN_RC" == 0 ]]; then
  hr; log "STAGE 4/4 — evaluator (serve checkpoint -> swebench-verified 100-subset)"; hr
  overlay evaluator
  EB="$ROOT_DIR/blocks/evaluator"; CFG="$EB/config.yaml"

  # WIRE: stand up vLLM+LiteLLM on the trainer pod, point evaluator at the wrapper.
  if [[ "$DRY_RUN" == 1 ]]; then
    log "[DRY-RUN] serve_checkpoint.sh start ; rewrite evaluator llm_api.api_base_url"
  else
    set +e
    BASE_URL="$(bash "$ROOT_DIR/tests/smoke/serve_checkpoint.sh" start | tail -1)"
    serve_rc=$?
    set -e
    if [[ $serve_rc == 77 ]]; then
      log "serve SKIP — no servable checkpoint/pod; evaluator cannot evaluate the trained model"
      CHAIN_RC=77
    elif [[ $serve_rc != 0 || -z "$BASE_URL" || "$BASE_URL" != http* ]]; then
      log "serve FAIL — could not stand up the checkpoint endpoint"
      CHAIN_RC=1
    else
      # Only rewrite api_base_url. Do NOT touch api_key: serve_checkpoint.sh
      # started vLLM with --api-key = llm_api.api_key (derived from THIS field so
      # the two can't drift), so evaluator's LiteLLM must keep forwarding that same key
      # upstream. Overwriting it with serving.litellm.master_key would 401 every
      # evaluator request against vLLM (they only happen to match today).
      BASE_URL="$BASE_URL" python3 - "$CFG" <<'PY'
import sys, os, yaml
p = sys.argv[1]; d = yaml.safe_load(open(p)) or {}
api = d["runtime_info"]["input"]["llm_api"]
api["api_base_url"] = os.environ["BASE_URL"]
# The edge this declares — llm_api.api_base_url <- trainer.output.checkpoint_path
# — has just been satisfied for real: the checkpoint is served and its URL is
# written above. Leaving the declaration makes validate_config resolve it
# against the trainer block, which restored its template config when its stage
# ended, so checkpoint_path reads null and the whole dryrun fails. Worse, that
# one failure trips dryrun's `FAIL -eq 0` gate and blanks every later config
# read, surfacing as ~12 phantom failures ("harbor.url is empty", ...).
dep = (d.get("meta_info") or {}).get("dependencies") or {}
if isinstance(dep.get("from"), dict):
    dep["from"].pop("llm_api.api_base_url", None)
yaml.safe_dump(d, open(p, "w"), sort_keys=False, allow_unicode=True)
print(f"wired evaluator llm_api.api_base_url -> {os.environ['BASE_URL']}")
print("cleared the now-satisfied dependency on trainer.output.checkpoint_path")
PY
    fi
  fi

  if [[ "$CHAIN_RC" == 0 ]]; then
    JOBS="$(cfg "$CFG" runtime_info.input.harbor_job.jobs_dir)"
    # Clear stale job dirs so policy=first doesn't match a prior run's result.json.
    [[ "$DRY_RUN" != 1 ]] && rm -rf "$EB/$JOBS"
    # #1 fix: Harbor hardcodes ~/.cache/harbor (repo/task cache) and docker buildx
    # uses ~/.docker; docker build scratch uses TMPDIR — all default to the ROOT
    # partition, which ENOSPC'd the first root-smoke (59/100 build failures on a
    # near-full /). Redirect HOME/TMPDIR to a BIG disk (defaults to the workspace
    # fs, which preflight already verified has headroom; override EVAL_CACHE_ROOT
    # if the workspace itself is on the small root fs). Docker image LAYERS still
    # go to the daemon's data-root — this only moves the client-side caches.
    EVAL_CACHE_ROOT="${EVAL_CACHE_ROOT:-$ROOT_DIR/.eval_home}"
    [[ "$DRY_RUN" != 1 ]] && mkdir -p "$EVAL_CACHE_ROOT/.cache" "$EVAL_CACHE_ROOT/.docker" "$EVAL_CACHE_ROOT/tmp"
    RUN="nohup env HOME='$EVAL_CACHE_ROOT' XDG_CACHE_HOME='$EVAL_CACHE_ROOT/.cache' TMPDIR='$EVAL_CACHE_ROOT/tmp' bash scripts/start.sh >> artifacts/logs/root-smoke-eval.log 2>&1 &"
    ( cd "$EB" && mkdir -p artifacts/logs && \
      { [[ -x artifacts/env/harbor-uv/bin/harbor ]] && artifacts/env/harbor-uv/bin/harbor --help >/dev/null 2>&1 || true; } && \
      claude_launch evaluator \
        'test -d artifacts/env && test -d repos && echo setup-ok' \
        'bash scripts/dryrun.sh' "$RUN" 'bash scripts/start.sh' 'evaluator' )
    # Wait for the harbor job process to exit (see tracer note) before grading.
    wait_job "harbor run.*$JOBS" "${BUDGET[evaluator]}" "$EB/$JOBS/*/*/result.json"
    if gate evaluator; then log "stage evaluator PASS"; else
      rc=$?; [[ $rc == 77 ]] && { log "stage evaluator SKIP"; CHAIN_RC=77; } || { log "stage evaluator FAIL"; CHAIN_RC=1; }
    fi
  fi

  # Tear serving down unless asked to keep it.
  if [[ "$DRY_RUN" != 1 && "$KEEP_SERVING" != 1 ]]; then
    bash "$ROOT_DIR/tests/smoke/serve_checkpoint.sh" stop || true
  fi
fi

hr
case "$CHAIN_RC" in
  0)  log "ROOT SMOKE CHAIN: PASS (curator -> tracer -> trainer -> evaluator)"; ;;
  77) log "ROOT SMOKE CHAIN: SKIPPED at a stage (prereq absent — see above)"; ;;
  *)  log "ROOT SMOKE CHAIN: FAILED (see the failing stage above)"; ;;
esac
hr
exit "$CHAIN_RC"
