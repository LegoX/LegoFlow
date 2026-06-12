---
name: run
description: >
  Preflight and launch the rl block in the current working directory.
  Runs /rl:check internally (rejects on any failure), shows the run
  configuration and waits for explicit confirmation, then launches
  scripts/start.sh in the background by default — training takes hours,
  so a foreground default would be wrong here. Run archiving is owned by
  start.sh's EXIT trap → scripts/archive_run.sh, which appends the run_NNN
  entry to artifacts/index.yaml; this skill never pre-writes run state.
  After launch it surfaces the auto-generated exp_name, the PIDs, and
  monitoring commands. Triggers on phrases like "run rl", "launch rl
  training", "kick off the rl block", "start the rl run",
  "fire off rl training", "launch sync_1node_cc".
---

# /rl:run

Preflight, then launch RL training in the background. Refuses if any check
fails, or if there's already a live training process on the box.

## Step 0 — Orient

The "rl block" is the current working directory. Validate:

1. `./config.yaml` exists and `meta_info.name == 'rl'`. Otherwise abort:
   "/rl:run must be run from inside the rl block (`subblock/rl/`)."
2. `./scripts/start.sh` exists.

Read `./config.yaml` and `./CLAUDE.md` for context.

## Step 1 — Refuse if a live run is in flight

A second concurrent run on the same GPUs will OOM or corrupt both. Live
state is the process table — never `config.yaml` (it is one-shot
configuration; per `BLOCK_DEFINITION.md`, run state lives in
`artifacts/index.yaml`, written on exit by `archive_run.sh`). Probe:

```bash
pgrep -af 'sync_1node_cc|train_1node_cc'
pgrep -af 'main_ppo'
```

If any is alive, abort with:

```
A training run is already in flight:
  pids:  <alive pid list with etime — ps -p <pid> -o pid=,comm=,etime=>
  log:   <newest logs/launch_*.log, and repos/harbor-verl-train/logs/<exp>.log>
Refusing to start another. Let it finish, or stop it first
(`kill -INT <pid>` then `bash scripts/clean.sh`), then re-run /rl:run.
```

## Step 2 — Preflight via /rl:check

Invoke the `check` skill's logic on the current block (call into that
skill, or inline its Step 1–3: `bash scripts/dryrun.sh` plus the live
job / port / GPU checks).

If `check` returns **SAFE TO RUN: ❌ NO** (any dryrun failure, any
`job:running`, `port:conflict`, or `gpu:foreign`), abort with the same
consolidated report `/rl:check` prints, prefixed:
"Preflight failed — fix the items below before `/rl:run`."

Do **not** invent values, skip checks, or pass `--force` flags. If the
user says "just run it", explain which check failed and ask them to
fix it. Inputs are user-owned; this skill is a launcher.

## Step 3 — Show run configuration and confirm  (mandatory)

After preflight passes, present a compact configuration summary to the
user and ask for explicit confirmation before proceeding. **Never skip
this** — a full RL run occupies 8 GPUs for hours.

```
┌─ Run Configuration ─────────────────────────────────
│ Model:        <model_path>  (served as: <served_model_name>)
│ Data:         train=<train_index filename>  val=<val_index filename>
│ Backend:      <Docker (remote tcp://...) | Docker (local) | K8s (kubeconfig)>
│ Parallelism:  <num_workers> workers
│ Batch:        <train_batch_size> prompts × <n_resp_per_prompt> resp = <total> trials/step
│ Context:      prompt=<max_prompt_length> + response=<max_response_length>
│ vLLM:         TP=<gen_tp>  max_len=<max_model_length>  gpu_mem=<gpu_memory_utilization>
│ Algorithm:    <adv_estimator> / <policy_loss_mode>  lr=<learning_rate>
│ Epochs:       <total_epochs>  save_freq=<N>  test_freq=<N>
│ Agent:        <agent_name>  timeout=<max_timeout_sec>s  retries=<max_retries>
│ wandb:        <wandb_mode>  project=<project_name>
│ Experiment:   <exp_name or "auto-generated">
└─────────────────────────────────────────────────────
```

Then ask:
```
Proceed with this configuration? [Y]es / [N]o (edit config.yaml first)
```

If the user says No, abort cleanly and tell them to edit `config.yaml`,
then re-run `/rl:run`.

If `dryrun.sh` already printed the summary (it does at the end), you may
reference it instead of reprinting, but you MUST still ask for confirmation.

## Step 4 — Decide launch mode

Default = **background**. RL training is hours-long; running it in the
foreground holds the agent session hostage.

Ask the user once, in a single short line:
```
Launch mode? [B]ackground (default — nohup setsid + writes logs/launch_<ts>.log) / [F]oreground (blocks this session until exit).
```

Accept `B`, `F`, or `<enter>` (= background). Map "background" / "fg" /
"detach" / etc. accordingly.

## Step 5 — Launch metadata

1. **Timestamp**: `TS=$(date -u +%Y%m%d-%H%M%S)`.
2. **Launch log path**: `./logs/launch_${TS}.log`. Create `./logs/` if
   missing. (Deliberately `logs/`, not `artifacts/logs/` — the dashboard
   webui's `serve.sh` reads `logs/launch_*.log` as a data source.)
3. **exp_name**: read `runtime_info.input.experiment.exp_name` from
   `config.yaml`. If empty, leave it empty — `sync_1node_cc.sh`
   auto-generates `harbor-cc-sync-1n-<UTC timestamp>` and that name appears
   in the first few seconds of the launch log; Step 6 parses it back for
   the report.

`start.sh` itself archives the run on exit (its EXIT trap calls
`scripts/archive_run.sh`, which appends the `run_NNN` entry to
`artifacts/index.yaml`). So this skill does **not** write run state into
`config.yaml` and does **not** pre-write an index.yaml row — let the
script own that to avoid double entries.

## Step 6 — Launch

### Background (default)

```bash
nohup setsid bash ./scripts/start.sh > "<launch_log>" 2>&1 < /dev/null &
PARENT_PID=$!
disown
```

After launching:

1. Wait up to 30s (1s polls) for the launch log to grow past 0 bytes.
2. Read its first 80 lines; locate the auto-generated exp_name (the
   upstream script prints something like `[sync_1node_cc] exp=<name>`).
   Surface it in the Step 7 report.
3. Locate the actual `sync_1node_cc.sh` PID and `main_ppo` PID once they
   appear (`pgrep -f sync_1node_cc.sh`, `pgrep -f main_ppo`). Surface them
   too.

If the launch log stays empty for 30s OR `start.sh` exits non-zero within
30s, print the tail of the launch log and tell the user to fix and re-run.
Do NOT retry automatically. (`archive_run.sh` has already recorded the
failed run in `artifacts/index.yaml` via the EXIT trap.)

### Foreground (only if user picked it)

```bash
bash ./scripts/start.sh 2>&1 | tee "<launch_log>"
```

Stream output. On exit, report the final exit code and the tail of the
upstream log. The EXIT trap has already archived the run and appended the
index entry — do not duplicate it.

## Step 7 — Report

Print a tight summary (≤ 12 lines):

```
Launched (background)
  exp_name:     <name or "auto — see launch log in 30s">
  launch log:   logs/launch_<TS>.log
  upstream log: repos/harbor-verl-train/logs/<exp_name>.log
  parent pid:   <pid>
  status:       running (vllm boot + cuda-graph capture, 10–20 min before LiteLLM registers)
  archive:      artifacts/index.yaml entry will be appended by archive_run.sh on exit

Monitor:
  tail -F logs/launch_<TS>.log
  curl -sS http://127.0.0.1:<litellm_port>/health/liveliness
  nvidia-smi
Stop:
  ps -ef | grep -E 'sync_1node_cc|main_ppo' | grep -v grep   # then kill -INT <pid>
```

For foreground runs, swap the "Monitor" section for the final exit code
and the upstream log tail.

## What this skill must NOT do

- Never `--force` past a failed preflight. Tell the user what to fix.
- Never `kill` an existing live training process to make room for a new one. Ask the user to stop it themselves.
- Never edit `runtime_info.input.*` values to make preflight pass. Inputs are user-owned; the skill is just a launcher.
- Never write run state into `config.yaml` or pre-write `artifacts/index.yaml` — `archive_run.sh` (via `start.sh`'s EXIT trap) owns run archiving.
- Never modify anything under `repos/` — pinned, read-only code.
- Never run training in the foreground without asking — it locks the user's session for hours.
- Never archive checkpoints or trial directories. Archival is a separate concern; checkpoints stay where verl wrote them.
- Never run on a remote host. `meta_info.resources.ip` is null for this block today; if it ever changes, refuse and ask the user to migrate the run path.
- There is no `scripts/stop.sh` in this block today; stopping is manual (`kill -INT <pid>` then `bash scripts/clean.sh`). Don't invent one.
