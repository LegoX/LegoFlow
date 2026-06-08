---
name: setup
description: >
  Bootstrap the sft block from a fresh clone to "/sft:check passes":
  preflight tooling (uv, wget, system python3 + PyYAML), check out the
  repos under repos/ (LLaMA-Factory, swe_data_process) at their pinned
  commits — initialising the git submodules, rewriting the swe_data_process
  SSH remote to https+token when needed — build the uv env at
  meta_info.environment.sft_uv (artifacts/env/lf) via scripts/install_env.sh
  (swe_data_process[llm] + torch cu128 + LLaMA-Factory[torch,metrics,
  deepspeed,liger-kernel] + flash-attn 2.8.3 wheel + wandb), then fill in
  runtime_info.input (source.job_dir, conversion.data_name,
  model.model_name_or_path, training.output_dir, experiment.wandb_*),
  prompting only for unset or placeholder values and keeping the WandB key
  out of git. Idempotent; ends by handing off to /sft:check (which owns the
  dryrun + preflight report) rather than re-running dryrun itself. Triggers on
  phrases like "set up sft", "bootstrap sft", "install LLaMA-Factory",
  "prepare sft before training", "wire up sft config".
---

# /sft:setup

Brings the sft block from a fresh clone to "**`/sft:check` passes**". The
skill is idempotent: any step already satisfied is skipped. It never runs
training or data conversion — that's `/sft:run`.

## Step 0 — Orient

Run only from inside the sft block. Validate `./config.yaml` exists and
`meta_info.name == 'sft'`; otherwise abort ("run from `subblock/sft/`").
Read `config.yaml` and `CLAUDE.md`.

Resolve the env path once: `SFT_UV = meta_info.environment.sft_uv`
(default `artifacts/env/lf`), `PY = $SFT_UV/bin/python`.

## Step 1 — Tooling preflight

The build scripts and the inline config readers depend on these:

- **`uv`** — required by `scripts/install_env.sh` and `dryrun.sh`. If
  missing on PATH: install to a writable location. On shared hosts where
  `~/.local/bin` is root-owned use
  `UV_INSTALL_DIR="$HOME/.uv/bin" UV_UNMANAGED_INSTALL=1 sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'`
  and persist `PATH="$HOME/.uv/bin:$PATH"`.
- **`wget`** — `install_env.sh` downloads the flash-attn wheel with it.
- **system `python3` + PyYAML** — every `scripts/*.sh` reads config through
  `scripts/config_value.py` (default `CONFIG_PYTHON=python3`), which
  `import yaml`. `python3 -c 'import yaml'` must succeed; `pip install
  --user pyyaml` if not.
- **network** — `install_env.sh` pulls from the Tsinghua PyPI mirror, the
  PyTorch cu128 index, and the flash-attn GitHub release. If the host is
  offline, stop here — don't half-build the env.

## Step 2 — Repos at pinned commits

The two repos under `repos/` are **git submodules** (declared in the repo
root `.gitmodules`); a fresh clone leaves them empty. For each entry under
`meta_info.repositories` (`llama_factory` → `repos/LLaMA-Factory`,
`swe_data_process` → `repos/swe_data_process`):

1. If `repos/<path>` is empty or missing, initialise the submodule from the
   repo root:
   ```bash
   git submodule update --init subblock/sft/repos/LLaMA-Factory subblock/sft/repos/swe_data_process
   ```
2. **`swe_data_process` uses an SSH remote** (`git@github.com:SWE-Lego/
   swe_data_process.git`) and is a private repo. On a host with only an
   https token (no SSH key), the clone fails — override the submodule URL
   first:
   ```bash
   git config submodule."subblock/sft/repos/swe_data_process".url \
     "https://<TOKEN>@github.com/SWE-Lego/swe_data_process.git"
   ```
   `LLaMA-Factory` is the public upstream (`https://github.com/hiyouga/
   LLaMA-Factory.git`) and needs no token.
3. Verify each checkout matches the pin:
   `git -C repos/<path> rev-parse HEAD` must equal `meta_info.repositories.
   <name>.commit` (`e695fdfa…` for LLaMA-Factory, `8f60ee31…` for
   swe_data_process). If a present worktree has drifted or has local
   edits, **report it and ask** — per `BLOCK_DEFINITION.md`, `repos/` is
   pinned, read-only code; `:setup` configures and pins, it does not patch.

After this, `dryrun.sh` section 3 should show `[OK] repos/LLaMA-Factory/`
and `[OK] repos/swe_data_process is an installable src-layout package`.

## Step 3 — Build / verify the uv env

The canonical builder is `scripts/install_env.sh`. It **removes and
recreates** `$SFT_UV` (it refuses any path outside `artifacts/env/`), then
installs the full stack: `swe_data_process[llm]` → torch 2.8.0 cu128 →
`LLaMA-Factory[torch,metrics,deepspeed,liger-kernel]` (`--no-build-
isolation`) → the pinned flash-attn 2.8.3 wheel → wandb. It ends by
importing `torch`, `swe_data_process`, `llamafactory` and printing
`cuda available: <bool>`. **Step 2 must be done first** — it installs
`-e repos/swe_data_process[llm]`, which fails on an empty submodule.

Decision:

- **Env missing** (`$PY` not executable) → run it:
  ```bash
  bash scripts/install_env.sh
  ```
  Relay the final import block. On a GPU-less host `cuda available` is
  `False` — that's expected and does not mean setup failed; it only means
  training itself must run on the 8-GPU node.
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

Walk these keys; for each that is empty, `null`, or an obvious placeholder
(a path that doesn't exist on this host), prompt with the current value as
default. Write accepted values back into `config.yaml`, preserving comments
and formatting (read–edit–write carefully; keep the inline `# choices: …`
comments).

| Key | What to ask / derive |
|---|---|
| `source.scaffold` | one of `openhands-sdk \| claude-code \| open-code \| terminus2` (matches the agent that produced the trajectories) |
| `source.job_dir` | trajgen job dir with raw trajectories — `trajgen.output.raw_trajectories_dir` (`subblock/trajgen/artifacts/jobs/<job>`). Offer to list candidates; confirm it exists on this host. |
| `conversion.data_name` | unique name for this dataset (drives the IM/LF filenames and the registered dataset) |
| `conversion.max_instances` / `exclude_repos_file` | usually keep defaults; confirm the exclude file exists |
| `dataset.name` | leave empty to auto-derive from `data_name` (recommended) |
| `model.model_name_or_path` | local base-model dir (must exist; dryrun checks it) |
| `training.output_dir` | run name → `artifacts/model/<basename>`; encode key hparams in the name as the existing value does |
| `training.deepspeed` | ZeRO-3 config path (`artifacts/training_config/deepspeed/ds_z3_config.json`); confirm it exists |
| `experiment.wandb_mode` | `offline` (default) \| `online` \| `disabled` |
| `credentials.wandb_api_key` | **only when `wandb_mode: online`.** Prefer `export WANDB_API_KEY=…` over writing it into `config.yaml`. Never commit a key. |

Do **not** invent a `job_dir` or `model_name_or_path` — a missing input is
the user's signal to provide one, never a signal to fabricate a path.
Conversion + dataset registration happen inside `/sft:run`'s `train.sh`
(STEP 0/1), so `:setup` does **not** convert data here.

## Step 5 — Hand off to `/sft:check`

Do **not** re-run `scripts/dryrun.sh` here — verification is `/sft:check`'s
job, and it already wraps the dryrun (plus the live process/GPU/checkpoint
probes and the SAFE-TO-RUN report). Running dryrun in `:setup` too would
just print the same checks the user sees again the moment they run check.

Instead, tell the user setup is done and the next step is **`/sft:check`**.
Since check is read-only, you may invoke it yourself and surface its verdict
— that confirms the bootstrap landed without duplicating the dryrun output.
A `❌ NO` here typically means a Step 4 input still points at a path that
doesn't exist on this host (model dir, `job_dir`) — fix those, not the env.

## Guardrails

- Idempotent: re-running must not duplicate work or destroy a working env
  without asking.
- Never modify files under `repos/` beyond checking out the pinned commit;
  report drift, don't paper over it.
- Never write secrets into `config.yaml`; keep `credentials.wandb_api_key`
  empty and use the env var.
- Never run training or data conversion here — that's `/sft:run`.
- Local block: don't SSH anywhere (`meta_info.resources.ip: null`).
