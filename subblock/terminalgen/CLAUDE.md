# Terminal-gen Agent Workbench

This file declares that the current directory is a `block`.

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `<repo_root>/.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` (resolve from the repo root, not from this block's directory). Every agent with this repo SHOULD READ that file before any actions.

## Block Summary

```md
Name: terminalgen
Type: data
Config: `config.yaml`  (identity, resources, runtime I/O — one-shot per run)
Main doc: `dashboard/overview.mdx`
Definition reference: `BLOCK_DEFINITION.md`
```

Automated pipeline that converts StackOverflow Q&A into verified terminal-bench tasks across 13 task domains. This is the terminal-task sibling of `swegen` (which builds verified SWE tasks). It wraps the [terminal-lego](https://github.com/SWE-Lego/terminal-lego) pipeline, pinned as a read-only submodule under `repos/terminal-lego/`.

**Never modify `repos/terminal-lego/`** — it is a pinned upstream dependency. All adaptation lives in this block's `scripts/`, `config.yaml`, and the conversion helper.

## Environment Setup

### Required Environment Variables

Set these BEFORE running any command:

| Variable | Purpose | Example |
|----------|---------|---------|
| `OPENAI_API_KEY` | LLM API key for the terminal-lego generator | `sk-xxx` |
| `OPENAI_API_BASE_URL` | OpenAI-compatible API endpoint (generator passes this via `--api-base`) | `https://api.example.com/v1` |
| `MODEL_NAME` | Model for task generation | `deepseek-v4-flash` |
| `SO_API_KEY` | StackExchange API key (10000 req/day with key, 300 without) | `rl_xxx` |

Optional: place the StackExchange key in `~/.bashrc` as `export SO_API_KEY=...`; `scripts/load_runtime_env.sh` hydrates it.

### Install

```bash
python3 -m venv artifacts/envs/terminalgen-env && source artifacts/envs/terminalgen-env/bin/activate
pip install -r requirements.txt
```

### Verify Docker

```bash
docker run --rm hello-world
```

## Core Workflow

### Quick Verification

Before running a large batch, a new AI agent should run the short verification flow in [`memory/quick-verify.md`](memory/quick-verify.md). It checks SO/LLM/Docker preflight, replays a known-good verified task (`https-nginx-cert-setup`), and runs a small single-domain smoke.

### Step 1: Scrape StackOverflow Questions

```bash
bash scripts/scrape_so_questions.sh <round> <count>
# e.g. bash scripts/scrape_so_questions.sh 904 200
```

Wraps `repos/terminal-lego/scraper/so_scraper.py` then buckets the result by each domain's `tag_filter`.
Output: `artifacts/collected_questions/{domain}_so_data.json` (one bucket per domain).

Supported domains: `core-terminal-os`, `versioning-containers`, `networking-services`, `file-text-processing`, `python-ecosystem`, `ml-data`, `databases-storage`, `web-automation-apis`, `security-cryptography`, `debugging-reliability`, `algorithms-concurrency`, `media-scientific`, `build-editor-tooling`.

### Step 2: Create Terminal Tasks

```bash
bash scripts/create_domain.sh <domain> [limit] [start]
# e.g. bash scripts/create_domain.sh security-cryptography      # all questions in the bucket
#      bash scripts/create_domain.sh security-cryptography 6 0  # cost-capped: 6 candidates from index 0
```

`limit` caps how many bucket questions to generate this invocation (cost control);
`start` is the 0-based bucket index (chunks never collide — task ids are `task_{start+i}`).
Each invocation stages candidates under `{domain}-tl/_candidates/s<start>/` and
validates only that chunk.

Internally runs (with per-domain tuned params from `config.yaml`):

```bash
python repos/terminal-lego/generator/task_generator.py \
  --input artifacts/collected_questions/{domain}_so_data.json \
  --output artifacts/terminal_tasks/{domain}-tl/_candidates/s{start} \
  --workers $GEN_WORKERS --start {start} [--limit {limit}] \
  --api-base "$OPENAI_API_BASE_URL" --model "$MODEL_NAME"

python repos/terminal-lego/validator/validate_tasks.py \
  --input artifacts/terminal_tasks/{domain}-tl/_candidates/s{start} \
  --output artifacts/terminal_tasks/{domain}-tl \
  --workers $VAL_WORKERS --timeout $VAL_TIMEOUT
```

**Batch to a verified target** (cost-controlled; generates in chunks until each
domain reaches N verified or a per-domain candidate cap):

```bash
CHUNK=6 CAND_CAP=24 bash scripts/batch_verify.sh 5 core-terminal-os python-ecosystem
# target 5 verified per domain; omit domain args to run all enabled domains
```


> **IMPORTANT**: the generator reads the endpoint from `--api-base`; always pass `"$OPENAI_API_BASE_URL"` (NOT `OPENAI_API_BASE`). The validator only discovers directories with a `task_*` prefix.

Output: task directories under `artifacts/terminal_tasks/{domain}-tl/`. Verified task IDs are recorded in `verifiable_tasks.txt` (rebuilt from the validated task dirs each run).

All domains in the background (full run + archive): `bash scripts/start.sh`. Use
`scripts/batch_verify.sh` instead when you want a cost-capped, target-driven run
(sequential, stops per domain at N verified, no run archiving).

### Step 3: Extract & Convert Verified Tasks

```bash
python scripts/extract_verified_tasks.py
```

Reads `verifiable_tasks.txt` from each domain, converts each task's `task.toml` from terminal-lego **v1.0** schema to **harbor 1.1** schema, and copies into `artifacts/merged_terminal_tasks/`. This merged directory is what downstream blocks consume.

## Downstream Agent Interface

Downstream agents consume verified terminal tasks for trajectory inference. The **authoritative consumer contract** is the manifest file:

- `artifacts/terminal_tasks/{domain}-tl/verifiable_tasks.txt` — newline-delimited list of task IDs that passed Docker round-trip validation (reward=1.0).

Consumers MUST filter by this manifest, not by scanning `artifacts/terminal_tasks/{domain}-tl/` directly — the latter also contains `_candidates/` (in-progress and failed skeletons).

Two interfaces are supported:

1. **In-place** (terminal-lego v1.0 schema): consumer reads tasks directly from `artifacts/terminal_tasks/{domain}-tl/<task_id>/`, gated by entries in `verifiable_tasks.txt`.
2. **Merged** (harbor 1.1 schema, recommended): run `python scripts/extract_verified_tasks.py` to materialize a flat `artifacts/merged_terminal_tasks/` directory with harbor-1.1 `task.toml` — directly loadable by the harbor task runner used by trajgen/sft/eval.

Each task directory contains:
- `instruction.md` — problem description (input to the solving agent)
- `task.toml` — task metadata (v1.0 in-place; harbor 1.1 in merged)
- `environment/Dockerfile` + `environment/task_file/` — Docker build environment and seed files
- `solution/solve.sh` — reference solution script
- `tests/test.sh` + `tests/test_outputs.py` — verification (writes reward to `/logs/verifier/reward.txt`)

## Directory Layout

```
repos/terminal-lego/    # Pinned upstream pipeline (READ-ONLY): scraper/, generator/, validator/
scripts/                # Per-domain scrape/create scripts + conversion + lifecycle
artifacts/
  collected_questions/  # Per-domain bucketed SO question JSON (input to generator)
  terminal_tasks/       # Generated terminal tasks per domain ({domain}-tl/), v1.0 schema
  merged_terminal_tasks/# Flat verified-task export converted to harbor 1.1 (downstream input)
  logs/                 # Adaptive tuning and create logs
```

## Key Files

| File | Purpose |
|------|---------|
| `config.yaml` | Single source of truth for inputs: identity, resources, runtime I/O, per-domain tunable params. One-shot per run — no live state (live state lives in `artifacts/index.yaml`). |
| `artifacts/terminal_tasks/{domain}-tl/verifiable_tasks.txt` | Authoritative manifest of validated task IDs per domain. Consumers must filter by this file. |
| `scripts/scrape_so_questions.sh` | Wraps terminal-lego's scraper and buckets questions by domain `tag_filter`. |
| `scripts/create_domain.sh` | Per-domain generate + Docker-validate, parameterized by `config.yaml`. |
| `scripts/extract_verified_tasks.py` | Converts verified v1.0 tasks → harbor 1.1 and merges into `artifacts/merged_terminal_tasks/`. |

## Coding Standards

- Python 3.10+; runtime deps are `requests` (terminal-lego) + `PyYAML` (this block's config scripts), both in `requirements.txt`
- Install: `pip install -r requirements.txt`
- Never edit files under `repos/terminal-lego/` — fork-and-pin upstream changes instead
- Generator must always receive `--api-base "$OPENAI_API_BASE_URL"`

## Adaptive Parameter Tuning

### Overview

You (the AI agent) monitor and tune the terminal-gen pipeline. Configuration and per-domain status live in `config.yaml` under `runtime_info.input.domains.<domain>` and `runtime_info.input.global`.

### Monitoring Cycle (every 30 minutes)

1. **Collect status**: Count verified tasks from `artifacts/terminal_tasks/{domain}-tl/verifiable_tasks.txt`. Count failures from `validation_report.json` under each `{domain}-tl/`. Update `config.yaml` → `runtime_info.input.domains.<domain>.status` fields.
2. **Decide tuning**: If `success_rate < 0.15` for 2 consecutive cycles, increase `val_timeout` (+60). If `success_rate > 0.4` and `gen_workers < 12`, increase `gen_workers` (+1). If `success_rate >= 0.25`, do nothing.
3. **Check question pool**: If `question_pool_remaining < 100`, run `bash scripts/scrape_so_questions.sh <next_round> 200`, then re-bucket and dedup against processed questions.

### Constraints

- Adjust at most 1 parameter per domain per cycle
- Wait ≥ 2 cycles (60 min) between adjustments for the same domain
- Parameter bounds (read from `runtime_info.input.global.param_bounds`): gen_workers [1, 16], val_workers [1, 8], val_timeout [120, 1800]
- Do NOT restart running create scripts unless `zero_success_streak >= 3`
- Log every decision to `artifacts/logs/adaptive_decisions.jsonl`

### Reading params from config.yaml

```bash
eval $(python scripts/read_params.py --domain security-cryptography --config-yaml config.yaml)
echo $GEN_WORKERS $VAL_WORKERS $VAL_TIMEOUT
```

### Decision log format

```json
{"timestamp": "2026-06-23T14:30:00Z", "domain": "ml-data", "action": "adjust_param", "param": "val_timeout", "old": 360, "new": 420, "reason": "success_rate 0.08 for 2 consecutive cycles"}
```

## Collaboration Rules

When updating this block:
- read `dashboard/overview.mdx` first for current state
- read `config.yaml` for inputs: identity (`meta_info`), resources, runtime I/O (`runtime_info.input`/`output`), and per-domain tunable params (`runtime_info.input.domains.<domain>` and `.global`). `config.yaml` is one-shot per run — for live state look at `artifacts/index.yaml`.
- treat `artifacts/terminal_tasks/{domain}-tl/verifiable_tasks.txt` as the authoritative output manifest — never have downstream blocks read raw task dirs without filtering through it
- NEVER modify `repos/terminal-lego/`; it is a pinned read-only upstream dependency
- after every run, archive params, metrics, inputs, and log into `artifacts/archives/run_NNN/` and append to `artifacts/index.yaml`
- use `dashboard/memory.mdx` (or `memory/`) for long-form context, experiment logs, and decisions
- use `subblock/` for nested child blocks
