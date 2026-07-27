# Curator Agent Workbench

This file declares that the current directory is a `block`.

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `<repo_root>/.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` (resolve from the repo root, not from this block's directory). Every agent with this repo SHOULD READ that file before any actions.

## Block Summary

```md
Name: curator
Type: data
Config: `config.yaml`  (identity, resources, runtime I/O — one-shot per run)
Main doc: `dashboard/overview.mdx`
Definition reference: `BLOCK_DEFINITION.md`
```

Automated pipeline that converts GitHub PRs into verified SWE-Bench tasks across 8 programming languages.

## Environment Setup

### Required Environment Variables

swegen calls the LLM over **two different API paths**. The recommended way to
configure both is to fill `config.yaml -> runtime_info.input.llm_api` and let
`scripts/load_runtime_env.sh` export the env vars for you — every `create_*.sh`,
`dryrun.sh`, and the skills source it. You normally do **not** set these env
vars by hand. The mapping is:

| config.yaml `llm_api` field | Env var it hydrates | Purpose |
|---|---|---|
| `api_key` | `OPENAI_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | LLM API key (mirrored to the Anthropic path) |
| `api_base_url` | `OPENAI_API_BASE_URL` | OpenAI-compatible endpoint (PR eval + instruction generation) |
| `pr_model` | `OPENAI_MODEL` | Model for PR evaluation + instruction generation |
| `task_model` | `ANTHROPIC_MODEL` | Model for the Claude Code path |
| `anthropic_base_url` | `ANTHROPIC_BASE_URL` | Claude Code path endpoint (task completion + verification) |
| `cc_provider_mode` | `SWEGEN_CC_PROVIDER_MODE` | `native` or `openai_proxy` (see below) |
| `cc_proxy_port` | `SWEGEN_CC_PROXY_PORT` | local LiteLLM proxy port (openai_proxy only) |

GitHub tokens are never stored in `config.yaml`: runtime tokens come from
`GITHUB_TOKENS`, `GITHUB_TOKEN`, or local token files (`gh_token.txt`).

`scripts/load_runtime_env.sh` imports selected variables from the interactive
shell, then sources the block's `.env`, so `.env` overrides the imported shell
values. `config.yaml` only fills still-unset exported variables. Keep real keys
out of `config.yaml`; prefer the shell or an ignored `.env`. The collector also
combines `GITHUB_TOKENS` / `GITHUB_TOKEN` with
`repos/swegen/gh_token.txt` (or `COLLECT_GITHUB_TOKEN_FILE`).

### LLM provider modes (read this before your first run)

The two paths matter because the **Claude Code path is what writes
`verifiable_tasks.txt`** — get it wrong and verification fails *silently*: task
skeletons stay as templates, no task is verified, yet batch state still reports
`success`. There are two supported modes, selected by
`llm_api.cc_provider_mode` in `config.yaml`:

- **`native`** — your provider already speaks the Anthropic Messages API (real
  Claude, or a gateway exposing `/v1/messages`). Point `anthropic_base_url`
  straight at it. No proxy needed.
- **`openai_proxy`** — your provider is **OpenAI-only** (Qwen / GLM / sglang /
  vLLM and most self-hosted endpoints). These reject `role:system` on
  `/v1/messages` with HTTP 400, so you must run a local **LiteLLM** proxy that
  translates Anthropic → OpenAI, and point `anthropic_base_url` at it:

  ```bash
  # one-time, before `swegen create`; fill in your endpoint/model/key first.
  # The config maps the Claude Code SDK's claude-* calls to your OpenAI endpoint
  # via use_chat_completions_url_for_anthropic_messages.
  litellm --config scripts/litellm_cc_proxy.example.yaml \
          --port 4010 --host 127.0.0.1 &
  curl -sf http://127.0.0.1:4010/health   # must succeed before generating
  ```

`scripts/dryrun.sh` probes this endpoint and fails loudly when
`cc_provider_mode=openai_proxy` but the proxy is down — always run it first.

> **Pick a stable PR-evaluation endpoint.** The PR-evaluation step (OpenAI path,
> `pr_model`) expects a single JSON reply and does **not** retry on an empty or
> non-JSON response, so a flaky endpoint silently drops those PRs (logged as
> `Combined LLM call failed: Expecting value`). Two gotchas seen in practice:
> some Claude-via-OpenAI gateways force a `tool_calls` reply with empty
> `content` for this prompt (send `tool_choice: "none"`, e.g. via the proxy, to
> get text back); and reasoning models can spend the whole `max_tokens` budget on
> reasoning and return empty content. Prefer a non-reasoning, JSON-reliable model
> for `pr_model`; the `task_model` (Claude Code path) is unaffected.

### Install

`/curator:setup` does this for you (submodule init, venv, `pip install -e`,
dryrun). To do it by hand, create the venv at the path the scripts and skills
expect — `config.yaml`'s `meta_info.environment.venv_path`
(`artifacts/envs/swegen-env`); `dryrun.sh` and `load_runtime_env.sh` look for it
there:

```bash
python3 -m venv artifacts/envs/swegen-env && source artifacts/envs/swegen-env/bin/activate
pip install -e repos/swegen/
```

### Verify Docker

```bash
docker run --rm hello-world
```

## Core Workflow

The recommended way to operate this block is through its Claude plugin
(`curator-plugin`). Launch Claude from inside `subblock/curator/` and use the
slash commands for their documented preflight and confirmation steps. Only
`/curator:create-tasks` full mode passes through `start.sh` and creates an archive;
smoke and single-language modes do not:

| Command | Wraps | Purpose |
|---|---|---|
| `/curator:setup` | submodule init, venv, `pip install -e`, dryrun | Bootstrap the block |
| `/curator:check` | `scripts/dryrun.sh` + token/LLM/docker probes | Read-only preflight |
| `/curator:collect-prs` | `scripts/collect_all_bg.sh` / collector | Collect PR IDs and wait for completion (Step 1 below) |
| `/curator:create-tasks` | Full: `scripts/start_with_*.sh` → `create_all_bg.sh`; smoke/single: direct command | Generate and verify tasks from existing PR IDs (Step 2) |
| `/curator:dashboard` | `dashboard/` generator + Cloudflare sync | Progress monitoring |

`/curator:create-tasks` does not invoke `/curator:collect-prs`. For production modes,
wait for collection to finish before starting generation; the create scripts
read fixed files under `artifacts/collected_prs/`. The create-tasks skill's smoke
mode is an exception and uses a sample PR file bundled in `repos/swegen`.

The knobs each command reads live in `config.yaml`: LLM under
`runtime_info.input.llm_api`, PR collection under
`runtime_info.input.pr_collection`, per-language generation under
`runtime_info.input.languages`. Edit `config.yaml`, not the scripts. The steps
below document the underlying commands for manual operation.

### Quick Verification

Before running a large batch, a new AI agent should run the short verification flow in [`memory/quick-verify.md`](memory/quick-verify.md). It checks GitHub/LLM/Docker preflight, validates a known task, and runs a small Python smoke test with `--min-source-files 1`.

### Step 1: Collect PRs

PR collection is configured in `config.yaml -> runtime_info.input.pr_collection`
(`languages`, `repo_num`, `max_prs_per_repo`, `output_dir`, `token_limit`, and
global `filters`). `scripts/load_runtime_env.sh` exports these as
`SWEGEN_COLLECT_*` / `SWEGEN_PR_*` / `COLLECT_TOKEN_LIMIT`; the collector reads
them, falling back to its built-in defaults. Per-language threshold overrides
live in the collector's `LANGUAGE_OVERRIDES` and win over the global `filters`.
Collection tokens come from the collector's token file plus
`GITHUB_TOKENS` / `GITHUB_TOKEN`, never from `config.yaml`.

Config-driven wrapper (reads `pr_collection` via `load_runtime_env.sh`):

```bash
# full: languages from config
bash scripts/collect_all_bg.sh

# single language, other knobs from config
LANGUAGES=python bash scripts/collect_all_bg.sh
```

Or call the collector directly (small smoke; filters still from config/defaults):

```bash
source scripts/load_runtime_env.sh && load_runtime_env
python repos/swegen/tools/collect_prs_wo_image.py \
  --languages python \
  --repo_num 2 \
  --max_prs_per_repo 10 \
  --output_dir ./artifacts/collected_prs \
  --disable_progress_bar
```

Output: `artifacts/collected_prs/{language}_pr_ids.txt` (format: `owner/repo:pr-NUMBER`)

Supported languages: `python`, `javascript`, `typescript`, `go`, `c`, `cpp`, `java`, `rust`

### Step 2: Create SWE Tasks

```bash
swegen create \
  --input-ids-file ./artifacts/collected_prs/python_pr_ids.txt \
  --n-concurrent 8 \
  --output ./artifacts/swe_tasks/py-cc \
  --timeout 3600 \
  --cc-timeout 2400 \
  --no-require-issue \
  --min-source-files 2 \
  --max-source-files 10
```

The `--timeout`/`--cc-timeout` here are illustrative. The per-language
`scripts/create_<lang>.sh` read the real values from `config.yaml`
(`languages.<lang>.params`, e.g. py `3200`/`2400`); `--timeout` is the overall
per-case budget and must stay >= `--cc-timeout`. Prefer the scripts over a
hand-written invocation.

Output: task directories under `artifacts/swe_tasks/{lang}-cc/`. Verified task IDs appended to `verifiable_tasks.txt`.

`--min-source-files` controls the yield/difficulty tradeoff: `1` keeps the most PRs (including small fixes, highest throughput), while higher values keep only larger changes. Use `1` for maximum data; every current per-language script uses `2`.

Per-language scripts with tuned parameters: `bash scripts/create_{lang}.sh` where lang = py, js, ts, go, c, cpp, java, rust.

#### Launching all languages — pick by `cc_provider_mode`

Two mode-specific launchers live in `scripts/`; each refuses to run if the
config's `cc_provider_mode` does not match it, then delegates to
`scripts/start.sh` (which calls `scripts/create_all_bg.sh` and archives on exit):

```bash
# native: real Claude or any Anthropic-format gateway (no local proxy needed)
bash scripts/start_with_anthropic_api.sh

# openai_proxy: OpenAI-only provider (Qwen / GLM / sglang / vLLM); starts the
# local LiteLLM proxy (Anthropic -> OpenAI) on cc_proxy_port first, then runs
# the pipeline. Fill scripts/litellm_cc_proxy.example.yaml placeholders first.
bash scripts/start_with_openai_api.sh
```

`create_all_bg.sh` starts all eight language workers with `nohup` and returns
immediately; it does not consult each language's `enabled` value or wait for
workers. Consequently, `start.sh` archives launcher completion, not worker
completion. In `openai_proxy` mode the wrapper also stops its LiteLLM proxy when
the launcher returns, so the current wrapper does not supervise that proxy for
the detached workers' full lifetime.

### Scaled parallel runs (proven recipe)

To accumulate hundreds of verified tasks, run several `swegen create` shards
**writing to the same `--output` pool but different `--state-dir`** — appends to
`verifiable_tasks.txt` are atomic (O_APPEND), so shards never collide. Keep
`--n-concurrent` around 16–20 (CPU-bound; higher causes Docker/LLM contention).
All shards share one CC proxy (see provider modes above); confirm the LLM
endpoint's QPS supports the combined concurrency. Downstream trajectory
collection can start incrementally as soon as a pool has a handful of verified
tasks — no need to wait for the full run.

### Step 3: Validate (optional, built into create)

```bash
swegen validate ./artifacts/swe_tasks/py-cc --max-parallel 8 \
  --jobs-dir artifacts/experiments/validate-jobs
```

`--jobs-dir` defaults to `.swegen/harbor-jobs` (relative to CWD) if omitted —
always pass it explicitly so Harbor job artifacts land under `artifacts/`,
not the block root.

### Step 4: Difficulty + metadata tagging

Difficulty is scored inline during `swegen create` (via `swegen.scoring`), so a
separate batch scoring pass is no longer required. Dataset-level difficulty +
the 4-tag `[language, area, topic, bug_class]` metadata (used by the databoard)
are produced by the **single canonical tagger**,
`repos/swegen/tools/tag_task_metadata.py`, over unified JSONL datasets:

```bash
# from subblock/curator/dashboard/ (datasets exported to datasets/<id>/tasks.jsonl)
python3 ../repos/swegen/tools/tag_task_metadata.py \
  --datasets-dir datasets --dataset all --jobs 64 --retries 3
```

See `dashboard/README.md` for dataset export and endpoint configuration.

### Step 5: Extract Verified Tasks

```bash
python scripts/extract_verified_tasks.py
```

Reads `verifiable_tasks.txt` from each language, copies verified task directories to `artifacts/merged_swe_tasks/`.

## Downstream Agent Interface

Downstream agents consume verified SWE tasks for trajectory inference. The **authoritative consumer contract** is the manifest file:

- `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` — newline-delimited list of task IDs that passed NOP/Oracle validation.

Consumers MUST filter by this manifest, not by scanning `artifacts/swe_tasks/{lang}-cc/` directly — the latter also contains in-progress and failed skeletons. The tracer block does this via `prepare_tasks.sh` (manifest-filtered copy).

Two interfaces are supported:

1. **In-place** (recommended): consumer reads tasks directly from `artifacts/swe_tasks/{lang}-cc/<task_id>/`, gated by entries in `verifiable_tasks.txt`. tracer uses this path.
2. **Merged**: run `python scripts/extract_verified_tasks.py` to materialize a flat `artifacts/merged_swe_tasks/` directory containing only verified tasks.

Each task directory contains:
- `instruction.md` — problem description (input to the solving agent)
- `environment/Dockerfile` — Docker build environment
- `environment/bug.patch` — patch that introduces the bug
- `solution/fix.patch` — ground truth fix
- `tests/test.sh` — verification script (writes reward to `/logs/verifier/reward.txt`)

## Directory Layout

```
repos/swegen/         # Core Python package + tools
  src/swegen/         # Python package (CLI, task generation, validation, scoring)
  tools/              # Standalone scripts (PR collection; tag_task_metadata.py difficulty + 4-tag tagging)
scripts/              # Per-language create scripts and the two mode-specific launchers
artifacts/
  collected_prs/      # PR ID lists (input to swegen create)
  swe_tasks/          # Generated SWE tasks per language ({lang}-cc/)
  merged_swe_tasks/   # Optional flat verified-task export
  logs/               # Per-language create logs
```

## Key Files

| File | Purpose |
|------|---------|
| `config.yaml` | Single source of truth for inputs: identity, resources, runtime I/O, per-language params (`languages.<lang>.params`). One-shot per run; worker progress lives in logs and batch state. |
| `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` | Authoritative manifest of validated task IDs per language. Consumers (e.g. tracer) must filter by this file. |
| `artifacts/swe_tasks/{lang}-cc/.swegen-create-batch/` | Per-batch state JSON used by `swegen create` for resume/dedup. |
| `scripts/extract_verified_tasks.py` | Optional: merges all verified tasks into a flat `artifacts/merged_swe_tasks/` directory. |

## Coding Standards

- Python 3.12, formatted with `black` + `ruff` (line-length=100)
- Install: `pip install -e repos/swegen/`
- Run tests: `pytest repos/swegen/tests/`
- CLI entry point: `swegen` (defined in pyproject.toml)

## Per-Language Parameters

`config.yaml -> runtime_info.input.languages.<lang>.params` holds three knobs per language:

| Field | Meaning |
|---|---|
| `timeout` | per-task overall timeout (seconds) |
| `cc_timeout` | Claude Code SDK session timeout (seconds) |
| `n_concurrent` | concurrent task workers |

`scripts/create_<lang>.sh` reads these via `scripts/read_params.py` before launching `swegen create`. Edit them in `config.yaml`; no auto-tuning is performed.
The current all-language launcher ignores `enabled`; invoke only the desired
`scripts/create_<lang>.sh` files to limit the language set.

```bash
eval $(python scripts/read_params.py --lang py --config-yaml config.yaml)
echo $TIMEOUT $CC_TIMEOUT $N_CONCURRENT
```

## PR collection configuration

`config.yaml -> runtime_info.input.pr_collection` controls Step 1 (PR
collection). `scripts/load_runtime_env.sh` exports each key as an env var that
`repos/swegen/tools/collect_prs_wo_image.py` and `scripts/collect_all_bg.sh`
read; unset/empty values fall back to the collector's built-in defaults.
The block `.env` is sourced after the imported interactive environment, and
`config.yaml` fills only still-unset exported values.

| config key | env var | Meaning |
|---|---|---|
| `languages` | `SWEGEN_COLLECT_LANGUAGES` | comma-joined `--languages` list |
| `repo_num` | `SWEGEN_COLLECT_REPO_NUM` | repos with qualifying PRs per language |
| `max_prs_per_repo` | `SWEGEN_COLLECT_MAX_PRS_PER_REPO` | max qualifying PRs kept per repo |
| `output_dir` | `SWEGEN_COLLECT_OUTPUT_DIR` | where `{lang}_pr_ids.txt` is written |
| `token_limit` | `COLLECT_TOKEN_LIMIT` | first N combined file + environment tokens (0 = all) |
| `filters.min_stars` | `SWEGEN_PR_MIN_STARS` | min repo stars |
| `filters.min_merged_prs` | `SWEGEN_PR_MIN_MERGED_PRS` | min merged PRs in repo |
| `filters.min_language_percentage` | `SWEGEN_PR_MIN_LANGUAGE_PERCENTAGE` | min fraction of codebase in target language |
| `filters.max_days_since_push` | `SWEGEN_PR_MAX_DAYS_SINCE_PUSH` | skip repos idle longer than this |
| `filters.min_issue_body_length` | `SWEGEN_PR_MIN_ISSUE_BODY_LENGTH` | min linked-issue body length |
| `filters.min_files_changed` | `SWEGEN_PR_MIN_FILES_CHANGED` | min files changed in the PR |
| `filters.max_files_changed` | `SWEGEN_PR_MAX_FILES_CHANGED` | max files changed in the PR |
| `filters.max_lines_changed` | `SWEGEN_PR_MAX_LINES_CHANGED` | max (additions + deletions) in the PR |

The `filters` are **global** thresholds. Per-language overrides remain in the
collector's `LANGUAGE_OVERRIDES` dict and take precedence over these globals —
they are intentionally not surfaced in `config.yaml`. GitHub collection tokens
come from the collector's token file plus `GITHUB_TOKENS` / `GITHUB_TOKEN`,
never `config.yaml`.

## Collaboration Rules

When updating this block:
- read `dashboard/overview.mdx` first for current state
- read `config.yaml` for inputs: identity (`meta_info`), resources, runtime I/O (`runtime_info.input`/`output`), and per-language params (`runtime_info.input.languages.<lang>`). `config.yaml` is one-shot per run; use logs, batch state, and `verifiable_tasks.txt` for worker progress.
- treat `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` as the authoritative output manifest — never have downstream blocks read raw task dirs without filtering through it
- treat `artifacts/index.yaml` entries from `start.sh` as launcher archives; detached worker completion is reported by logs, batch state, and `verifiable_tasks.txt`
- use `dashboard/memory.mdx` (or `memory/`) for long-form context, experiment logs, and decisions
- use `subblock/` for nested child blocks
