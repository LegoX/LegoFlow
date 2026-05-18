# sft_training

Trains a language model using supervised fine-tuning (SFT).

**Parent:** root  
**Children:** none (leaf block)

## Read first

1. `config.yaml` — block identity, inputs, outputs, resources, live status
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

After each run, create `artifacts/archives/run_NNN/` containing:

| File | Content |
|------|---------|
| `metadata.yaml` | Run id, timestamps, stage, results summary, repo commit ids, copy of inputs |
| `config.yaml` | Snapshot of config.yaml as it was at run time |
| `scripts/` | Copy of all scripts executed during this run |
| `repo/` | Snapshot or reference of the repo code at the pinned commit |
| `session.log` | Claude Code session record (tool calls, agent reasoning, decisions) |
| `monitor.md` | Human-readable monitor output produced by the agent during the run |

Then append one entry to `artifacts/index.yaml`:
```yaml
- id: run_001
  started_at: "2026-05-04T08:00:00Z"
  completed_at: "2026-05-04T10:11:35Z"
  status: completed
  archive: artifacts/archives/run_001/
  notes: "lr=1e-4, epochs=3; val_loss=0.42"
```

## Status updates

Keep the `status` section in `config.yaml` current throughout execution:
- set `phase` to `running` when starting, `done` or `blocked` when finished
- update `progress`, `next_steps`, `blockers`, and `last_updated` continuously

## Remote execution

`meta_info.resources.ip` is set. Always execute on the remote node:
1. `tmux new-window -n sft_training`
2. SSH into 192.168.1.10 and attach to (or create) a tmux session there
3. Run scripts inside that remote session — never run this block locally
