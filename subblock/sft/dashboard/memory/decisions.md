# Training Decisions

## 2026-04: inputs.yaml as the single config entry point
**Decision:** All adjustable parameters (model path, dataset, hyperparameters, credentials)
live in `inputs.yaml`. Scripts read from this file rather than being hand-edited.
**Why:** Mirrors the rl-train approach. Prevents inconsistencies between the "what we ran"
and "what the script says" — the YAML is the record.

## 2026-04: Train YAML is generated from inputs.yaml
**Decision:** `scripts/train.sh` generates the LLaMA-Factory YAML at runtime from
`inputs.yaml`, instead of expecting a checked-in `training.train_config_yaml` pointer.
**Why:** This keeps block-level configuration and the executed training config aligned, and
prevents stale YAML files from drifting away from the recorded inputs.

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
after each training run.
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

## 2026-04: Unified outputs.yaml format
**Decision:** Every entry in `outputs.yaml` uses three fields: `description`,
`value`, `note`. Added `train_results`, `train_loss_plot`, `final_loss`,
`total_steps`, `train_runtime` fields.
**Why:** The original format was inconsistent (mixed `path`, `value`, `wandb_run_id`
across entries). Unified format is easier to parse and extend. New fields capture
key training metrics directly from LLaMA-Factory output files.

## 2026-04: Live status monitoring via dashboard/status.mdx
**Decision:** `scripts/update_status.py` generates `dashboard/status.mdx` with config
summary, live progress, and final results. Auto-launched by `train.sh` in background
(30s refresh), also works standalone.
**Why:** Provides a human-readable view of training state without needing to parse
logs or trainer_log.jsonl manually. Viewable directly in IDE.
