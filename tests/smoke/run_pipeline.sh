#!/usr/bin/env bash
# ROOT end-to-end smoke orchestrator.
#
# Runs the full pipeline as ONE chain, fail-fast, the SAME way the per-block CI
# smokes run each block: a narrow `claude -p` (Claude SDK) launcher that nohups
# the long-running command and exits, plus a bash poller that waits for the
# stage's terminal artifact. Between stages it wires each block's REAL output
# into the next, and stops the moment a stage's verify fails.
#
#   stage 1 swegen  : collect ~200 PRs from scratch -> generate verified tasks
#   stage 2 trajgen : infer trajectories on those verified tasks -> keep reward==1
#                     -> convert to LF SFT data
#   stage 3 sft     : train on the 512 fixture + trajgen reward==1 LF (combined)
#                     -> PERSIST a checkpoint   (remote GPU pod)
#   stage 4 eval    : serve that checkpoint (vLLM+LiteLLM on the pod) -> evaluate
#                     on the SWE-bench Verified 100-subset
#
# Usage:
#   bash tests/smoke/run_pipeline.sh [--from <stage>] [--to <stage>]
#                                    [--budget <sec>] [--keep-serving] [--dry-run]
#
# Stages: swegen trajgen sft eval. --from/--to bound which run (default all).
# Each stage overlays tests/smoke/<block>/config.yaml onto
# subblock/<block>/config.yaml; the originals are restored on exit.
#
# Env:
#   CLAUDE_SDK=0   launch stages with a direct nohup instead of `claude -p`
#                  (for hosts without the Claude CLI; default 1 = use the SDK).
#
# Exit 0 = full chain PASS, 77 = a stage SKIP'd on an absent prereq (chain
# stopped, not a failure), 1 = a stage FAILED.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAGES=(swegen trajgen sft eval)
FROM="swegen"; TO="eval"
BUDGET_OVERRIDE=""
KEEP_SERVING=0
DRY_RUN=0
CLAUDE_SDK="${CLAUDE_SDK:-1}"

# Per-stage default budgets (seconds). swegen gets 4h: a genuine ~200-PR
# from-scratch collection + create-to-verified is slow. trajgen/eval use
# policy=first early-exit so these are just upper bounds on the wait.
# sft: 50 steps at 128K cutoff on an 8B model (DeepSpeed ZeRO-3) is ~4 min/step
# (~3.3h), plus model load + tokenization — 4h cap.
declare -A BUDGET=( [swegen]=14400 [trajgen]=10800 [sft]=14400 [eval]=5400 )

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
  local prod="$ROOT_DIR/subblock/$b/config.yaml"
  local smoke="$ROOT_DIR/tests/smoke/$b/config.yaml"
  [[ -f "$smoke" ]] || { echo "FAIL: missing $smoke"; exit 1; }
  [[ "$DRY_RUN" == 1 ]] && { log "[DRY-RUN] would overlay tests/smoke/$b/config.yaml -> subblock/$b/config.yaml"; return 0; }
  cp "$prod" "$prod.root-smoke-bak.$$"
  _BACKUPS+=("$prod")
  cp "$smoke" "$prod"
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
  log "overlaid tests/smoke/$b/config.yaml -> subblock/$b/config.yaml"
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
"Root-smoke stage for ${label}. The smoke config is overlaid at subblock/${block}/config.yaml. cwd is subblock/${block}.

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
      (( absent >= 6 )) && { log "  harbor job never started (3 min) — proceeding to verify"; return 0; }
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

CHAIN_RC=0
hr; log "ROOT SMOKE CHAIN  from=$FROM to=$TO  claude_sdk=$CLAUDE_SDK  dry_run=$DRY_RUN"; hr
[[ "$DRY_RUN" == 1 ]] && log "NOTE: dry-run skips the config overlay, so smoke-only launch params (max_pr, collect.*, ...) render blank below — they populate from tests/smoke/<block>/config.yaml in a real run."

# ============================================================ stage 1: swegen =
if want swegen; then
  hr; log "STAGE 1/4 — swegen (collect ~200 PRs -> verified tasks)"; hr
  overlay swegen
  SB="$ROOT_DIR/subblock/swegen"; CFG="$SB/config.yaml"
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
  C_MINIDS="$(cfg "$CFG" runtime_info.input.smoke.collect.min_ids)"; C_MINIDS="${C_MINIDS:-8}"
  IDS_FILE="$SB/$C_OUT/${C_LANG}_pr_ids.txt"

  # PREP: from-scratch PR collection (best-effort) + committed fallback top-up.
  if [[ "$DRY_RUN" == 1 ]]; then
    log "[DRY-RUN] collect_prs_wo_image.py --languages $C_LANG --repo_num $C_REPON --max_prs_per_repo $C_MAXPR (budget ${C_BUDGET}s) -> $IDS_FILE; top up from fallback if < $C_MINIDS; head -$C_TARGET"
  else
    mkdir -p "$SB/$C_OUT" "$SB/$BASE/$SUB" "$SB/$BASE/$STATE"
    log "from-scratch PR collection ($C_LANG), best-effort budget ${C_BUDGET}s"
    # The collector imports the swegen package and reads its OWN gh_token.txt
    # (it ignores GITHUB_TOKENS), and can futex-stall at init (0 sockets,
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
    # swegen's CC verification path (the half that writes verifiable_tasks.txt)
    # uses cc_provider_mode: openai_proxy, so it needs a local LiteLLM proxy on
    # cc_proxy_port translating Anthropic -> the upstream OpenAI endpoint. Unlike
    # the per-block smoke (10_pr_demo.sh), this orchestrator builds its own
    # swegen-create, so it must start the proxy itself — otherwise dryrun.sh's
    # /health check fails and verification SILENTLY banks 0 tasks (the failure
    # that sank the first root run). No-op when cc_provider_mode != openai_proxy;
    # torn down after the swegen gate. shellcheck source=/dev/null
    source "$SB/scripts/cc_proxy_lib.sh"
    [[ "$DRY_RUN" == 1 ]] || cc_proxy_start "$SB" "$CFG" \
      || log "WARN: CC LiteLLM proxy did not start — swegen dryrun/verification will fail"
    RUN="source scripts/load_runtime_env.sh && load_runtime_env >/dev/null 2>&1 ; source artifacts/envs/swegen-env/bin/activate ; nohup swegen create --input-ids-file ${IDS_FILE#$SB/} --max-pr ${MAXPR} --n-concurrent ${NCONC} --output ${BASE}/${SUB} --state-dir ${BASE}/${STATE} --timeout ${TO_} --cc-timeout ${CCTO} --no-require-issue --min-source-files ${MINSF} --max-source-files ${MAXSF} --docker-prune-batch ${DPB} --verbose >> artifacts/logs/root-smoke-swegen.log 2>&1 &"
    ( cd "$SB" && mkdir -p artifacts/logs && claude_launch swegen \
        '( test -d artifacts/envs || test -d artifacts/env ) && test -d repos && echo setup-ok' \
        'bash scripts/dryrun.sh' "$RUN" 'swegen create --input-ids-file' 'swegen' )
    # Wait until swegen banks up to max_pr verified tasks (not just the first) so
    # trajgen gets a diverse pool — one hard/unsolvable task shouldn't sink the
    # reward==1 gate. Break early once `create` finishes (PRs exhausted or it hit
    # its own --max-pr) or the budget elapses.
    MAN="$SB/$BASE/$SUB/verifiable_tasks.txt"
    swdl=$(( $(date +%s) + ${BUDGET[swegen]} ))
    log "waiting for swegen to bank up to max_pr=$MAXPR verified task(s) (budget ${BUDGET[swegen]}s)"
    while (( $(date +%s) < swdl )); do
      nver=$([[ -f "$MAN" ]] && grep -c . "$MAN" 2>/dev/null || echo 0)
      if (( nver >= MAXPR )); then log "  swegen banked $nver verified task(s) (>= max_pr=$MAXPR)"; break; fi
      if ! pgrep -f 'swegen create --input-ids-file' >/dev/null 2>&1; then
        log "  swegen create finished — $nver verified task(s) banked"; break
      fi
      sleep 30
    done
    # The nohup'd `swegen create` is NOT bounded by this wait loop — once we've
    # banked enough (or hit the stage budget) it keeps grinding the remaining PRs
    # in the background. Left detached, its continuous Docker/disk writeback makes
    # the NEXT stage's overlay() `sync` block in wb_wait_for_completion and wedge
    # the whole pipeline (observed: trajgen overlay `sync` stuck 44+ min behind 8
    # orphaned create workers). Stop it (and any lingering PR collector) here so
    # `sync` can settle and the chain advances.
    pkill -f 'swegen create --input-ids-file' 2>/dev/null || true
    pkill -f 'collect_prs_wo_image' 2>/dev/null || true
    if gate swegen; then log "stage swegen PASS"; else
      rc=$?; [[ $rc == 77 ]] && { log "stage swegen SKIP"; CHAIN_RC=77; } || { log "stage swegen FAIL"; CHAIN_RC=1; }
    fi
    # Tear down the swegen CC proxy before the next stage (no-op if not started).
    [[ "$DRY_RUN" == 1 ]] || cc_proxy_stop
  fi
fi

# =========================================================== stage 2: trajgen =
if want trajgen && [[ "$CHAIN_RC" == 0 ]]; then
  hr; log "STAGE 2/4 — trajgen (verified tasks -> reward==1 -> LF SFT data)"; hr
  overlay trajgen
  TB="$ROOT_DIR/subblock/trajgen"; CFG="$TB/config.yaml"
  # WIRE: harbor has no "run a task N times" knob, so to give trajgen multiple
  # independent solve attempts (raising the reward==1 gate's odds without
  # re-running swegen) we stage `smoke_attempts` replicas of each swegen verified
  # task into a dir of distinct names (harbor IDs tasks by dir name; task.toml
  # has no id), then point task_source.dataset_name at that staging dir.
  # Read swegen's smoke output location from the STATIC smoke config (it carries
  # the smoke-only output_subdir field) — the production swegen config lacks it,
  # and with --from trajgen swegen is never overlaid, so reading the subblock
  # config would yield an empty subdir and stage 0 tasks.
  SWE_SMOKE_CFG="$ROOT_DIR/tests/smoke/swegen/config.yaml"
  SWE_SUB="$(cfg "$SWE_SMOKE_CFG" runtime_info.input.smoke.output_subdir)"
  SWE_BASE="$(cfg "$SWE_SMOKE_CFG" runtime_info.output.swe_tasks_dir.path)"
  SWE_ABS="$ROOT_DIR/subblock/swegen/$SWE_BASE/$SWE_SUB"
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
print(f"wired trajgen task_source.dataset_name -> {src}")
PY
  fi
  JOBS="$(cfg "$CFG" runtime_info.input.harbor_job.jobs_dir)"
  # Clear stale job dirs + prepared-task cache from any prior smoke run, else
  # policy=first would match an old result.json instantly and verify the wrong
  # job. jobs_dir is smoke-isolated (artifacts/jobs/root-smoke).
  if [[ "$DRY_RUN" != 1 ]]; then
    rm -rf "$TB/$JOBS" "$TB/artifacts/tasks/$(basename "$STAGE_DIR")"
  fi
  RUN="nohup bash scripts/start.sh >> artifacts/logs/root-smoke-trajgen.log 2>&1 &"
  ( cd "$TB" && mkdir -p artifacts/logs && \
    { [[ "$DRY_RUN" == 1 ]] && echo "[DRY-RUN] prepare_tasks.sh" || bash scripts/prepare_tasks.sh || log "WARN: prepare_tasks.sh non-zero"; } && \
    claude_launch trajgen \
      'test -d artifacts/env && test -d repos && echo setup-ok' \
      'bash scripts/dryrun.sh' "$RUN" 'bash scripts/start.sh' 'trajgen' )
  # Wait for the harbor job PROCESS to exit (NOT for a result.json to appear —
  # harbor writes the job-level result.json before the trials are actually done,
  # and exiting then tears the LiteLLM proxy down under the still-running trials
  # -> ConnectionRefused). Only after the job truly finishes does verify.sh grade
  # the per-task rewards (needs >=1 reward==1 across the replicas).
  wait_job "harbor run.*$JOBS" "${BUDGET[trajgen]}" "$TB/$JOBS/*/*/result.json"
  if gate trajgen; then log "stage trajgen PASS"; else
    rc=$?; [[ $rc == 77 ]] && { log "stage trajgen SKIP"; CHAIN_RC=77; } || { log "stage trajgen FAIL"; CHAIN_RC=1; }
  fi
fi

# =============================================================== stage 3: sft =
if want sft && [[ "$CHAIN_RC" == 0 ]]; then
  hr; log "STAGE 3/4 — sft (512 fixture + trajgen reward==1 -> trained checkpoint)"; hr
  overlay sft
  FB="$ROOT_DIR/subblock/sft"; CFG="$FB/config.yaml"
  FIXTURE="$(cfg "$CFG" runtime_info.input.source.fixture_lf)"
  UPDIR="$(cfg "$CFG" runtime_info.input.source.upstream_lf_dir)"
  R_IP="$(cfg "$CFG" meta_info.resources.ip)"
  # The trajgen reward==1 LF lives on the CI host under subblock/trajgen/<out_dir>.
  TRAJ_SFT="$ROOT_DIR/subblock/trajgen/$(cfg "$ROOT_DIR/subblock/trajgen/config.yaml" runtime_info.input.sft_conversion.out_dir)"

  # WIRE: merge fixture + every trajgen lf.json into one combined LF, then lower
  # source.type to the sft block's native local_lf. For a remote pod this merge
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
      log "fixture $FIXTURE absent — staging via subblock/sft/tests/smoke/prepare_smoke_data.sh"
      bash "$FB/tests/smoke/prepare_smoke_data.sh" "$FB/$FIXTURE" || log "WARN: could not stage 512 fixture"
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
print("lowered sft source.type -> local_lf")
PY
  fi

  OUT="$(cfg "$CFG" runtime_info.input.training.output_dir)"
  if [[ -z "$R_IP" || "$R_IP" == "local" || "$R_IP" == "null" ]]; then
    # Local GPU training.
    RUN="nohup bash scripts/start.sh >> artifacts/logs/root-smoke-sft.log 2>&1 &"
    ( cd "$FB" && mkdir -p artifacts/logs && claude_launch sft \
        '( test -d artifacts/env ) && test -d repos && echo setup-ok' \
        'bash scripts/dryrun.sh' "$RUN" 'bash scripts/start.sh' 'sft' )
    wait_for "$FB/artifacts/model/$OUT/train_results.json" "${BUDGET[sft]}" first
  else
    # Remote GPU pod: stage the merged data + smoke config, launch start.sh over
    # SSH, poll the pod, and fetch train_results.json back so verify.sh (local)
    # sees it. The persisted checkpoint stays on the pod for serve_checkpoint.sh.
    log "sft runs on remote pod $R_IP — staging data + launching over SSH"
    bash "$ROOT_DIR/tests/smoke/_remote_sft.sh" "$FB" "$MERGED_REL" "${BUDGET[sft]}" "$DRY_RUN" \
      || log "WARN: remote sft helper returned non-zero"
  fi
  if gate sft; then log "stage sft PASS"; else
    rc=$?; [[ $rc == 77 ]] && { log "stage sft SKIP"; CHAIN_RC=77; } || { log "stage sft FAIL"; CHAIN_RC=1; }
  fi
fi

# ============================================================== stage 4: eval =
if want eval && [[ "$CHAIN_RC" == 0 ]]; then
  hr; log "STAGE 4/4 — eval (serve checkpoint -> swebench-verified 100-subset)"; hr
  overlay eval
  EB="$ROOT_DIR/subblock/eval"; CFG="$EB/config.yaml"

  # WIRE: stand up vLLM+LiteLLM on the sft pod, point eval at the wrapper.
  if [[ "$DRY_RUN" == 1 ]]; then
    log "[DRY-RUN] serve_checkpoint.sh start ; rewrite eval llm_api.api_base_url"
  else
    set +e
    BASE_URL="$(bash "$ROOT_DIR/tests/smoke/serve_checkpoint.sh" start | tail -1)"
    serve_rc=$?
    set -e
    if [[ $serve_rc == 77 ]]; then
      log "serve SKIP — no servable checkpoint/pod; eval cannot evaluate the trained model"
      CHAIN_RC=77
    elif [[ $serve_rc != 0 || -z "$BASE_URL" || "$BASE_URL" != http* ]]; then
      log "serve FAIL — could not stand up the checkpoint endpoint"
      CHAIN_RC=1
    else
      LKEY="$(cfg "$CFG" runtime_info.input.serving.litellm.master_key)"
      BASE_URL="$BASE_URL" LKEY="$LKEY" python3 - "$CFG" <<'PY'
import sys, os, yaml
p = sys.argv[1]; d = yaml.safe_load(open(p)) or {}
api = d["runtime_info"]["input"]["llm_api"]
api["api_base_url"] = os.environ["BASE_URL"]
api["api_key"] = os.environ.get("LKEY") or api.get("api_key")
yaml.safe_dump(d, open(p, "w"), sort_keys=False, allow_unicode=True)
print(f"wired eval llm_api.api_base_url -> {os.environ['BASE_URL']}")
PY
    fi
  fi

  if [[ "$CHAIN_RC" == 0 ]]; then
    JOBS="$(cfg "$CFG" runtime_info.input.harbor_job.jobs_dir)"
    # Clear stale job dirs so policy=first doesn't match a prior run's result.json.
    [[ "$DRY_RUN" != 1 ]] && rm -rf "$EB/$JOBS"
    RUN="nohup bash scripts/start.sh >> artifacts/logs/root-smoke-eval.log 2>&1 &"
    ( cd "$EB" && mkdir -p artifacts/logs && \
      { [[ -x artifacts/env/harbor-uv/bin/harbor ]] && artifacts/env/harbor-uv/bin/harbor --help >/dev/null 2>&1 || true; } && \
      claude_launch eval \
        'test -d artifacts/env && test -d repos && echo setup-ok' \
        'bash scripts/dryrun.sh' "$RUN" 'bash scripts/start.sh' 'eval' )
    # Wait for the harbor job process to exit (see trajgen note) before grading.
    wait_job "harbor run.*$JOBS" "${BUDGET[eval]}" "$EB/$JOBS/*/*/result.json"
    if gate eval; then log "stage eval PASS"; else
      rc=$?; [[ $rc == 77 ]] && { log "stage eval SKIP"; CHAIN_RC=77; } || { log "stage eval FAIL"; CHAIN_RC=1; }
    fi
  fi

  # Tear serving down unless asked to keep it.
  if [[ "$DRY_RUN" != 1 && "$KEEP_SERVING" != 1 ]]; then
    bash "$ROOT_DIR/tests/smoke/serve_checkpoint.sh" stop || true
  fi
fi

hr
case "$CHAIN_RC" in
  0)  log "ROOT SMOKE CHAIN: PASS (swegen -> trajgen -> sft -> eval)"; ;;
  77) log "ROOT SMOKE CHAIN: SKIPPED at a stage (prereq absent — see above)"; ;;
  *)  log "ROOT SMOKE CHAIN: FAILED (see the failing stage above)"; ;;
esac
hr
exit "$CHAIN_RC"
