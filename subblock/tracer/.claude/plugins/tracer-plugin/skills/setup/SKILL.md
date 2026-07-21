---
name: setup
description: >
  Bootstrap the tracer block: install `uv` (in a writable location when
  `~/.local/bin` is root-owned), clone+pin Harbor and swe_data_process,
  build the three uv/venv environments (harbor uv, LiteLLM venv on Python
  3.13, swe_data_process uv), fill in `runtime_info.input` only for unset
  fields, initialise `artifacts/consumption_ledger.yaml`, and (when
  `task_source.provider: huggingface`) prompt for a HF token if the
  dataset is gated. Idempotent. Ends by running `scripts/dryrun.sh` so the
  user sees whether the block is now check-passing. Triggers on phrases
  like "set up tracer", "bootstrap tracer", "install harbor for
  tracer", "prepare tracer before running", "/tracer:setup".
---

# /tracer:setup

Brings the tracer block from a fresh clone to "`/tracer:check` passes."
The skill is idempotent: any step that is already satisfied is skipped.
The skill never launches Harbor — that's `/tracer:run`.
All commands run from the block root `subblock/tracer/`.

## Procedure

### 1. Tooling preflight

- **`uv`**: required by every env step and by `scripts/start.sh` (`uv run
  harbor …`). If missing on PATH:
  - Check whether `~/.local/bin` is writable by the current user. On
    shared hosts this directory is often owned by `root`.
  - If `~/.local/bin` is writable: install via
    `curl -LsSf https://astral.sh/uv/install.sh | sh`.
  - Otherwise: install with
    `UV_INSTALL_DIR="$HOME/.uv/bin" UV_UNMANAGED_INSTALL=1 sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'`
    and persist `PATH="$HOME/.uv/bin:$PATH"`, `UV_PYTHON_INSTALL_DIR=$HOME/.uv/python`,
    `UV_CACHE_DIR=$HOME/.uv/cache` in `~/.bashrc`. The cache/python redirects
    matter on the same shared hosts where `~/.local/share/uv/` is root-owned.

- **Python 3.13**: required by the LiteLLM venv. If absent from the system,
  run `uv python install 3.13` (uses `UV_PYTHON_INSTALL_DIR`).

- **PyYAML in system `python3`**: `scripts/*.sh` use inline `python3 -`
  config readers. `python3 -c 'import yaml'` must succeed; install with
  `pip install --user pyyaml` if missing.

### 2. Repos

For each entry under `meta_info.repositories`:

- If `repos/<name>/` is missing OR present but empty, run
  `bash scripts/update_repos.sh --repo <name>` (clones, checks out the
  pinned commit, chmods read-only when `readonly: true`).
- `repos/harbor` is registered as a tracked git submodule (`.gitmodules`
  in the repo root); a fresh clone of SWE-Lego-Live therefore needs
  `git submodule update --init subblock/tracer/repos/harbor` before
  `update_repos.sh` is useful.
- The script refuses to update a worktree with local modifications. Stop
  and ask the user when that happens.

Both repos are **managed local-only dependencies** and are gitignored. Never edit
their sources here; they are set read-only after checkout when `readonly: true`,
so any uv environment for them must live outside the checkout (under `artifacts/env/`).

### 3. Environments

Build only the envs that don't already pass the editable-install check
(use `<env>/bin/python -c "import <pkg>"` to gate):

| Env path | Builder | Verifies |
|---|---|---|
| `artifacts/env/harbor-uv/` | `bash scripts/setup_harbor_env.sh` | `python -c "import harbor"` |
| `artifacts/env/litellm-venv/` | `uv venv ... --python 3.13 && uv pip install 'litellm[proxy]==1.83.14'` then `litellm --version` | DO NOT use `import litellm; litellm.__version__` — litellm raises `AttributeError` on `__version__` by design. |
| `artifacts/env/swe-data-process-uv/` | `bash scripts/setup_swe_data_process_env.sh` | `python -c "import swe_data_process, jinja2"` |

Both `setup_harbor_env.sh` and `setup_swe_data_process_env.sh` handle
the non-root chmod dance: they temporarily restore write perms on the
read-only worktree, run `uv sync`, and re-lock on EXIT.

### 4. Config

Walk `runtime_info.input` and prompt only for unset fields (the literal `human` marker always counts as unset; `""` fields are env/auto-supplied — do not prompt for those):

- `llm_api.{api_key, api_base_url, model}` — pick the configured upstream
  (e.g. `https://az.gptplus5.com/v1` with `openai/deepseek-v4-flash`).
- `litellm_proxy.{port, master_key}` — defaults are usually fine.
- `task_source` — either:
  - `{provider: huggingface, dataset_name, split}` — production default
    for swerebench-style runs.
  - `{provider: local, dataset_name: ../curator/artifacts/swe_tasks/<lang>-cc}`
    — only valid if curator has actually exposed verified tasks at that
    contract path. curator historically keeps outputs inside
    `repos/swegen/artifacts/...`; before picking `local`, verify the
    path exists or have curator symlink it.
- `harbor_job.{n_concurrent, n_tasks, max_retries, timeout_multiplier}`,
  `agent.{name, version, runtime_image, max_turns, temperature}`,
  `sft_conversion.*` — config defaults are sensible for first-time runs.

### 5. Credentials

- **HuggingFace** (when `task_source.provider: huggingface`): probe
  `https://huggingface.co/api/datasets/<dataset_name>` with `Authorization:
  Bearer <token>` from `~/.cache/huggingface/token` (or `$HF_TOKEN`). If
  the response is 401/403, prompt the user for a token and write it to
  `~/.cache/huggingface/token` (mode 600). `SWE-Lego/*` datasets are
  gated, so this almost always applies.
- **LLM endpoint**: a live probe (`GET <api_base_url>/models`) is now part
  of `scripts/dryrun.sh`, so setup does not need to repeat it. Note: when
  running inside Claude Code's sandboxed shell, some endpoints (e.g.
  `llm10.jierungogogo.com`) return 401 due to CF gating — see memory
  `project-swegen-llm-endpoint`. That is not a credential failure.

### 6. Ledger

If `artifacts/consumption_ledger.yaml` is missing, create it:

```yaml
description: "Tracer task consumption ledger — statuses: pending | running | done | failed | skipped."
runs: []
```

### 7. Final check

Run `bash scripts/dryrun.sh` and report PASS/WARN/FAIL counts. If FAIL is
zero, point the user at `/tracer:run`. Setup is done.

## Notes

- This skill does not run `prepare_tasks.sh` by default. Task staging is part of
  `start.sh`'s preflight (and `dryrun.sh` now probes HF auth so failures
  surface early). If the user explicitly asks to stage now, run
  `bash scripts/prepare_tasks.sh` and continue.
- Setup must run on the host declared in `meta_info.resources.ip`. If the
  current shell is on a different host, SSH there first (the tracer
  block currently uses `local` / the named cpu node).
- Run inside a named tmux session on the host named by `meta_info.resources.ip`
  so long clones/syncs survive disconnects.

---

## Config reference (moved from config.yaml — do not re-add as comments)

### task_source

Production runs use `provider: local` pointing at curator's verified tasks — the dependency is declared in `meta_info.dependencies` (`task_source.dataset_name: {from: curator.output.swe_tasks_dir, when: {task_source.provider: local}}`). `prepare_tasks.sh` filters the source dir through its `verifiable_tasks.txt` manifest before copying into `artifacts/tasks/`.

Alternative — verified TERMINAL tasks from the terminalgen block (terminal-lego v1.0 schema, parallel to curator):
```yaml
task_source:
  provider: terminalgen
  dataset_name: ../terminalgen/artifacts/terminal_tasks
  split: train
```
Filter by per-domain `../terminalgen/artifacts/terminal_tasks/{domain}-tl/verifiable_tasks.txt` (or the optional flat `../terminalgen/artifacts/merged_terminal_tasks/verifiable_tasks.txt`). Switching provider = edit the dep's `when:` mode and the `task_source` input together.

### sft_conversion

`scaffold: auto` derives the converter scaffold from `agent.name` (choices: claude_code | open_code | openhands_sdk | terminus2). `exclude_repos_file: ""` uses the repo default `artifacts/excluded_repos.txt`.
