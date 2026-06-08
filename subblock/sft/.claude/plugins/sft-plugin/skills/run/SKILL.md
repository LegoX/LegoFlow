---
name: run
description: >
  Preflight and launch the sft block in the current working directory.
  Runs /sft:check internally (rejects on any failure), shows the run
  configuration and waits for explicit confirmation, then launches
  scripts/start.sh — which runs dryrun.sh, then train.sh (trajectory→IM→LF
  conversion, dataset registration, LLaMA-Factory + DeepSpeed ZeRO-3
  training on 8× GPU, WandB tracking), and on exit archives the run via
  scripts/archive_run.sh. Background by default — training is long-running.
  train.sh itself writes runtime_info.output, refreshes dashboard/status.mdx,
  and appends to the tracking table; archive_run.sh appends to
  artifacts/index.yaml. Triggers on phrases like "run sft", "launch sft
  training", "start the sft block", "kick off the supervised fine-tuning",
  "fire off sft".
---

# /sft:run

Preflight, then launch SFT training. Refuses if any check fails, or if
there's already a live training process on the box.

## Step 0 — Orient

The "sft block" is the current working directory. Validate:

1. `./config.yaml` exists and `meta_info.name == 'sft'`. Otherwise abort:
   "/sft:run must be run from inside the sft block (`subblock/sft/`)."
2. `./scripts/start.sh` exists.

Read `./config.yaml` and `./CLAUDE.md` for context.

## Step 1 — Refuse if a live run is in flight

A second concurrent training run on the same GPUs will OOM or corrupt both.
Probe:

```bash
pgrep -af 'llamafactory.cli train'
pgrep -af 'scripts/train.sh'
```

If any is alive, abort with:

```
A training run is already in flight:
  pids:  <alive pid list with etime — ps -p <pid> -o pid=,comm=,etime=>
  log:   <newest artifacts/logs/*.log>
Refusing to start another. Let it finish, or stop it first
(`kill <pid>`), then re-run /sft:run.
```

An orphaned `update_status.py --loop` with no training process is harmless
— note it and continue.

## Step 2 — Preflight via /sft:check

Invoke the `check` skill's logic on the current block (call into that
skill, or inline its Step 1–3: `bash scripts/dryrun.sh` plus the live
job/GPU/checkpoint checks).

If `check` returns **SAFE TO RUN: ❌ NO** (any dryrun `[FAIL]`, any
`job:running`, any `gpu:foreign`), abort with the same consolidated report
`/sft:check` prints, prefixed:
"Preflight failed — fix the items below before `/sft:run`."

Do **not** invent values, skip checks, or edit `runtime_info.input` to make
preflight pass. If the user says "just run it", explain which check failed
and ask them to fix it. Inputs are user-owned; this skill is a launcher.

## Step 3 — Show run configuration and confirm  (mandatory)

After preflight passes, present a compact summary and ask for explicit
confirmation. **Never skip this** — a full SFT run occupies 8 GPUs for
many minutes to hours and overwrites `output_dir` (`overwrite_output_dir:
true`).

```
┌─ sft Run Configuration ─────────────────────────────
│ Scaffold:   <source.scaffold>   job_dir=<...basename>
│ Data:       <conversion.data_name>  (max_instances=<N>)
│ Dataset:    <dataset.name or "auto = data_name">
│ Model:      <model.model_name_or_path>
│ Template:   <training.template>  cutoff=<cutoff_len>  rope=<rope_scaling>
│ Batch:      gbs=<pbs×accum×gpus> (per_device=<pbs> × accum=<accum> × gpu=<n_gpus>)
│ Optim:      lr=<learning_rate>  epochs=<num_train_epochs>  sched=<lr_scheduler_type>
│ Output:     <training.output_dir>  → artifacts/model/<basename>
│ Checkpoint: <ckpt:clean | ckpt:clobber — will overwrite existing checkpoints>
│ wandb:      mode=<wandb_mode>  run_id=<wandb_run_id or "auto">
│ GPUs:       <n_gpus_per_node>
└─────────────────────────────────────────────────────
```

Then ask:
```
Proceed with this configuration? [Y]es / [N]o (edit config.yaml first)
```

If No, abort cleanly and tell them to edit `config.yaml`, then re-run
`/sft:run`.

## Step 4 — Decide launch mode

Default = **background**. SFT training is long; the foreground holds the
agent session hostage. Ask once:

```
Launch mode? [B]ackground (default — nohup setsid + writes logs/launch_<ts>.log) / [F]oreground (blocks this session until exit).
```

Accept `B`, `F`, or `<enter>` (= background). Map "background"/"bg"/"detach"
and "foreground"/"fg" accordingly.

## Step 5 — Launch metadata

1. `TS=$(date -u +%Y%m%d-%H%M%S)`.
2. Launch-log path: `./logs/launch_${TS}.log`. Create `./logs/` if missing.
   (Note: `train.sh` also writes its own detailed log under
   `artifacts/logs/<run_name>_<timestamp>.log`; the launch log is just the
   `start.sh` stdout/stderr capture.)

`start.sh` itself archives the run on exit (its EXIT trap calls
`scripts/archive_run.sh`, which appends the `run_NNN` entry to
`artifacts/index.yaml`). So this skill does **not** pre-write an
index.yaml row — let the script own that to avoid double entries.

## Step 6 — Launch

### Background (default)

```bash
nohup setsid bash ./scripts/start.sh > "./logs/launch_${TS}.log" 2>&1 < /dev/null &
PARENT_PID=$!
disown
```

After launching:

1. Wait up to 30s (1s polls) for the launch log to grow past 0 bytes.
2. Read its first ~80 lines. `start.sh` runs `dryrun.sh` first; if dryrun
   prints `FAIL` and exits non-zero, the run aborts before training — catch
   that and report it (don't claim training started).
3. Once `train.sh` reaches STEP 2 it prints `=== Launching training ===`
   and the actual log path; surface that path. The torchrun PID appears via
   `pgrep -f 'llamafactory.cli train'`.

If the launch log stays empty for 30s, or `start.sh` exits non-zero within
30s, abort: print the tail of the launch log and tell the user to fix and
re-run. Do NOT retry automatically.

### Foreground (only if user picked it)

```bash
bash ./scripts/start.sh 2>&1 | tee "./logs/launch_${TS}.log"
```

Stream output. On exit, report the final exit code and the tail of the
training log. `train.sh`'s STEP 3/4 already wrote
`runtime_info.output.*`, refreshed `dashboard/status.mdx`, and appended to
`artifacts/实验追踪表.xlsx`; `archive_run.sh` appended the index entry.

## Step 7 — Report

Print a tight summary (≤ 12 lines):

```
Launched (background)
  launch log:   logs/launch_<TS>.log
  train log:    artifacts/logs/<run_name>_<...>.log   (appears once STEP 2 starts)
  output dir:   artifacts/model/<basename of output_dir>
  parent pid:   <pid>
  status:       running (STEP 0 conversion → STEP 1 register → STEP 2 train)

Monitor:
  tail -F logs/launch_<TS>.log
  nvidia-smi
  cat dashboard/status.mdx              # refreshed every 30s by update_status.py
Stop:
  pgrep -af 'llamafactory.cli train'    # then: kill -INT <pid>
```

For foreground runs, swap "Monitor" for the final exit code, final loss
(from `runtime_info.output.training_metrics`), and the training-log tail.

## What this skill must NOT do

- Never `--force` past a failed preflight. Tell the user what to fix.
- Never `kill` an existing live training process to make room for a new one.
  Ask the user to stop it themselves.
- Never edit `runtime_info.input.*` to make preflight pass. Inputs are
  user-owned.
- Never modify anything under `repos/` — pinned, read-only code.
- Never run training in the foreground without asking — it can lock the
  session for hours.
- Never run on a remote host. `meta_info.resources.ip` is null for this
  block; if it ever becomes a real IP, refuse and ask the user to confirm
  the remote run path (SSH + tmux) first.
- There is no `scripts/stop.sh` in this block today; stopping is manual
  (`kill <pid>` then `bash scripts/clean.sh`). Don't invent one.
