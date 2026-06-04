# SWE-gen Agent Workbench

This file declares that the current directory is a `block`.

For the canonical definition of a block, the field semantics, and the default directory contract, read `BLOCK_DEFINITION.md`.

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

Set these BEFORE running any command:

| Variable | Purpose | Example |
|----------|---------|---------|
| `GITHUB_TOKENS` | Comma-separated GitHub tokens for API access | `ghp_xxx,ghp_yyy` |
| `OPENAI_API_KEY` | LLM API key (auto-mirrored to ANTHROPIC_API_KEY) | `sk-xxx` |
| `OPENAI_API_BASE_URL` | OpenAI-compatible API endpoint | `https://api.example.com/v1` |
| `OPENAI_MODEL` | Model for PR evaluation + instruction generation | `gpt-4o` |
| `ANTHROPIC_MODEL` | Model for Claude Code SDK (task completion) | `claude-sonnet-4-6` |

Optional: place GitHub tokens in `gh_token.txt` (one per line) at project root or `~/gh_token.txt`.

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

Before running a large batch, a new AI agent should run the short verification flow in [`quick-verify.md`](quick-verify.md). It checks GitHub/LLM/Docker preflight, validates a known task, and runs a 10-PR Python smoke test with `--min-source-files 1`.

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

Per-language scripts with tuned parameters: `bash scripts/create_{lang}.sh` where lang = py, js, ts, go, c, cpp, java, rust.

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

You (the AI agent) monitor and tune the SWE-gen pipeline. Configuration and per-language status live in `config.yaml` under `runtime_info.input.languages.<lang>` and `runtime_info.input.global`.

### Monitoring Cycle (every 30 minutes)

1. **Collect status**: Count verified tasks from `artifacts/swe_tasks/{lang}-cc/verifiable_tasks.txt`. Count failures from batch state in `artifacts/swe_tasks/{lang}-cc/.swegen-create-batch/`. Update `config.yaml` → `runtime_info.input.languages.<lang>.status` fields.
2. **Decide tuning**: If `success_rate < 0.15` for 2 consecutive cycles, increase `timeout` (+400) or `cc_timeout` (+300). If `success_rate > 0.4` and `n_concurrent < 24`, increase `n_concurrent` (+4). If `success_rate >= 0.25`, do nothing.
3. **Check PR pool**: If `pr_pool_remaining < 100`, run `python repos/swegen/tools/collect_prs_wo_image.py --languages {lang} --repo_num 100 --max_prs_per_repo 50 --output_dir ./artifacts/collected_prs`, then deduplicate against processed PRs and update the input-ids-file.

### Constraints

- Adjust at most 1 parameter per language per cycle
- Wait ≥ 2 cycles (60 min) between adjustments for the same language
- Parameter bounds (read from `runtime_info.input.global.param_bounds`): timeout [2400, 5400], cc_timeout [1800, 4200], n_concurrent [4, 32]
- Do NOT restart running create scripts unless `zero_success_streak >= 3`
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
