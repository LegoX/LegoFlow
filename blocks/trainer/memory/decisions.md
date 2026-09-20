# Training Decisions

## 2026-07: Exact Hugging Face file selection
**Decision:** `hf_lf` supports an optional `source.hf_file_name`. When set,
`scripts/train.sh` downloads only that file with `hf_hub_download`, caches it under
`artifacts/data/hf_data/`, and registers it with LLaMA-Factory as a local `file_name`.
When unset, the original `hf_hub_url` + subset/split train-time loading path remains.
**Why:** A Hub repository can contain several JSON files that Hugging Face combines
into one default split. Training must be able to select one curated file without
implicitly mixing the repository's other files. This extends the June input-source
decision below.

## 2026-07: Qwen3.5-capable training environment
**Decision:** The environment installer now uses PyTorch 2.10.0 CUDA 12.8,
builds `flash-attn` against the installed PyTorch, and installs
`flash-linear-attention` plus `tilelang` for Qwen3.5. The patched LegoX
LLaMA-Factory is installed editable with its metrics, DeepSpeed, and Liger
requirements.
**Why:** Qwen3.5-35B-A3B uses linear-attention code paths and dependencies absent
from the earlier Qwen3-8B environment. This supersedes the May environment stack
description retained below as history.

## 2026-06: Pluggable input sources (harbor_job | hf_lf | local_lf)
**Decision:** `config.yaml.runtime_info.input.source.type` selects the dataset source. `harbor_job`
(default) converts raw Harbor trajectories as before. `hf_lf` registers the dataset with
LLaMA-Factory's native `hf_hub_url` and loads it from the HuggingFace Hub at train time;
`local_lf` registers an existing LF/ShareGPT json (absolute `file_name`) as-is. Both ready-made
sources skip STEP 0 conversion in `scripts/train.sh`/`dataprep.sh`; `conversion.data_name` stays the
dataset key and `conversion.max_instances` (>0) becomes the entry's `num_samples`. Private HF
datasets use `HF_TOKEN` from the private runtime environment. Empty/absent `source.type` defaults to `harbor_job` (back-compat).
**Why:** Not every training set comes from a Harbor job — sometimes we already have a curated LF
dataset (shared on HF or produced elsewhere). Leaning on LLaMA-Factory's built-in `hf_hub_url`
support avoids writing any download/caching logic. Scoring and eval-repo exclusion only apply during
conversion, so ready-made sources must be pre-cleaned upstream.

## 2026-06: Web dashboard replaces the status.mdx text view
**Decision:** Live monitoring is the React + `server.py` webui under `dashboard/`
(from `LegoX/LLaMA-Factory:webui`). It reads `trainer_log.jsonl`,
`trainer_state.json`, and `*_results.json` from `artifacts/model/<run>/` and console
logs from `artifacts/logs/`. The previous `scripts/update_status.py` + `dashboard/status.mdx`
text mechanism and its `train.sh` background loop were removed.
**Why:** A real web UI gives live loss curves, multi-run comparison, eval/perf panels,
and optional wandb + Cloudflare-tunnel sharing — superseding the hand-rolled mdx status doc.

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

## 2026-05: Outputs live in config.yaml
**Decision:** `scripts/train.sh` updates `config.yaml.runtime_info.output` after each run
with checkpoint path, artifacts, and training metrics. Only the
`runtime_info.output` block is replaced, preserving comments and formatting elsewhere.
**Why:** Keeping inputs and outputs in one block config avoids drift from a separate
`outputs.yaml` file that is not checked in for this block.

## 2026-04: scripts/train.sh launches torchrun directly on the current node
**Decision:** `scripts/train.sh` launches LLaMA-Factory through its torchrun path on the
current node, with `NPROC_PER_NODE` wired from `infrastructure.n_gpus_per_node`.
**Why:** The block currently targets single-node launches. Keeping execution local avoids a
second layer of Slurm wrapper logic while still making the GPU count explicit in config.

## 2026-04: Repo exclusion filter enabled by default
**Decision:** All data converters default to filtering out repos in `scripts/excluded_repos.txt`.
**Why:** Prevents eval benchmark repos (SWE-bench_Verified, SWE-bench_Pro,
SWE-bench_Multilingual) from leaking into training data. Pass `--exclude-repos-file ""`
to disable for specific experiments.

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
