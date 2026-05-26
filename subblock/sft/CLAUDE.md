# sft

SFT training pipeline for SWE-bench coding models.

## Block Identity

- **Name**: sft
- **Role**: Training - supervised fine-tuning on agent trajectories
- **Parent**: swe_lego_live
- **Children**: none (leaf block)

## What To Read First

1. `config.yaml` — block identity, training config, runtime values (one-shot per run; live state in `artifacts/index.yaml`)
2. `dashboard/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml` → `runtime_info.input`):
- `source`: Raw trajectory source (supported scaffold and Harbor `job_dir`)
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
- `training_curves`: WandB run id, when available

## Repos

- `repos/LLaMA-Factory/`: Training framework (LLaMA-Factory + DeepSpeed ZeRO-3)
- `repos/swe_data_process/`: Installable data processing package. Runtime scripts call `python -m swe_data_process...` with `PYTHONPATH=repos/swe_data_process/src`.

## Environment

uv env: `artifacts/env/lf` (Python 3.12 by default, configured at `meta_info.environment.sft_uv`). Prepare or refresh it with:

```bash
bash scripts/install_env.sh
```

`scripts/install_env.sh` recreates the env, installs `repos/swe_data_process[llm]` from the Tsinghua PyPI mirror, installs PyTorch 2.8.0 CUDA 12.8 wheels, installs `repos/LLaMA-Factory[torch,metrics,deepspeed,liger-kernel]` with `--no-build-isolation`, installs the pinned flash-attn 2.8.3 wheel, then installs `wandb`.

## How To Run

- `scripts/start.sh`: Launch an SFT training run (reads config.yaml)
- `scripts/install_env.sh`: Recreate the uv training environment and install local repos
- `scripts/train.sh`: End-to-end pipeline (data conversion → dataset registration → training)
- `scripts/dataprep.sh`: Data-only pipeline (conversion only, no training)
- `scripts/dryrun.sh`: Validate config, paths, and uv environment
- `scripts/clean.sh`: Remove temporary outputs; artifact deletion requires `--artifacts --yes`

## Artifact Archiving

After each successful run, `scripts/train.sh` updates `config.yaml.runtime_info.output`,
refreshes `dashboard/status.mdx`, and appends a row to `artifacts/实验追踪表.xlsx`.
If archiving is needed, create `artifacts/archives/run_NNN/` containing metadata.yaml,
config snapshot, scripts copy, session.log, and monitor.md, then append an entry to
`artifacts/index.yaml`.

## Memory

Long-form notes, architecture overview, experiment log, and design decisions are kept in `dashboard/memory/`.

## Remote Execution

This block requires 8× A100/H100 GPUs. If `meta_info.resources.ip` is set, execute remotely via SSH + tmux.
