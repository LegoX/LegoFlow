---
name: setup
description: >
  Bootstrap the curator block from a fresh clone: initialise the legoflow-curator
  submodule under `repos/legoflow-curator/` (`git submodule update --init`),
  create + activate the Python venv, `pip install -e repos/legoflow-curator/`, and
  ensure the cross-provider LLM env (`OPENAI_API_KEY`,
  `OPENAI_API_BASE_URL`, `OPENAI_MODEL`, mirrored to `ANTHROPIC_API_KEY` /
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`), `GITHUB_TOKENS` / `GITHUB_TOKEN`,
  `DOCKER_HOST`, and `CLAUDE_CONFIG_DIR` are set in the user's shell —
  prompting for any that are missing. Stage `gh_token.txt` (one token per
  line) if the user prefers the file channel. Idempotent. Triggers on
  phrases like "set up curator", "bootstrap curator", "install curator",
  "prepare curator before running".
---

# /curator:setup

Prepare `blocks/curator/` so `/curator:check` can pass. The operation is
idempotent: repeat it freely, but never overwrite secrets or runtime outputs
without explicit user consent.

## Step 0 - Orient

Run from inside the curator block. Verify:

1. `./config.yaml` exists and `meta_info.name == "curator"`.
2. `./CLAUDE.md` exists.
3. `./repos/legoflow-curator/pyproject.toml` exists after submodule init.

If the current directory is not `blocks/curator/`, stop and tell the user
to rerun the command from the block directory.

## Step 1 - Initialize the LegoFlow Curator source checkout

From the repository root, ensure the submodule exists:

```bash
git submodule update --init blocks/curator/repos/legoflow-curator
```

Then return to `blocks/curator/`. If `config.yaml` declares a non-null
`meta_info.repos.legoflow-curator.commit_id`, compare it with
`git -C repos/legoflow-curator rev-parse HEAD` and report any drift. Do not change the
submodule pin unless the user explicitly asks.

## Step 2 - Build or refresh the Python environment

Read `meta_info.environment.venv_path` from `config.yaml` (currently
`artifacts/envs/legoflow-curator-env`) and create it if missing:

```bash
python3 -m venv artifacts/envs/legoflow-curator-env
source artifacts/envs/legoflow-curator-env/bin/activate
pip install -e repos/legoflow-curator/
```

The editable install exposes `legoflow-curator` and `harbor` console scripts from
`repos/legoflow-curator/pyproject.toml`.

## Step 3 - Prepare runtime environment variables

LegoFlow Curator uses both direct OpenAI-compatible calls and Claude Code SDK based
task generation. Keep these in the shell or in `.env`; never write real
secrets into `config.yaml`.

| Variable | Purpose |
| --- | --- |
| `GITHUB_TOKENS` | Comma-separated GitHub API tokens for `legoflow-curator create` and general API use. |
| `GITHUB_TOKEN` | Optional first-token alias; `scripts/load_runtime_env.sh` derives it from `GITHUB_TOKENS` when absent. |
| `OPENAI_API_KEY` | LLM key used by `legoflow_curator.llm_env.get_openai_compatible_config()`. |
| `OPENAI_API_BASE_URL` | OpenAI-compatible base URL. |
| `OPENAI_MODEL` | Model for PR evaluation, instruction generation, and analysis calls. |
| `ANTHROPIC_API_KEY` | Usually the same value as `OPENAI_API_KEY` for the cross-provider shim. |
| `ANTHROPIC_BASE_URL` | Endpoint for the Claude Code path. For OpenAI-only providers (`cc_provider_mode: openai_proxy`) this must be the local LiteLLM proxy, not the raw provider URL — see `CLAUDE.md` "LLM provider modes". |
| `ANTHROPIC_MODEL` | Model used by Claude Code SDK task completion. |
| `DOCKER_HOST` | Prefer `unix:///var/run/docker.sock` so Harbor does not probe stale Podman sockets. |
| `LEGOFLOW_CURATOR_CLAUDE_HOME` | Optional isolated Claude runtime home. The task runner defaults this under the ignored task state so user-level settings and stale credentials cannot override the run. |

If a required value is missing, ask once and show the exact `export` line.
Most of these are hydrated from `config.yaml -> runtime_info.input.llm_api` by
`scripts/load_runtime_env.sh`; confirm `cc_provider_mode` matches the provider
(use `openai_proxy` + a running LiteLLM proxy for Qwen/GLM/sglang/vLLM).
For GitHub collection, `repos/legoflow-curator/tools/collect_prs_wo_image.py` reads
tokens from `repos/legoflow-curator/gh_token.txt` by default; set
`COLLECT_GITHUB_TOKEN_FILE` when using a different token file.

## Step 3b - Start the local LiteLLM CC proxy (openai_proxy mode only)

When `runtime_info.input.llm_api.cc_provider_mode == openai_proxy`, the Claude
Code verification path needs an isolated LiteLLM proxy:

1. Run `bash scripts/setup_cc_proxy_env.sh`. It creates
   `artifacts/envs/litellm-proxy` with the proxy extra and a compatible FastAPI
   version, separate from the Curator application environment.
2. Export `OPENAI_API_KEY`, `OPENAI_API_BASE_URL`, and `OPENAI_MODEL`. The proxy
   helper uses these runtime values before tracked `human` placeholders and
   writes its generated config only under ignored `artifacts/logs/`.
3. Run `bash scripts/start_with_openai_api.sh --proxy-only`. Override a
   conflicting host port with `LEGOFLOW_CURATOR_CC_PROXY_PORT=<free-port>`.
   The launcher verifies the new process, not merely an unrelated service
   already answering on the configured port.

This step is skipped when `cc_provider_mode == native` (no local proxy) or
when `llm_api` is still unfilled.

## Step 4 - Create expected local directories

Create directories used by scripts and smoke tests:

```bash
mkdir -p artifacts/collected_prs artifacts/swe_tasks artifacts/logs/legoflow-curator-create artifacts/claude-config
```

If the user wants the quick verification path, confirm that the submodule
sample data exists at:

- `repos/legoflow-curator/artifacts/collected_prs/python_pr_ids.txt`
- `repos/legoflow-curator/artifacts/swe_tasks/py-cc/verifiable_tasks.txt`
- `repos/legoflow-curator/artifacts/swe_tasks/py-cc/tox-dev__tox-3813/`

## Step 5 - Hand off to `/curator:check`

Finish by running the check logic. Report setup as complete only if the
environment is ready for `scripts/dryrun.sh` and the user has enough
credentials to run either a smoke test or a real batch.

## Guardrails

- Do not commit `.env`, `gh_token.txt`, API keys, or generated task outputs.
- Do not edit `config.yaml` unless the user asks for a specific config
  change.
- Do not run `legoflow-curator create`, `legoflow-curator validate`, or Docker cleanup from
  setup; leave execution to `/curator:check` or `/curator:create-tasks`.

---

## Config reference (moved from config.yaml — do not re-add as comments)

### llm_api — two API paths, fill order

legoflow-curator calls the LLM over TWO paths; set both correctly or task verification fails silently (see /curator:check reference):
1. **OpenAI-compatible path** (`api_base_url` + `pr_model`): PR evaluation and task-instruction generation.
2. **Claude Code path** (Anthropic /v1/messages, `task_model`): task completion AND the verification step that writes `verifiable_tasks.txt`.

`cc_provider_mode` selects how the Claude Code path reaches the provider:
- `native` — provider already speaks the Anthropic Messages API (real Claude, or a gateway exposing /v1/messages). `anthropic_base_url` points straight at the provider. Launch: `bash scripts/start_with_anthropic_api.sh`.
- `openai_proxy` — provider is OpenAI-only (Qwen / GLM / sglang / vLLM; these reject role:system on /v1/messages with HTTP 400). A local LiteLLM proxy translates Anthropic → OpenAI; `anthropic_base_url` points at that proxy. REQUIRED for OpenAI-only providers. Launch: `bash scripts/start_with_openai_api.sh`.

Fill `llm_api` first; `cc_provider_mode` decides which launcher to use.

**Example A — non-Anthropic model (local Qwen via openai_proxy), tested end-to-end (NOP=0/Oracle=1):**
```yaml
llm_api:
  api_key: dummy-cf
  api_base_url: https://<your-openai-compatible-endpoint>/v1
  pr_model: Qwen3.6-35B-A3B
  task_model: claude-sonnet-4-6        # any claude-* alias; proxy maps it to pr_model
  cc_provider_mode: openai_proxy
  anthropic_base_url: http://127.0.0.1:4010
  cc_proxy_port: 4010
```

**Example B — Anthropic-format model via gateway, tested end-to-end (NOP=0/Oracle=1):**
```yaml
llm_api:
  api_key: <YOUR_KEY>
  api_base_url: https://<your-anthropic-gateway>/v1            # OpenAI-compatible side of the same gateway
  pr_model: claude-opus-4-6
  task_model: claude-opus-4-6
  cc_provider_mode: native
  anthropic_base_url: https://<your-anthropic-gateway>         # provider Anthropic root, no /v1
  # cc_proxy_port not used in native mode
```

### pr_collection knobs

`enabled` is informational (wrappers do not gate on it). `filters` are global thresholds mapped to `LEGOFLOW_CURATOR_PR_*` env vars — null/absent keeps the collector's built-in default; per-language overrides live in the collector's `LANGUAGE_OVERRIDES` and win over these globals. The collector combines `gh_token.txt` (or `COLLECT_GITHUB_TOKEN_FILE`) with `GITHUB_TOKENS` / `GITHUB_TOKEN` — real tokens never go in config.yaml.

### languages

`languages.<lang>.params` (timeout / cc_timeout / n_concurrent) are read by `scripts/read_params.py` + `scripts/create_<lang>.sh`. `enabled` selects which languages `create_all_bg.sh` launches (`python scripts/read_params.py --list-enabled` shows the current set) — set it to `false` to drop a language, or run a specific `create_<lang>.sh` to generate just one.
