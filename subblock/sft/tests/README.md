# sft CI tests

Cheap (~30 s) deterministic drift detection for the sft block. Calibrated to
this repo's fixed CI runner. For portable user-environment diagnostics, use the
`/sft:check` skill instead.

---

## Quickstart

```bash
# runs every cases/NN_*.sh — safe to run anywhere:
bash subblock/sft/tests/run.sh
```

Per-test exit codes: `0` pass · `77` skip · anything else fail.
`run.sh` returns non-zero iff at least one test failed; skips never fail the suite.

---

## What gets checked

| # | Test | What it asserts | Time |
|---|---|---|---|
| 01 | config schema | every required key in `config.yaml` is set, `meta_info.name == "sft"`, scaffold + `wandb_mode` are in range | <1 s |
| 02 | repo pins | `repos/LLaMA-Factory` + `repos/swe_data_process` are checked out at the pinned commits; `swe_data_process` is a src-layout package | <1 s |
| 03 | uv env | `artifacts/env/lf` imports `torch` + `swe_data_process` + `llamafactory` (CUDA reported, not required) | ~10 s |
| 04 | converter module | the `source.scaffold` → converter module mapping (mirrors `train.sh`) resolves inside the uv env | ~3 s |
| 05 | source job_dir | `source.job_dir` holds ≥1 `*/agent/litellm-trajectory.jsonl` (SKIPs if the dir is absent) | <1 s |
| 06 | model path | `model.model_name_or_path` exists and has `config.json` (SKIPs if absent) | <1 s |
| 07 | deepspeed config | `training.deepspeed` exists, is valid JSON, declares a `zero_optimization.stage` | <1 s |
| 08 | gpu count | `nvidia-smi` reports ≥ `infrastructure.n_gpus_per_node` GPUs (SKIPs if `nvidia-smi` absent) | <1 s |

Cases 05/06/08 SKIP rather than FAIL when their large local asset (trajectory
source, base model, GPUs) isn't staged on the runner — so the suite stays green
on a CPU-only cases runner, while still catching real config drift.

There is no end-to-end training smoke for this block; the deterministic cases
are the whole suite. To actually exercise a (real) training run, use
`/sft:check` then `/sft:run`.

---

## When something fails

| You see | Most likely cause | What to do |
|---|---|---|
| 02: `.git missing` | `/sft:setup` never ran here | run `/sft:setup` (it does `git submodule update --init`) |
| 02: `HEAD does not match commit` | a submodule drifted off its pin | `git -C repos/<repo> checkout <pin>` |
| 03: `python missing` | the uv env was never built | `bash scripts/install_env.sh` (rebuilds `artifacts/env/lf`) |
| 03: `import failed — llamafactory` | env half-built or torch/cuda mismatch | re-run `bash scripts/install_env.sh` |
| 04: module not importable | scaffold/converter drift between `config.yaml` and `train.sh` | confirm `source.scaffold` is one of the four supported scaffolds |
| 05: SKIPped | the trajgen `job_dir` isn't on this host | not a failure; stage it, or repoint `source.job_dir` |
| 06: SKIPped | the base model isn't staged on the cases runner | not a failure; a real run needs it present |
| 07: `deepspeed config missing` | `artifacts/training_config/deepspeed/*.json` got deleted | `git restore subblock/sft/artifacts/training_config/deepspeed/` |
| 08: SKIPped | runner has no `nvidia-smi` | not a failure on a CPU cases runner |

---

## Layout

```
cases/                cheap deterministic checks
  01_config_schema.sh
  02_repo_pins.sh
  03_uv_env_editable.sh
  04_converter_module.sh
  05_source_job_dir.sh
  06_model_path.sh
  07_deepspeed_config.sh
  08_gpu_count.sh
run.sh                aggregator
```

---

## Per-test reference

Skip this section unless you're debugging a specific case or about to change one.

<details>
<summary><code>cases/01_config_schema.sh</code> — config.yaml shape</summary>

Parses `config.yaml` with PyYAML and asserts every key the runtime contract
depends on is present and non-empty: `meta_info.name == "sft"`,
`meta_info.environment.sft_uv`, both `meta_info.repositories.<repo>.{path,commit}`,
`runtime_info.input.source.{scaffold,job_dir}`,
`runtime_info.input.conversion.{data_name,exclude_repos_file}`,
`runtime_info.input.model.model_name_or_path`,
`runtime_info.input.training.{stage,finetuning_type,deepspeed,template,cutoff_len,output_dir}`,
`runtime_info.input.infrastructure.n_gpus_per_node`,
`runtime_info.input.experiment.wandb_mode`. Also range-checks `scaffold`,
`wandb_mode`, the online-WandB key requirement, and that `n_gpus_per_node` is a
positive integer. Pure-Python, no I/O.
</details>

<details>
<summary><code>cases/02_repo_pins.sh</code> — submodule pins</summary>

For `llama_factory` and `swe_data_process`: asserts `repos/<path>/.git` exists
and, when `commit` is non-null, `git rev-parse HEAD` matches the pin. Also
asserts `swe_data_process` is a src-layout package (`pyproject.toml` +
`src/swe_data_process/`).
</details>

<details>
<summary><code>cases/03_uv_env_editable.sh</code> — training stack imports</summary>

Resolves `meta_info.environment.sft_uv`, then imports `torch`,
`swe_data_process`, `llamafactory` inside that env's python (with
`PYTHONPATH=repos/swe_data_process/src`, as `train.sh` runs it). Reports
`torch.cuda.is_available()` but does not require it.
</details>

<details>
<summary><code>cases/04_converter_module.sh</code> — scaffold → converter</summary>

Mirrors `train.sh`'s `case "$SCAFFOLD"` mapping (claude-code →
`swe_data_process.claudecode_opencode.convert_cc_to_im`, etc.) and asserts the
module imports inside the uv env. Catches a scaffold/converter mismatch before
STEP 0 of a run.
</details>

<details>
<summary><code>cases/05_source_job_dir.sh</code> — trajectory source present</summary>

Resolves `source.job_dir` and asserts it contains ≥1
`*/agent/litellm-trajectory.jsonl`. SKIPs (77) when the dir is absent — the
trajgen output may live on another node.
</details>

<details>
<summary><code>cases/06_model_path.sh</code> — base model present</summary>

Asserts `model.model_name_or_path` exists and contains `config.json`. SKIPs
when absent (the base model is a large external asset).
</details>

<details>
<summary><code>cases/07_deepspeed_config.sh</code> — ZeRO config valid</summary>

Asserts the `training.deepspeed` file exists, parses as JSON, and declares a
`zero_optimization.stage`. Guards the regression where the
`artifacts/training_config/deepspeed/ds_z*.json` files get deleted while
`config.yaml` still points at one.
</details>

<details>
<summary><code>cases/08_gpu_count.sh</code> — enough GPUs visible</summary>

Asserts `nvidia-smi` reports ≥ `infrastructure.n_gpus_per_node` GPUs. SKIPs
when `nvidia-smi` is absent. Does not judge whether the GPUs are free — that
live check belongs to `/sft:check`.
</details>
