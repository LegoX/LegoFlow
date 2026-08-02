---
name: setup
description: >
  Bootstrap the trainer block from a fresh clone to "/trainer:check passes":
  preflight tooling (uv, python3 + PyYAML), check out repos/ (LLaMA-Factory,
  swe_data_process) at their pinned commits via git submodules, build the uv
  env at artifacts/env/lf through scripts/install_env.sh (torch 2.10 cu128 +
  editable repos + DeepSpeed/Liger + flash-attn + wandb), then fill in
  runtime_info.input (source fields, conversion.data_name, model path,
  training.output_dir, experiment.wandb_*), prompting only for unset or
  placeholder values and keeping the WandB key out of git. Idempotent; hands
  off to /trainer:check for the dryrun + preflight report. Triggers on phrases
  like "set up trainer", "bootstrap trainer", "install LLaMA-Factory",
  "prepare trainer before training", "wire up trainer config".
---

# /trainer:setup

Brings the trainer block from a fresh clone to "**`/trainer:check` passes**". The
skill is idempotent: any step already satisfied is skipped. It never runs
training or data conversion — that's `/trainer:run`.

## Step 0 — Orient

Run only from inside the trainer block. Validate `./config.yaml` exists and
`meta_info.name == 'trainer'`; otherwise abort ("run from `blocks/trainer/`").
Read `config.yaml` and `CLAUDE.md`.

Resolve the env path once: `SFT_UV = meta_info.environment.sft_uv`
(default `artifacts/env/lf`), `PY = $SFT_UV/bin/python`.

## Step 0b — GPU precondition

Read `meta_info.resources.ip` first. `null`/`local` means training runs on
this host — check GPU here. A real IP means the target is that remote host —
SSH there before checking, not on this one (per `BLOCK_DEFINITION.md`'s
execution-location rule).

On the target host, compare `nvidia-smi --list-gpus` against
`infrastructure.n_gpus_per_node` (same check `dryrun.sh` §11 runs later).
If `nvidia-smi` is missing, or reports fewer GPUs than configured, stop:
tell the user trainer cannot be set up successfully on this host — it needs
a GPU node — and do not proceed to Step 1's env build. This gate exists so a
missing GPU is caught before the multi-GB `install_env.sh` build, not after.

## Step 1 — Tooling preflight

The build scripts and the inline config readers depend on these:

- **`uv`** — required by `scripts/install_env.sh` and `dryrun.sh`. If
  missing on PATH: install to a writable location. On shared hosts where
  `~/.local/bin` is root-owned use
  `UV_INSTALL_DIR="$HOME/.uv/bin" UV_UNMANAGED_INSTALL=1 sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'`
  and persist `PATH="$HOME/.uv/bin:$PATH"`.
- **system `python3` + PyYAML** — every `scripts/*.sh` reads config through
  `scripts/config_value.py` (default `CONFIG_PYTHON=python3`), which
  `import yaml`. `python3 -c 'import yaml'` must succeed; `pip install
  --user pyyaml` if not.
- **network** — `install_env.sh` pulls from the Tsinghua PyPI mirror and the
  PyTorch cu128 index, and builds `flash-attn` from its source distribution. If the host is
  offline, stop here — don't half-build the env.

## Step 2 — Repos at pinned commits

The two repos under `repos/` are **git submodules** (declared in the repo
root `.gitmodules`); a fresh clone leaves them empty. For each entry under
`meta_info.repositories` (`llama_factory` → `repos/LLaMA-Factory`,
`swe_data_process` → `repos/swe_data_process`):

1. If `repos/<path>` is empty or missing, initialise the submodule from the
   repo root:
   ```bash
   git submodule update --init blocks/trainer/repos/LLaMA-Factory blocks/trainer/repos/swe_data_process
   ```
2. **`swe_data_process` may require repository access.** The checked-in
   submodule URL uses HTTPS. Authenticate with GitHub CLI or a credential
   helper before initializing it:
   ```bash
   gh auth login
   gh auth setup-git
   ```
   Never embed a token in the submodule URL or shell history.
   `LLaMA-Factory` uses the public patched SWE-Lego fork
   (`https://github.com/SWE-Lego/LLaMA-Factory.git`) and needs no token.
3. Verify each checkout matches the pin:
   `git -C repos/<path> rev-parse HEAD` must equal `meta_info.repositories.
   <name>.commit` (`3e32f8ca…` for LLaMA-Factory, `538d3838…` for
   swe_data_process). If a present worktree has drifted or has local
   edits, **report it and ask** — per `BLOCK_DEFINITION.md`, `repos/` is
   pinned, read-only code; `:setup` configures and pins, it does not patch.

After this, `dryrun.sh` section 3 should show `[OK] repos/LLaMA-Factory/`
and `[OK] repos/swe_data_process is an installable src-layout package`.

## Step 3 — Build / verify the uv env

The canonical builder is `scripts/install_env.sh`. It **removes and
recreates** `$SFT_UV` (it refuses any path outside `artifacts/env/`), then
installs the full stack: torch 2.10.0 cu128 → editable
`swe_data_process[llm]` and patched LLaMA-Factory → metrics/DeepSpeed/Liger
requirements → source-built `flash-attn` → `flash-linear-attention` +
`tilelang` → wandb. It ends by
importing `torch`, `swe_data_process`, `llamafactory` and printing
`cuda available: <bool>`. **Step 2 must be done first** — it installs
`-e repos/swe_data_process[llm]`, which fails on an empty submodule.

Decision:

- **Env missing** (`$PY` not executable) → run it:
  ```bash
  bash scripts/install_env.sh
  ```
  Relay the final import block. Step 0b already confirmed a GPU is present
  on this host, so `cuda available: False` here is a real problem, not
  expected — surface it rather than waving it off.
- **Env present** → verify before rebuilding (the rebuild is destructive and
  pulls several GB):
  ```bash
  "$PY" - <<'PY'
  import torch, swe_data_process, llamafactory
  print("torch", torch.__version__, "cuda", torch.cuda.is_available())
  PY
  ```
  If imports succeed, leave it. If they fail, tell the user the env is
  broken and **ask** before re-running `install_env.sh` (it wipes `$SFT_UV`).

The hardlink warning (`Failed to hardlink files; falling back to full
copy`) is benign — the uv cache and `artifacts/` are on different
filesystems. Suppress it with `export UV_LINK_MODE=copy` if it's noisy.

## Step 4 — Fill `runtime_info.input` (prompt only for gaps)

Walk these keys; for each that is the literal `human` (the must-fill marker —
always prompt for these), or an obvious placeholder (a path that doesn't exist
on this host), prompt with the current value as default. `""` fields are
env/auto-supplied and `null` is a semantic default — do not prompt for those
unless their inline comment says otherwise. Write accepted values back into `config.yaml`, preserving comments
and formatting (read–edit–write carefully; keep the inline `# choices: …`
comments).

| Key | What to ask / derive |
|---|---|
| `source.type` | `harbor_job`, `hf_lf`, or `local_lf` |
| Harbor source fields | `source.scaffold` plus an existing `source.job_dir` |
| Hugging Face source fields | `source.hf_hub_url`; optionally `source.hf_file_name` for exactly one file, otherwise subset/split; private datasets use `HF_TOKEN` from the runtime environment |
| Local source fields | existing LF/ShareGPT JSON at `source.lf_path` |
| `conversion.data_name` | unique name for this dataset (drives the IM/LF filenames and the registered dataset) |
| `conversion.max_instances` / `conversion.exclude_repos_file` | usually keep defaults; confirm the exclude file exists |
| `dataset.name` | leave empty to auto-derive from `data_name` (recommended) |
| `model.model_name_or_path` | Hub model ID or local base-model directory |
| `training.output_dir` | run name → `artifacts/model/<basename>`; encode key hparams in the name as the existing value does |
| `training.deepspeed` | ZeRO-3 config path (`scripts/deepspeed/ds_z3_config.json`); confirm it exists |
| `experiment.wandb_mode` | `offline` (default) \| `online` \| `disabled` |
| `credentials.wandb_api_key` | Leave empty in tracked config. For `wandb_mode: online`, export `WANDB_API_KEY` in the private runtime environment. |

Do **not** invent source fields or `model_name_or_path` — a missing input is
the user's signal to provide one, never a signal to fabricate a path.
Data acquisition/conversion + dataset registration happen inside `/trainer:run`'s
`train.sh` (STEP 0/1), so `:setup` does not prepare data here.

## Step 5 — Hand off to `/trainer:check`

Do **not** re-run `scripts/dryrun.sh` here — verification is `/trainer:check`'s
job, and it already wraps the dryrun (plus the live process/GPU/checkpoint
probes and the SAFE-TO-RUN report). Running dryrun in `:setup` too would
just print the same checks the user sees again the moment they run check.

Instead, tell the user setup is done and the next step is **`/trainer:check`**.
Since check is read-only, you may invoke it yourself and surface its verdict
— that confirms the bootstrap landed without duplicating the dryrun output.
A `❌ NO` here typically means a Step 4 input still points at a path that
doesn't exist on this host (model dir, `job_dir`) — fix those, not the env.

## Guardrails

- Idempotent: re-running must not duplicate work or destroy a working env
  without asking.
- Never modify files under `repos/` beyond checking out the pinned commit;
  report drift, don't paper over it.
- Online WandB requires `WANDB_API_KEY` in the private runtime environment.
  Never write a real key into tracked `config.yaml`.
- Never run training or data conversion here — that's `/trainer:run`.
- Local block: don't SSH anywhere (`meta_info.resources.ip: null`).

---

## Config reference (moved from config.yaml — do not re-add as comments)

### source.type mode matrix

| mode | uses | ignores | conversion |
|---|---|---|---|
| `harbor_job` | `scaffold`, `job_dir` (wired from tracer via `meta_info.dependencies.from`, mirrored by tracer's own `dependencies.to`) | `hf_*`, `lf_path` | full trajectory → LF conversion |
| `hf_lf` | `hf_hub_url`, `hf_file_name` (exact repo file; empty = full dataset config), `hf_subset` (ignored when hf_file_name set), `hf_split` | `scaffold`, `job_dir`, `lf_path` | skipped |
| `local_lf` | `lf_path` (absolute or block-relative LF json) | `scaffold`, `job_dir`, `hf_*` | skipped |

For `hf_lf` / `local_lf`, `conversion.data_name` is still used as the registered dataset key, and `conversion.max_instances` (>0) becomes the dataset's `num_samples` (random subsample). `dataset.name: ""` auto-derives from `conversion.data_name`.
