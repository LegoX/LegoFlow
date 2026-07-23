---
name: setup
description: >
  Bootstrap the evaluator block: clone `repos/harbor/` at the commit declared
  in `meta_info.repositories.harbor` (via `scripts/update_repos.sh`),
  build the Harbor uv env at the path under `meta_info.environment.harbor_uv`,
  build the LiteLLM venv at the configured path, then fill in
  `runtime_info.input.llm_api` / `litellm_proxy` / `task_source` /
  `harbor_job` / `agent` — prompting only for values that aren't already
  set. Idempotent. Triggers on phrases like "set up evaluator", "bootstrap
  evaluator", "install harbor for evaluator", "prepare evaluator before running".
---

# /evaluator:setup

Brings the evaluator block from a fresh clone to "`/evaluator:check` passes." The
skill is idempotent: any step already satisfied is skipped. It never
launches Harbor — that's `/evaluator:run`.

Unlike tracer, evaluator has **no `setup_*_env.sh` helper scripts**; the two
environments are built with the explicit `uv` commands below (which
`scripts/dryrun.sh` also prints when an env is missing). Evaluator builds only
two envs (Harbor uv + LiteLLM venv) — there is no `swe_data_process` env,
because evaluator does not convert trajectories.

## Where to run

Read `meta_info.resources.ip` and `directory` from the user's private run
profile. For `local` / null, use the current host. For a remote hostname
or IP, connect through SSH keys or `~/.ssh/config` and run setup from the
configured block directory. Envs and the Harbor checkout are host-local
— never build them on a host the run won't use. Never add SSH passwords
to `config.yaml`.

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
  `pip install --user pyyaml` if missing (evaluator scripts emit
  `ERROR: PyYAML is required` otherwise).
- **Docker**: evaluator is CPU-only but every task runs in a container, and
  the agent runtime is extracted with `docker`. Confirm `docker ps`
  works on this host.

### 2. Repos

For `meta_info.repositories.harbor`:

- If `repos/harbor/` is missing, run `bash scripts/update_repos.sh`
  (clones, fetches, checks out the pinned `commit` detached, inits
  submodules, then sets the worktree read-only because `readonly: true`).
- `repos/harbor` is registered as a tracked submodule (`.gitmodules` at
  the repo root), so a fresh clone of SWE-Lego-Live needs
  `git submodule update --init subblock/evaluator/repos/harbor` first if the
  dir is empty.
- `update_repos.sh` refuses to update a worktree with local
  modifications. Stop and ask the user when that happens.
- Confirm `git -C repos/harbor rev-parse HEAD` equals the configured
  `commit`. Always read the live value from `config.yaml →
  meta_info.repositories.harbor.commit`; never hard-code the SHA in this
  skill because the pin moves as the block tracks newer registry
  contents.

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

Walk `runtime_info.input` and prompt only for unset fields (the literal `human` marker always counts as unset; `""` fields are env/auto-supplied — do not prompt for those):

- `llm_api.{api_key, api_base_url, model}` — the upstream served via the
  per-job LiteLLM proxy. `config.yaml` keeps two interchangeable recipes,
  only one uncommented at a time (read the active block, don't assume):
  - **MODE A — remote API** (commented example:
    `https://api.example.com/v1`, `openai/<served-model-name>`, and a key
    supplied in the user's private profile). Do not commit real API keys.
  - **MODE B — local vLLM checkpoint** (checked-in example:
    `http://127.0.0.1:8000/v1`, `openai/Qwen3.5-35B-A3B`,
    `api_key: dummy-key` matching vLLM's `--api-key`, costs `0.0`).
    Paired with the `local_model_serving` block (checkpoint path / served
    name).
  - **Custom local checkpoint (vLLM)**: to benchmark a local model
    (e.g. an SFT/RL output) instead of a remote API, serve it with vLLM
    on a **GPU node** via `scripts/serve_local_model.sh` (vLLM-only — the
    block's own LiteLLM still wraps it; do not start a second proxy), then
    set `llm_api` to the local recipe documented at the top of
    `runtime_info.input` in `config.yaml`: `api_key` matching vLLM's
    `--api-key`, `api_base_url: http://<GPU_NODE_IP>:<port>/v1`,
    `model: openai/<served-model-name>`, and costs `0.0`. The serving step
    is run by the user, not this skill. See the "Evaluating a custom local
    model" section in `CLAUDE.md`.
    The current serving profile is tuned for the active
    Qwen3.5-35B-A3B checkpoint: its defaults include
    `TOOL_CALL_PARSER=qwen3_coder`, `MAX_MODEL_LEN=262144`,
    `GPU_MEMORY_UTILIZATION=0.90`, `MAX_NUM_SEQS=32`,
    `LANGUAGE_MODEL_ONLY=1`, and `GDN_PREFILL_BACKEND=triton`. For
    another model architecture, override those parser/model-specific
    settings before launch. The
    script refuses to kill an existing listener; stop its owner
    explicitly or choose another `VLLM_PORT`.
  - **The vLLM env is out of this skill's scope.** `/evaluator:setup` builds
    only the CPU-side Harbor uv env + LiteLLM venv on the evaluator node; it
    does **not** install vLLM. The vLLM conda env lives on the GPU node and
    is a one-time manual step (`conda create … && pip install vllm==0.18.1`
    for bf16/fp16; the Harbor source-build script for FP8). If asked to
    "set up the local model", point the user at the GPU node + the CLAUDE.md
    recipe rather than installing anything on the evaluator node.
- `litellm_proxy.{config_template, port, master_key}` — defaults
  (`scripts/serve_llm/litellm_config.example.yaml`, port `4101`) are
  usually fine; the template path is resolved relative to `repos/harbor`.
- `task_source.{dataset_name, version}` — must resolve to an entry in
  `repos/harbor/registry.json`. Suggest a benchmark from the curated
  `CLAUDE.md` table (e.g. `swebench-verified@1.0`, or a `-100` subset for
  a smoke run). Keep `provider: harbor_registry` and
  `registry_path: repos/harbor/registry.json` — evaluator does **not** stage
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
- `job_analysis.{enabled, tag_llm}` — controls the post-eval analysis
  pipeline `start.sh` runs automatically (see `/evaluator:run`). Defaults are
  fine: `enabled: true`, LLM judge off (pure-CPU, zero token cost). It
  needs **no extra env** — the pipeline reuses `artifacts/env/harbor-uv`.
  `tag_llm.{base_url, model, api_key}` is used **only** when a missing gold
  dataset must be auto-generated (`scripts/prepare_dataset.sh`), to tag
  `task.toml` metadata; it must point at a **JSON-clean** endpoint
  (a reasoning model that emits `<think>` breaks tagging — Qwen3.5-35B-A3B served
  with thinking on does **not** work; GLM-5-FP8 does). Set `enabled: false`
  to skip analysis entirely.

### 5. Agent runtime extraction (evaluator-specific)

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

- **LLM endpoint**: the live
  `bash scripts/probe_llm_completion.sh` launch-gate belongs to
  `/evaluator:check`, not setup (evaluator's `dryrun.sh` does not probe). It sends a
  minimal real completion; do not replace it with `GET /models`, which
  can succeed while the upstream origin is down. If a remote gateway's
  edge can mask origin authentication, set `EVAL_GATEWAY_HOST_SUFFIX`
  in the private runtime environment; matching 401/403 responses become
  WARNs that must be re-probed on the configured evaluator host. A **local
  vLLM** `api_base_url` (MODE B) has no such caveat — there a connection
  failure is real.
- **No HuggingFace token needed**: evaluator is registry-driven; Harbor
  fetches task data via `registry.json`, so there is no gated-dataset
  prompt (unlike tracer).

### 7. Artifacts

`artifacts/index.yaml` is a runtime artifact written by
`scripts/archive_run.sh` after the first run. Its absence is **not** a
setup failure — `dryrun.sh` §1 treats a missing `index.yaml` as INFO
(auto-created later), so there is nothing to seed here. If you want a
placeholder anyway, `runs: []` is valid, but it is optional.

### 8. Final check

Hand off to `/evaluator:check` (which owns the dryrun + the live probe +
the structured preflight report). Do not re-run `dryrun.sh` here. If
`/evaluator:check` reports zero FAILs, point the user at `/evaluator:run`.

## Notes

- This skill never runs Harbor and never starts the LiteLLM proxy.
- Setup must run on the host declared in `meta_info.resources.ip`. If
  your shell is elsewhere, connect there first.

---

## Config reference (moved from config.yaml — do not re-add as comments)

### llm_api — MODE A (remote API) vs MODE B (local vLLM)

The block only talks to an OpenAI/Anthropic-compatible HTTP endpoint (the per-job LiteLLM proxy wraps `api_base_url`), so the two backends are interchangeable — point `llm_api` at one:

**MODE A — remote API, no local serving:**
```yaml
llm_api:
  api_key: "<your-api-key>"
  api_base_url: "https://api.example.com/v1"
  model: "openai/<served-model-name>"
  protocols: [openai_compatible, anthropic_compatible]
  served_via: per_job_litellm_proxy
  input_cost_per_token: 0.0
  output_cost_per_token: 0.0
```

**MODE B — local checkpoint served by vLLM** (the shipped default): `api_base_url: http://<GPU_NODE>:8000/v1`, `api_key` matching vLLM `--api-key`, `model: openai/<vLLM --served-model-name>`. `local_model_serving.{model_path,model_name}` is the source of truth read by `scripts/serve_local_model.sh`; ignored in MODE A. Full topology: CLAUDE.md "Evaluating a custom local model".

### Agent presets (switch all four fields together)

```yaml
# custom-claude-code (Anthropic protocol, validated end-to-end)
agent: {name: custom-claude-code, version: 2.1.118, runtime_image: docker.io/jierun/c-cc-2.1.118:v0.1, runtime_host_path: artifacts/runtime/claude-code}
# custom-openhands-sdk (OpenAI protocol; max_turns is reused as max_iterations)
agent: {name: custom-openhands-sdk, version: 1.14.0, runtime_image: docker.io/jierun/c-oh-sdk-1.14.0:v0.5, runtime_host_path: artifacts/runtime/openhands-sdk}
# custom-opencode (OpenAI-compatible via @ai-sdk/openai-compatible)
agent: {name: custom-opencode, version: 1.14.22, runtime_image: docker.io/jierun/c-oc-1.14.22:v0.1, runtime_host_path: artifacts/runtime/opencode}
```

### Runtime pre-extract recipe (once per runtime_image; idempotent)

`runtime_host_path` must already contain the extracted agent runtime tree from `runtime_image` (bind-mount source; preferred over image-mount, which trips an overlayfs filename-too-long bug on some task images). SUBPATH is `claude-code` or `oh-sdk` to match the agent:

```bash
RUNTIME_IMAGE=<runtime_image from config>
SUBPATH=claude-code   # or: oh-sdk
HOST_DIR=artifacts/runtime/<runtime_host_path basename>
docker pull "$RUNTIME_IMAGE" && \
CID=$(docker create "$RUNTIME_IMAGE") && \
rm -rf "$HOST_DIR" && mkdir -p "$(dirname "$HOST_DIR")" && \
docker cp "$CID:/opt/custom-agent-runtime/$SUBPATH" "$HOST_DIR" && \
docker rm "$CID"
```
