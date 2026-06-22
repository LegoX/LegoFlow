# SWE-gen Agent Workbench

This file declares that the current directory is a `block`.

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `<repo_root>/.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` (resolve from the repo root, not from this block's directory). Every agent with this repo SHOULD READ that file before any actions.

## Block Summary

```md
Name: swegen
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
| `github_tokens` (top-level input) | `GITHUB_TOKENS` | comma-separated GitHub tokens |

**Priority is env > `.env` > `config.yaml`** — hydration only fills vars that are
not already set, so a stale value in your shell will *override* config.yaml. If a
run ignores your config, unset the conflicting `OPENAI_*` / `ANTHROPIC_*` shell
vars (or put the intended values in the block's `.env`). Keep real keys out of
`config.yaml`; prefer `.env` or the shell. GitHub tokens may instead go in
`gh_token.txt` (one per line) at the project root or `~/gh_token.txt`.

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

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e repos/swegen/
```

### Verify Docker

```bash
docker run --rm hello-world
```

## Core Workflow

### Quick Verification

Before running a large batch, a new AI agent should run the short verification flow in [`memory/quick-verify.md`](memory/quick-verify.md). It checks GitHub/LLM/Docker preflight, validates a known task, and runs a small Python smoke test with `--min-source-files 1`.

### Step 1: Collect PRs

```bash
python repos/swegen/tools/collect_prs_wo_image.py \
  --languages python \
  --repo_num 100 \
  --max_prs_per_repo 50 \
  --output_dir ./artifacts/collected_prs
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

Output: task directories under `artifacts/swe_tasks/{lang}-cc/`. Verified task IDs appended to `verifiable_tasks.txt`.

`--min-source-files` controls the yield/difficulty tradeoff: `1` keeps the most PRs (including small fixes, highest throughput), `2`–`3` keep only larger changes (harder tasks, lower yield). Use `1` for maximum data; the per-language scripts default to `2`–`3`.

Per-language scripts with tuned parameters: `bash scripts/create_{lang}.sh` where lang = py, js, ts, go, c, cpp, java, rust.

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
swegen validate ./artifacts/swe_tasks/py-cc --max-parallel 8
```

### Step 4: Score Tasks

```bash
python repos/swegen/tools/score_tasks.py --dir artifacts/swe_tasks/py-cc --update-toml
```

### Step 5: Extract Verified Tasks

```bash
python scripts/extract_verified_tasks.py
```

Reads `verifiable_tasks.txt` from each language, copies verified task directories to `artifacts/merged_swe_tasks/`.

## Downstream Agent Interface

Downstream agents consume verified SWE tasks for trajectory inference. The **authoritative consumer contract** is the manifest file:

- `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` — newline-delimited list of task IDs that passed NOP/Oracle validation.

Consumers MUST filter by this manifest, not by scanning `artifacts/swe_tasks/{lang}-cc/` directly — the latter also contains in-progress and failed skeletons. The trajgen block does this via `prepare_tasks.sh` (manifest-filtered copy).

Two interfaces are supported:

1. **In-place** (recommended): consumer reads tasks directly from `artifacts/swe_tasks/{lang}-cc/<task_id>/`, gated by entries in `verifiable_tasks.txt`. trajgen uses this path.
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
  tools/              # Standalone scripts (PR collection, batch scoring)
scripts/              # Per-language create scripts with tuned parameters
artifacts/
  collected_prs/      # PR ID lists (input to swegen create)
  swe_tasks/          # Generated SWE tasks per language ({lang}-cc/)
  merged_swe_tasks/   # Optional flat verified-task export
  logs/               # Adaptive tuning and create logs
```

## Key Files

| File | Purpose |
|------|---------|
| `config.yaml` | Single source of truth for inputs: identity, resources, runtime I/O, per-language tunable params. One-shot per run — no live state (live state lives in `artifacts/index.yaml`). |
| `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` | Authoritative manifest of validated task IDs per language. Consumers (e.g. trajgen) must filter by this file. |
| `artifacts/swe_tasks/{lang}-cc/.swegen-create-batch/` | Per-batch state JSON used by `swegen create` for resume/dedup. |
| `scripts/extract_verified_tasks.py` | Optional: merges all verified tasks into a flat `artifacts/merged_swe_tasks/` directory. |

## Coding Standards

- Python 3.12, formatted with `black` + `ruff` (line-length=100)
- Install: `pip install -e repos/swegen/`
- Run tests: `pytest repos/swegen/tests/`
- CLI entry point: `swegen` (defined in pyproject.toml)

## Adaptive Parameter Tuning

### Overview

You (the AI agent) monitor and tune the SWE-gen pipeline. Tunable params live in `config.yaml` under `runtime_info.input.languages.<lang>.params` and the bounds under `runtime_info.input.global`. `config.yaml` holds no live status — track per-cycle metrics in `artifacts/index.yaml` (and the decision log below).

### Monitoring Cycle (every 30 minutes)

1. **Collect status**: Count verified tasks from `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt`. Count failures from batch state in `artifacts/swe_tasks/{lang}-cc/.swegen-create-batch/`. Record the cycle's counts/rate in `artifacts/index.yaml`.
2. **Decide tuning**: If `success_rate < 0.15` for 2 consecutive cycles, increase `timeout` (+400) or `cc_timeout` (+300) in `languages.<lang>.params`. If `success_rate > 0.4` and `n_concurrent < 24`, increase `n_concurrent` (+4). If `success_rate >= 0.25`, do nothing.
3. **Check PR pool**: If the remaining PR pool drops below `global.pr_pool_min_threshold`, run `python repos/swegen/tools/collect_prs_wo_image.py --languages {lang} --repo_num 100 --max_prs_per_repo 50 --output_dir ./artifacts/collected_prs`, then deduplicate against processed PRs and update the input-ids-file.

### Constraints

- Adjust at most 1 parameter per language per cycle
- Wait ≥ 2 cycles (60 min) between adjustments for the same language
- Parameter bounds (read from `runtime_info.input.global.param_bounds`): timeout [2400, 5400], cc_timeout [1800, 4200], n_concurrent [4, 32]
- Do NOT restart running create scripts unless the zero-success streak reaches `global.restart_policy.zero_success_cycles`
- Log every decision to `artifacts/logs/adaptive_decisions.jsonl`

### Reading params from config.yaml

```bash
eval $(python scripts/read_params.py --lang py --config-yaml config.yaml)
echo $TIMEOUT $CC_TIMEOUT $N_CONCURRENT
```

### Decision log format

```json
{"timestamp": "2026-04-22T14:30:00Z", "lang": "rust", "action": "adjust_param", "param": "timeout", "old": 3600, "new": 4000, "reason": "success_rate 0.08 for 2 consecutive cycles"}
```

## Collaboration Rules

When updating this block:
- read `dashboard/overview.mdx` first for current state
- read `config.yaml` for inputs: identity (`meta_info`), resources, runtime I/O (`runtime_info.input`/`output`), and per-language tunable params (`runtime_info.input.languages.<lang>` and `.global`). `config.yaml` is one-shot per run — for live state look at `artifacts/index.yaml`.
- treat `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt` as the authoritative output manifest — never have downstream blocks read raw task dirs without filtering through it
- after every run, archive params, metrics, inputs, and log into `artifacts/archives/run_NNN/` and append to `artifacts/index.yaml`
- use `dashboard/memory.mdx` (or `memory/`) for long-form context, experiment logs, and decisions
- use `subblock/` for nested child blocks
