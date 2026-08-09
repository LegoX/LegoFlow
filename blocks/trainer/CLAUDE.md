# trainer

The Trainer block — an SFT (supervised fine-tuning) training pipeline for SWE-bench coding models.

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `blocks/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `<repo_root>/.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` (resolve from the repo root, not from this block's directory). Every agent with this repo SHOULD READ that file before any actions.

## Block Identity

- **Name**: trainer
- **Role**: Training - supervised fine-tuning on agent trajectories
- **Parent**: legoflow
- **Children**: none (leaf block)

## What To Read First

1. `config.yaml` — block identity and one-shot training configuration; live progress comes from the active output directory/dashboard
2. `memory/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml` → `runtime_info.input`):
- `source`: Dataset source. `source.type` is `harbor_job` (default; convert raw trajectories from a supported scaffold + Harbor `job_dir`), `hf_lf` (load a ready-made LF/ShareGPT dataset from `source.hf_hub_url`), or `local_lf` (register an existing LF json at `source.lf_path`). For `hf_lf`, a non-empty `source.hf_file_name` downloads and registers only that Hub file; when empty, LLaMA-Factory loads the selected Hub dataset config/split at train time. `hf_lf`/`local_lf` skip trajectory conversion.
- `conversion`: Data conversion settings (max_instances, exclude_repos_file, data_name). For `hf_lf`/`local_lf`, only `data_name` (dataset key) and `max_instances` (→ `num_samples`) apply.
- `dataset`: LLaMA-Factory dataset registration
- `model`: Base model path and config
- `training`: Training hyperparameters (stage, deepspeed, template, cutoff_len, batch size, learning rate, epochs, etc.)
- `infrastructure`: GPU configuration
- `experiment`: WandB tracking settings
- `credentials`: optional local-only fallback values; prefer private runtime environment variables for API keys

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

`scripts/install_env.sh` recreates the env, installs PyTorch 2.10.0 CUDA 12.8 wheels, the editable `swe_data_process[llm]` and patched LLaMA-Factory repos plus metrics/DeepSpeed/Liger requirements, builds `flash-attn` against that PyTorch, installs Qwen3.5's `flash-linear-attention` and `tilelang` dependencies, then installs `wandb`.

## How To Run

- `scripts/start.sh`: Launch an SFT training run (reads config.yaml)
- `scripts/install_env.sh`: Recreate the uv training environment and install local repos
- `scripts/train.sh`: End-to-end pipeline (obtain/convert LF data → dataset registration → training)
- `scripts/dataprep.sh`: Data-only pipeline (conversion only, no training)
- `scripts/dryrun.sh`: Validate config, paths, and uv environment
- `scripts/clean.sh`: Remove a run's temporary output (logs, generated training YAML, offline WandB state). Keeps the uv env, `artifacts/data/` and `artifacts/model/`. `--all` wipes `artifacts/` entirely except git-tracked files, confirming twice

## Artifact Archiving

After each successful run, `scripts/train.sh` updates `config.yaml.runtime_info.output`.
If archiving is needed, create `artifacts/archives/run_NNN/` containing metadata,
a credential-redacted config snapshot, and a scripts copy, then append the
terminal record to `artifacts/index.yaml`.

## Dashboard

`dashboard/` is a real-time, read-only web UI (React + a Python `server.py`) for
monitoring training runs. It parses `artifacts/model/<run>/`
(`trainer_log.jsonl`, `trainer_state.json`, `*_results.json`) and the console
logs under `artifacts/logs/`, and can compare multiple runs or connect to wandb.

```bash
cd dashboard && ./start_dashboard.sh    # builds the frontend (first run), serves :8091
```

Defaults read `../artifacts/model` (runs) and `../artifacts/logs` (logs); set
`TUNNEL=true` for a Cloudflare quick tunnel. See `dashboard/README.md`. It only
monitors — it never launches or controls training.

## Memory

Long-form notes, architecture overview, experiment log, and design decisions are kept in `memory/`.

## Remote Execution

This block requires 8× A100/H100 GPUs. If `meta_info.resources.ip` is set, execute remotely via SSH + tmux.
