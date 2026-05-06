# sft

SFT training pipeline for SWE-bench coding models.

## Block Identity

- **Name**: sft
- **Role**: Training - supervised fine-tuning on agent trajectories
- **Parent**: swe_lego_live
- **Children**: none (leaf block)

## What To Read First

1. `config.yaml` — block identity, training config, runtime values, and status
2. `dashboard/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml` → `runtime_info.input`):
- `source`: Raw trajectory source (provider, scaffold, job_dir, trajs_dir)
- `conversion`: Data conversion settings (max_instances, exclude_repos_file, data_name)
- `dataset`: LLaMA-Factory dataset registration
- `model`: Base model path and config
- `training`: Training hyperparameters (stage, deepspeed, template, cutoff_len, batch size, learning rate, epochs, etc.)
- `infrastructure`: GPU configuration
- `experiment`: WandB tracking settings
- `credentials`: API keys

**Outputs** (written to `config.yaml` → `runtime_info.output`):
- `checkpoint_path`: Path to trained model checkpoint
- `training_metrics`: Final loss, total steps, runtime
- `artifacts`: Paths to train_results.json, loss plot, training log
- `training_curves`: WandB run URL

## Repos

- `repos/LLaMA-Factory/`: Training framework (LLaMA-Factory + DeepSpeed ZeRO-3)
- `repos/swe_data_process/`: Data processing (trajectory converters, quality scorers)

## Environment

Conda env: `swelf` (Python 3.12). See CLAUDE.md in the original file for full setup instructions.

## How To Run

- `scripts/start.sh`: Launch an SFT training run (reads config.yaml)
- `scripts/train.sh`: End-to-end pipeline (data conversion → dataset registration → training)
- `scripts/dataprep.sh`: Data-only pipeline (conversion only, no training)
- `scripts/dryrun.sh`: Validate config, paths, and conda environment
- `scripts/clean.sh`: Remove temporary outputs and logs

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing metadata.yaml, config snapshot, scripts copy, session.log, and monitor.md. Append entry to `artifacts/index.yaml`.

## Memory

Long-form notes, architecture overview, experiment log, and design decisions are kept in `dashboard/memory/`.

## Remote Execution

This block requires 8× A100/H100 GPUs. If `meta_info.resources.ip` is set, execute remotely via SSH + tmux.
