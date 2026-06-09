# sft_training

Trains a language model using supervised fine-tuning (SFT).

**Parent:** root  
**Children:** none (leaf block)

## Read first

1. `config.yaml` — block identity, inputs, outputs, resources (one-shot per run; live state in `artifacts/index.yaml`)
2. `dashboard/overview.mdx` — human-readable current state

## Input / Output contract

Read from `runtime_info.input` in `config.yaml` before running:
- `dataset_path` — path to the labeled training dataset
- `learning_rate` — float, e.g. 1e-4
- `num_epochs` — int, number of training epochs

Write to `runtime_info.output` in `config.yaml` after running:
- `model_checkpoint` — path to saved model checkpoint

## Repos

- `repos/trl/` — SFT trainer implementation (git submodule, pinned to a1b2c3d4)

## How to run

- `scripts/start.sh` — execute the block
- `scripts/dryrun.sh` — validate inputs and environment without side effects
- `scripts/clean.sh` — reset outputs and artifacts

## Artifact archiving

Each run is archived automatically by `scripts/archive_run.sh`, invoked from `scripts/start.sh`'s EXIT trap. The helper creates `artifacts/archives/run_NNN/` with `metadata.yaml` (id, block, timestamps, status, exit_code, repo SHAs), a `config.yaml` snapshot, and a `scripts/` snapshot, and appends one entry to `artifacts/index.yaml`. Agent may optionally drop `session.log` or `monitor.md` into the archive after the run for additional context.

Example index.yaml entry written by `archive_run.sh`:
```yaml
- id: run_001
  started_at: "2026-05-04T08:00:00Z"
  completed_at: "2026-05-04T10:11:35Z"
  status: completed        # completed | failed | interrupted
  archive: artifacts/archives/run_001/
  notes: ""                # agent may refine after the run
```

`config.yaml` is **one-shot per run** — do not edit it during execution to track progress. Live state lives in `artifacts/index.yaml`.

## Remote execution

`meta_info.resources.ip` is set. Always execute on the remote node:
1. `tmux new-window -n sft_training`
2. SSH into 192.168.1.10 and attach to (or create) a tmux session there
3. Run scripts inside that remote session — never run this block locally
