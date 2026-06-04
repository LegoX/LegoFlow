# Training Decisions

## 2026-05: config.yaml as the single config entry point
**Decision:** All adjustable parameters (model path, dataset, hyperparameters, credentials)
live in `config.yaml` under `runtime_info.input`. Scripts read from this file rather than being hand-edited.
**Why:** Mirrors the rl-train approach. Prevents inconsistencies between the "what we ran"
and "what the script says" — the YAML is the record.

## 2026-05: Train YAML is generated from config.yaml
**Decision:** `scripts/train.sh` generates the LLaMA-Factory YAML at runtime from
`config.yaml.runtime_info.input`, instead of expecting a checked-in `training.train_config_yaml` pointer.
**Why:** This keeps block-level configuration and the executed training config aligned, and
prevents stale YAML files from drifting away from the recorded inputs.

## 2026-05: Use swe_data_process package modules
**Decision:** Data conversion is invoked with `python -m swe_data_process...` modules and
local `PYTHONPATH=repos/swe_data_process/src`. Only job-dir converters are wired:
`claude-code`, `open-code`, `openhands-sdk`, and `terminus2`.
**Why:** The refactored `swe_data_process` repo removed the old source-specific converter scripts.

## 2026-05: uv-managed SFT environment
**Decision:** `scripts/install_env.sh` is the single environment installer for SFT. It
recreates the environment configured by `meta_info.environment.sft_uv`, installs
`swe_data_process[llm]`, installs PyTorch 2.8.0 CUDA 12.8 wheels, installs
`LLaMA-Factory[torch,metrics,deepspeed,liger-kernel]` with `--no-build-isolation`,
installs the pinned flash-attn 2.8.3 wheel under `artifacts/wheels/`, and installs `wandb`.
**Why:** Installing PyTorch and flash-attn in an explicit order avoids slow or incorrect
resolver choices and keeps the local training environment reproducible.

## 2026-04: scripts/train.sh launches torchrun directly on the current node
**Decision:** `scripts/train.sh` launches LLaMA-Factory through its torchrun path on the
current node, with `NPROC_PER_NODE` wired from `infrastructure.n_gpus_per_node`.
**Why:** The block currently targets single-node launches. Keeping execution local avoids a
second layer of Slurm wrapper logic while still making the GPU count explicit in config.

## 2026-04: Repo exclusion filter enabled by default
**Decision:** All data converters default to filtering out repos in `artifacts/data/excluded_repos.txt`.
**Why:** Prevents eval benchmark repos (SWE-bench_Verified, SWE-bench_Pro,
SWE-bench_Multilingual) from leaking into training data. Pass `--exclude-repos-file ""`
to disable for specific experiments.

## 2026-04: 实验追踪表.xlsx as single source of truth for experiment tracking
**Decision:** All experiment metadata (dataset, script, hyperparameters, results) is
maintained only in Excel. `scripts/update_tracking.py` appends rows automatically
after a successful training run.
**Why:** Excel supports structured data entry with cell validation, merged cells, and
comments. Editing Markdown tables by hand is error-prone at scale.

## 2026-04: DeepSpeed ZeRO-3 as default parallelism strategy
**Decision:** Use `ds_z3_config.json` (ZeRO-3, optimizer + parameter offload) for all
8-GPU single-node runs.
**Why:** ZeRO-3 is necessary for 30B+ models. For 8B models it is safe and avoids
needing to tune FSDP sharding manually.

## 2026-04: Separate dataprep.sh for data-only pipeline
**Decision:** `scripts/dataprep.sh` runs only data conversion (STEP 0), without
dataset registration or training.
**Why:** Allows inspecting and validating converted data before committing to a
training run. Keeps the data processing step independently executable.

## 2026-05: Outputs live in config.yaml
**Decision:** `scripts/train.sh` updates `config.yaml.runtime_info.output` after each run
with checkpoint path, artifacts, training metrics, and experiment tracking values. Only the
`runtime_info.output` block is replaced, preserving comments and formatting elsewhere.
**Why:** Keeping inputs and outputs in one block config avoids drift from a separate
`outputs.yaml` file that is not checked in for this block.

## 2026-04: Live status monitoring via dashboard/status.mdx
**Decision:** `scripts/update_status.py` generates `dashboard/status.mdx` with config
summary, live progress, and final results. Auto-launched by `train.sh` in background
(30s refresh), cleaned up by `train.sh` on exit, and also works standalone.
**Why:** Provides a human-readable view of training state without needing to parse
logs or trainer_log.jsonl manually. Viewable directly in IDE.
