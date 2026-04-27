# sft-train

This file declares that the current directory is a `block`.

## Block Summary

```md
Name: sft-train
Type: training
Main doc: `dashboard/overview.mdx`
Meta info: `metainfo.yaml`
Status: `status.yaml`
Definition reference: `BLOCK_DEFINITION.md`
```

## Functional Positioning

This block is responsible for SFT (supervised fine-tuning) training of coding models on
SWE-bench trajectory data.

It exists to coordinate the end-to-end pipeline from raw agent trajectories to a
fine-tuned model checkpoint: converting trajectories from multiple agent scaffolds
(Claude Code, OpenCode, OpenHands, OpenHands SDK, Terminus2) into LLaMA-Factory
sharegpt format, and training models via LLaMA-Factory + DeepSpeed.

Its boundary is:
- **in scope**: trajectory data conversion, quality scoring, training data registration,
  LLaMA-Factory SFT training, experiment tracking
- **out of scope**: agent trajectory collection, downstream deployment of trained
  checkpoints, RL training (see `rl-train` block)

## Inputs

This block depends on the following input categories:
- `raw_trajectories`: per-scaffold raw trajectory files (jierun or chaofan format)
- `base_model`: model checkpoint to initialize training from (configurable)
- `lf_dataset`: LLaMA-Factory sharegpt JSON produced by data conversion scripts
- `training_fields`: model, dataset, and runtime fields in `inputs.yaml` used to generate the train YAML
- `excluded_repos`: repo exclusion list for filtering eval benchmark repos out of training data

Input readiness rule:
- this block can start when raw trajectories exist and the base model is accessible
- this block is blocked when the `swelf` conda environment is not set up

## Outputs

This block produces:
- `checkpoint_path`: path to the trained model checkpoint (in `artifacts/model/`)
- `training_curves`: WandB run with loss, learning rate, and token metrics
- `training_log`: per-run stdout/stderr log (in `artifacts/logs/`)
- `train_results`: path to `train_results.json` (epoch, loss, runtime, throughput)
- `train_loss_plot`: path to `training_loss.png` loss curve
- `final_loss`: final training loss value
- `total_steps`: total training steps completed
- `train_runtime`: total training wall time in seconds

## Artifacts And Memory

Artifacts stored by this block include:
- `excluded_repos`: `artifacts/data/excluded_repos.txt` — 64-entry repo exclusion list
- `model_checkpoints`: `artifacts/model/` — trained model checkpoints

Long-form memory maintained by this block includes:
- `memory/notes.md`: architecture overview, data flow, config gotchas, experiment log,
  known failure modes
- `memory/decisions.md`: design decisions with rationale

## Parent And Child Relationships

Parent relationship:
- parent block: none (root block)
- this block receives raw trajectory data and base model from upstream infra
- this block reports back trained checkpoints

Child relationship:
- child blocks under `subblock/`: none yet

## Environment

Conda env: `swelf` (Python 3.12)

**Setup (first time):**
```bash
conda create -n swelf python=3.12 -y
conda activate swelf

pip install -e repos/swe_data_process/

pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
pip install -e 'repos/LLaMA-Factory/[torch,metrics,deepspeed,liger-kernel]' --no-build-isolation

# install flash-attn
wget https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.8cxx11abiFALSE-cp312-cp312-linux_x86_64.whl
pip install flash_attn-2.8.3+cu12torch2.8cxx11abiFALSE-cp312-cp312-linux_x86_64.whl

pip install wandb
```

**Key package versions:**

| Package | Version | Note |
|---|---|---|
| llamafactory | latest | editable from `repos/LLaMA-Factory/` |
| deepspeed | — | ZeRO-3 config at `artifacts/training_config/deepspeed/ds_z3_config.json` |
| torch | — | GPU required (8×GPU per node) |
| wandb | — | set `wandb_api_key` in `inputs.yaml` |
| liger-kernel | — | efficient triton kernels (`enable_liger_kernel: true`) |
| flash-attn | — | flash attention (`flash_attn: fa2`) |

## Configuration

All training variables (model path, data paths, experiment name, hyperparameters,
credentials) are in `inputs.yaml`. Edit this file before running any script.
All paths are relative to the block root unless otherwise noted.
See `memory/inputs_参数说明.md` for a full Chinese reference of all parameters.

The experiment tracking table lives in
`artifacts/实验追踪表.xlsx` (single source of truth). New rows are appended
automatically by `scripts/update_tracking.py` (called by `train.sh` after each run).

## Execution Interface

Available scripts:
- `scripts/start.sh`: entry point — launch an SFT training run (reads `inputs.yaml`)
- `scripts/train.sh`: end-to-end SFT pipeline (data conversion → dataset registration → training)
- `scripts/dataprep.sh`: data-only pipeline — conversion only, no training
- `scripts/dryrun.sh`: validate config, paths, and conda environment without training
- `scripts/clean.sh`: remove temporary outputs and logs
- `scripts/generate_excluded_repos.py`: generate `artifacts/data/excluded_repos.txt` from HuggingFace reference datasets (one-time)
- `scripts/update_tracking.py`: append a row to `artifacts/实验追踪表.xlsx` after a training run (called by `train.sh`)
- `scripts/update_status.py`: generate/refresh `dashboard/status.mdx` with current training state (auto-run by `train.sh`, also standalone)

Dryrun expectation:
- `scripts/dryrun.sh` should verify the swelf environment, model path, LF dataset path, dataset registration update behavior, WandB mode, and generated train YAML destination.

## Collaboration Rules

When updating this block:
- read `dashboard/overview.mdx` first for current state
- read `metainfo.yaml` for block identity, resources, and dependency wiring
- read `status.yaml` for live job progress, results, and next steps
- use `inputs.yaml` for all configurable training parameters
- use `outputs.yaml` to record the latest checkpoint and run info
- after every run, archive params, metrics, inputs, and log into `artifacts/files/run_NNN/` and append to `artifacts/index.yaml`
- use `artifacts/` for raw evidence (logs, score results, conversion summaries)
- use `memory/` for long-form context and experiment notes
- data processing code lives at: `repos/swe_data_process/`
- training framework lives at: `repos/LLaMA-Factory/`
