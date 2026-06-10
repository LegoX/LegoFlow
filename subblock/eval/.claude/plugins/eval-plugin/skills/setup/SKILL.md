---
name: setup
description: >
  Bootstrap the eval block: clone `repos/harbor/` at the commit declared
  in `meta_info.repositories.harbor` (via `scripts/update_repos.sh`),
  build the Harbor uv env at the path under `meta_info.environment.harbor_uv`,
  build the LiteLLM venv at the configured path, then fill in
  `runtime_info.input.llm_api` / `litellm_proxy` / `task_source` /
  `harbor_job` / `agent` — prompting only for values that aren't already
  set. Idempotent. Triggers on phrases like "set up eval", "bootstrap
  eval", "install harbor for eval", "prepare eval before running".
---

# /eval:setup

Brings the eval block from a fresh clone to "`/eval:check` passes." The
skill is idempotent: any step already satisfied is skipped. It never
launches Harbor — that's `/eval:run`.

Unlike trajgen, eval has **no `setup_*_env.sh` helper scripts**; the two
environments are built with the explicit `uv` commands below (which
`scripts/dryrun.sh` also prints when an env is missing). Eval builds only
two envs (Harbor uv + LiteLLM venv) — there is no `swe_data_process` env,
because eval does not convert trajectories.

## Where to run

`meta_info.resources.ip` is currently `192.168.35.240` (a real remote
IP), `user: root`, working dir `/gpufs/haoli/code/`. If the current shell
is on a different host, SSH there first and run setup from the eval block
dir under that path. Envs and the Harbor checkout are host-local — never
build them on a host the run won't use.

## Procedure

### 1. Tooling preflight

- **`uv`**: required for both env builds and by `start.sh` (`uv run
  harbor …`). If missing on PATH:
  - If `~/.local/bin` is writable, install via
    `curl -LsSf https://astral.sh/uv/install.sh | sh`.
  - Otherwise (shared host, root-owned `~/.local`): install with
    `UV_INSTALL_DIR="$HOME/.uv/bin" UV_UNMANAGED_INSTALL=1 sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'`
    and persist `PATH="$HOME/.uv/bin:$PATH"`, `UV_PYTHON_INSTALL_DIR=$HOME/.uv/python`,
    `UV_CACHE_DIR=$HOME/.uv/cache` in `~/.bashrc`.
- **Python 3.13**: required by the LiteLLM venv. If absent, run
  `uv python install 3.13`.
- **PyYAML in system `python3`**: every `scripts/*.sh` uses inline
  `python3 -` config readers. `python3 -c 'import yaml'` must succeed;
  `pip install --user pyyaml` if missing (eval scripts emit
  `ERROR: PyYAML is required` otherwise).
- **Docker**: eval is CPU-only but every task runs in a container, and
  the agent runtime is extracted with `docker`. Confirm `docker ps`
  works on this host.

### 2. Repos

For `meta_info.repositories.harbor`:

- If `repos/harbor/` is missing, run `bash scripts/update_repos.sh`
  (clones, fetches, checks out the pinned `commit` detached, inits
  submodules, then sets the worktree read-only because `readonly: true`).
- `repos/harbor` is registered as a tracked submodule (`.gitmodules` at
  the repo root), so a fresh clone of SWE-Lego-Live needs
  `git submodule update --init subblock/eval/repos/harbor` first if the
  dir is empty.
- `update_repos.sh` refuses to update a worktree with local
  modifications. Stop and ask the user when that happens.
- Confirm `git -C repos/harbor rev-parse HEAD` equals the configured
  `commit` (`149e75770f8c369d6fae31c158ea761d676b4d3a`).

### 3. Environments

Build only the envs that don't already pass the import check
(`<env>/bin/python -c "import <pkg>"` to gate). The env paths come from
`meta_info.environment`.

| Env path | Build command | Verifies |
|---|---|---|
| `artifacts/env/harbor-uv/` | from inside `repos/harbor`: `UV_PROJECT_ENVIRONMENT=<abs path> uv sync --all-extras` | `python -c "import harbor, litellm, datasets"`, `harbor --help`, and that `harbor` resolves to `repos/harbor` (editable). |
| `artifacts/env/litellm-venv/` | `uv venv <abs path> --python 3.13 && uv pip install --python <abs path>/bin/python 'litellm[proxy]==1.83.14'` | `litellm --version` and installed version == `1.83.14`. Do **not** use `import litellm; litellm.__version__` — litellm raises `AttributeError` on `__version__` by design. |

The Harbor env path **must be outside `repos/harbor`** while
`repositories.harbor.readonly: true` — `artifacts/env/harbor-uv` already
satisfies this. `UV_PROJECT_ENVIRONMENT` must be an absolute path.

### 4. Config

Walk `runtime_info.input` and prompt only for unset fields:

- `llm_api.{api_key, api_base_url, model}` — the upstream served via the
  per-job LiteLLM proxy (currently `https://qwen.jierungogogo.com/v1`
  with `openai/Qwen3.5-35B-A3B`). `api_key: dummy-key` is intentional
  for CF-gated production endpoints (see step 6).
- `litellm_proxy.{config_template, port, master_key}` — defaults
  (`scripts/serve_llm/litellm_config.example.yaml`, port `4101`) are
  usually fine; the template path is resolved relative to `repos/harbor`.
- `task_source.{dataset_name, version}` — must resolve to an entry in
  `repos/harbor/registry.json`. Suggest a benchmark from the curated
  `CLAUDE.md` table (e.g. `swebench-verified@1.0`, or a `-100` subset for
  a smoke run). Keep `provider: harbor_registry` and
  `registry_path: repos/harbor/registry.json` — eval does **not** stage
  tasks locally. If the user picks a benchmark **outside** the curated
  table, surface the agent-compatibility caveat from `CLAUDE.md`
  ("Other registry entries").
- `harbor_job.{jobs_dir, n_concurrent, n_tasks, max_retries,
  timeout_multiplier}` and `agent.{name, version, runtime_image,
  runtime_host_path, max_turns, temperature}` — config defaults are
  sensible. The three validated agents (`custom-claude-code`,
  `custom-openhands-sdk`, `custom-opencode`) are switched by editing
  `name`/`version`/`runtime_image`/`runtime_host_path` **together** — see
  the commented block in `config.yaml`.

### 5. Agent runtime extraction (eval-specific)

`agent.runtime_host_path` must be a directory pre-populated with the
agent runtime extracted from `agent.runtime_image`; `start.sh`
bind-mounts it read-only into every task container. An empty dir makes
the agent fall back to an in-container install that 403s on isolated
networks. Pre-extract once per image (idempotent; re-run when you bump
the image), matching `SUBPATH` to the agent
(`claude-code` / `oh-sdk` / `opencode`):

```bash
RUNTIME_IMAGE=<agent.runtime_image from config>
SUBPATH=oh-sdk        # claude-code | oh-sdk | opencode
HOST_DIR=<agent.runtime_host_path from config>
docker pull "$RUNTIME_IMAGE" && \
CID=$(docker create "$RUNTIME_IMAGE") && \
rm -rf "$HOST_DIR" && mkdir -p "$(dirname "$HOST_DIR")" && \
docker cp "$CID:/opt/custom-agent-runtime/$SUBPATH" "$HOST_DIR" && \
docker rm "$CID"
```

`dryrun.sh` §8 checks the per-agent marker file (`bin/claude`,
`runtime-env.sh`, or `bin/opencode`) so a bad extraction surfaces in
preflight.

### 6. Credentials

- **LLM endpoint**: a live `GET <api_base_url>/models` probe belongs to
  `/eval:check`, not setup (eval's `dryrun.sh` does not probe). Note: from
  Claude Code's sandboxed shell some endpoints (e.g.
  `qwen.jierungogogo.com`) return 401 due to CF gating with `dummy-key`
  as the real production key — not a credential failure. See memory
  `project-swegen-llm-endpoint`.
- **No HuggingFace token needed**: eval is registry-driven; Harbor
  fetches task data via `registry.json`, so there is no gated-dataset
  prompt (unlike trajgen).

### 7. Artifacts

If `artifacts/index.yaml` is missing (it was removed in a recent merge),
create it so `dryrun.sh` §1 passes:

```yaml
runs: []
```

`scripts/archive_run.sh` appends real entries here after each run.

### 8. Final check

Hand off to `/eval:check` (which owns the dryrun + the live probe +
the structured preflight report). Do not re-run `dryrun.sh` here. If
`/eval:check` reports zero FAILs, point the user at `/eval:run`.

## Notes

- This skill never runs Harbor and never starts the LiteLLM proxy.
- Setup must run on the host declared in `meta_info.resources.ip`
  (`192.168.35.240`). If your shell is elsewhere, SSH there first.
