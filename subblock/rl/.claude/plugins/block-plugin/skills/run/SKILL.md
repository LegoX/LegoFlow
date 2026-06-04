---
name: run
description: >
  Preflight and launch the rl block in the current working directory.
  Runs /block:check internally (rejects on any failure), then launches
  scripts/start.sh in the background by default — training takes hours,
  so the foreground default of /block:run is wrong here. After launch,
  stamps status.phase: running with the auto-generated exp_name, parent
  PID, and launch log path in config.yaml; appends a row to
  artifacts/index.yaml; and prints monitoring commands. Does not block
  on completion — archive on exit is left to /block:check / a future
  /block:finish skill. Triggers on phrases like "run rl", "launch rl
  training", "kick off the rl block", "start the rl run",
  "fire off rl training", "launch sync_1node_cc".
---

# /block:run

Preflight, then launch RL training in the background. Refuses if any check
fails, or if there's already a live training process whose PID is recorded
in `status.current_job`.

## Step 0 — Orient

The "rl block" is the current working directory. Validate:

1. `./config.yaml` exists and `meta_info.name == 'rl'`. Otherwise abort:
   "/block:run must be run from inside the rl block (`subblock/rl/`)."
2. `./scripts/start.sh` exists.

Read `./config.yaml` and `./CLAUDE.md` for context.

## Step 1 — Refuse if a live run is in flight

If `status.phase == 'running'` in `config.yaml`:

1. Parse PIDs from `status.current_job.detail` (free-text; look for
   `pid <digits>` patterns).
2. For each PID, `ps -p <pid> -o pid=,comm=,etime=`.
3. If any PID matches `sync_1node_cc|train_1node_cc|main_ppo|bash` AND is
   alive → abort with:
   ```
   A training run is already in flight:
     job:   <status.current_job.name>
     pids:  <alive pid list with etime>
     log:   <log_path from status.current_job.detail>
   Refusing to start another. Stop the current run first
   (`kill <pid>` then `bash scripts/clean.sh`) or wait for it to finish.
   ```
4. If no PIDs are alive but `status.phase == 'running'`, do not abort —
   warn that the status is stale, and tell the user `/block:run` will overwrite
   it. (This is the common case after a crash.)

## Step 2 — Preflight via /block:check

Invoke the `check` skill's logic on the current block (you can call into
that skill, or inline its Step 1–5 directly: `bash scripts/dryrun.sh` plus
the k8s / port / venv / submodule checks).

If any **failure** is reported (warnings are fine), abort with the same
consolidated report `/block:check` would have printed, prefixed:
"Preflight failed — fix the items below before `/block:run`."

Do **not** invent values, skip checks, or pass `--force` flags. If the
user says "just run it", explain which check failed and ask them to
fix it.

## Step 2.5 — Show run configuration and confirm

After preflight passes, present a compact configuration summary to the
user and ask for explicit confirmation before proceeding. This is
**mandatory** — never skip it. The summary must include:

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
then re-run `/block:run`.

If `dryrun.sh` already printed the summary (it does at the end), you may
reference it instead of reprinting, but you MUST still ask for confirmation.

## Step 3 — Decide launch mode

Default = **background**. RL training is hours-long; running it in the
foreground holds the agent session hostage.

Ask the user once, in a single short line:
```
Launch mode? [B]ackground (default — nohup setsid + writes logs/launch_<ts>.log) / [F]oreground (blocks this session until exit).
```

Accept `B`, `F`, or `<enter>`. If the user replies "background" / "fg" /
"detach" / etc., map accordingly.

## Step 4 — Generate launch metadata

1. **Timestamp**: `TS=$(date -u +%Y%m%d-%H%M%S)`.
2. **Launch log path**: `./logs/launch_${TS}.log`. Create `./logs/` if
   missing.
3. **Run id**: scan `./artifacts/index.yaml` for the highest `run_NNN`,
   pick the next zero-padded id. Initialise `artifacts/index.yaml` with
   `runs: []` if it's missing or empty.
4. **exp_name**: read `runtime_info.input.experiment.exp_name` from
   `config.yaml`. If empty, leave it empty — `sync_1node_cc.sh` will
   auto-generate `harbor-cc-sync-1n-<UTC timestamp>` and that name will
   appear in the first few seconds of the launch log; Step 6 parses it back.

## Step 5 — Stamp running state (before launch)

Update `./config.yaml` (preserve all other fields and YAML formatting as
best you can — read–parse–write with `ruamel.yaml` or a careful
text-edit; never blow away comments):

```yaml
status:
  phase: running
  progress: "Launched <TS> — vllm boot + cuda-graph capture (10–20 min before LiteLLM registers)"
  next_steps: |
    Monitor:
      tail -F <launch_log>
      curl -sS http://127.0.0.1:<litellm_port>/health/liveliness   # once vLLM registers
      nvidia-smi
  blockers: null
  last_updated: "<UTC now ISO>"
  current_job:
    name: <exp_name or "auto — see launch log">
    started_at: "<UTC now ISO>"
    detail: |
      run id: <run_NNN>
      launch mode: <background|foreground>
      launch log: <launch_log>
      parent pid: <to be filled in Step 6>
      upstream log: repos/harbor-verl-train/logs/<exp_name>.log
```

Append to `./artifacts/index.yaml`:

```yaml
- id: <run_NNN>
  started_at: "<UTC now ISO>"
  status: running
  archive: artifacts/runs/<exp_name>/    # if /block:create scaffolded a slot for this exp_name; else null
  notes: "<short summary — derive from experiment.yaml if the slot exists; else ask the user briefly>"
```

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
   upstream script prints something like `[sync_1node_cc] exp=<name>`
   or sets it as an env var echo). If found, update
   `status.current_job.name` and the matching `artifacts/index.yaml` row.
3. Locate the actual `sync_1node_cc.sh` PID and `main_ppo` PID once they
   appear (`pgrep -f sync_1node_cc.sh`, `pgrep -f main_ppo`). Add both
   to `status.current_job.detail`.

If the launch log stays empty for 30s OR exits within 30s with non-zero,
abort: revert `status.phase` to `failed` with the tail of the launch log
in `status.blockers`, mark the `artifacts/index.yaml` row `failed`, and
print the tail. Do NOT retry — the user fixes and re-runs.

### Foreground (only if user picked it)

```bash
bash ./scripts/start.sh 2>&1 | tee "<launch_log>"
```

Stream output. On exit, update `status.phase` to `done` (rc 0) or `failed`
(rc != 0), set `current_job.completed_at` and `current_job.exit_code`,
and update the matching `artifacts/index.yaml` row.

## Step 7 — Report

Print a tight summary (≤ 12 lines):

```
Launched (background)
  run id:       run_NNN
  exp_name:     <name or "auto — see launch log in 30s">
  launch log:   logs/launch_<TS>.log
  upstream log: repos/harbor-verl-train/logs/<exp_name>.log
  parent pid:   <pid>
  status:       running (vllm boot + cuda-graph capture)

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
- Never run training in the foreground without asking — it locks the user's session for hours.
- Never archive checkpoints or trial directories. Archival is a separate concern; checkpoints stay where verl wrote them.
- Never run on a remote host. `meta_info.resources.ip` is null for this block today; if it ever changes, refuse and ask the user to migrate the run path.
