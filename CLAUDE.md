# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# SWE Lego Live

Root orchestration block for the self-evolving LLM development pipeline. Coordinates data curation (swegen), trajectory generation (trajgen), supervised fine-tuning (sft), and reinforcement learning (rl) in sequence.

## Block System

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md` and `config.yaml`.

**Full specification**: `BLOCK_DEFINITION.md` — every agent with this repo SHOULD READ it before any actions.

### config.yaml schema

Every block's `config.yaml` follows this structure:

```yaml
meta_info:
  name, label, description, parent
  subblocks:
    <child>:
      role:
      dependencies:
        <input_key>: <source_block>.output.<key>  # or: human
  repos: {}          # name → {commit_id, role}
  resources:
    ip:              # 'local' (default) or null = run on current host; remote IP = run via SSH+tmux
    pwd:             # working directory on remote node (only used when ip is a remote IP)

runtime_info:
  input: {}          # ONLY external values (API keys, human decisions)
  output: {}         # values produced for downstream blocks

evolving:
  tunable_params: {} # auto-tuned parameters with bounds
```

`config.yaml` is **one-shot per run**: every key is configuration. Live state (running / completed / failed) lives in `artifacts/index.yaml` (written automatically by `scripts/archive_run.sh`'s EXIT trap), not in `config.yaml`.

**Wiring rule**: inter-block values go in `meta_info.subblocks[].dependencies`, never in `runtime_info.input`. Only values originating outside the block tree go in `runtime_info.input`.

### Execution location rule

**Default: run locally.** Unless explicitly told otherwise, agents should treat `meta_info.resources.ip: local` (or null) as the intended setting and execute on the current host inside a local tmux session — no SSH, no rsync. Do not "restore" an old remote IP found in git history or older CLAUDE.md revisions; the local default is intentional.

If — and only if — `meta_info.resources.ip` is set to a real remote IP, the agent **must** SSH into that node and run inside a tmux session there, and confirm with the user whether code needs to be synced or is already present at the remote path.

## Block Identity (Root)

- **Name**: swe_lego_live
- **Parent**: none
- **Children**: swegen → trajgen → sft → rl

## What To Read First

1. `dashboard/overview.mdx` — current state narrative and new-user quickstart
2. `subblock/swegen/config.yaml` and `subblock/trajgen/config.yaml` — identity, resources, dependency wiring, and runtime values of the two active subblocks. Live state is in each subblock's `artifacts/index.yaml`, not `config.yaml`.
3. `BLOCK_DEFINITION.md` — full block system specification

The root block has no `config.yaml` of its own; inputs and outputs are owned by the subblock configs listed below. Each subblock has its own `CLAUDE.md` agent contract.

## Input/Output Contract

The root block does not consume external inputs directly. Required external values are filled into each active subblock's `runtime_info.input`:

**swegen** (`subblock/swegen/config.yaml` → `runtime_info.input`):
- `github_tokens`: comma-separated GitHub API tokens for PR collection
- `llm_api.api_key`, `llm_api.api_base_url`: OpenAI-compatible LLM endpoint
- `llm_api.pr_model`, `llm_api.task_model`: model names for PR evaluation and task completion

**trajgen** (`subblock/trajgen/config.yaml` → `runtime_info.input`):
- `llm_api.api_key`, `llm_api.api_base_url`, `llm_api.model`: OpenAI-compatible LLM endpoint and model used by the per-job LiteLLM proxy

**Outputs** (downstream-consumable artifacts):
- `swegen.output.swe_tasks_dir`: verified SWE tasks under `subblock/swegen/artifacts/swe_tasks/{lang}-cc/`. The authoritative manifest is `{lang}-cc/verifiable_tasks.txt` — only task IDs in that file have passed NOP/Oracle validation.
- `trajgen.output`: raw agent trajectories under `subblock/trajgen/artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`

**Producer→consumer contract**: trajgen consumes **only** tasks listed in swegen's `verifiable_tasks.txt`. `subblock/trajgen/scripts/prepare_tasks.sh` enforces this by filtering through the manifest when copying from a local task source; task IDs already processed are tracked in `subblock/trajgen/artifacts/consumption_ledger.yaml` and re-excluded via `HARBOR_EXCLUDE_TASKS` in trajgen's `config.yaml`.

## How To Run

```bash
scripts/dryrun.sh   # validate config, inputs, and required paths (no side effects)
scripts/start.sh    # execute the full pipeline
scripts/clean.sh    # remove temporary working files
```

## Subblocks

All subblocks run **locally** by default (`meta_info.resources.ip: local`). Override to a remote IP only on explicit user request.

| Block | Execution | Key tool | Status |
|---|---|---|---|
| `subblock/swegen/` | Local (CPU + Docker) | `swegen` CLI + GitHub API | Adaptive per-language task generation |
| `subblock/trajgen/` | Local (CPU + Docker) | Harbor + LiteLLM proxy | Trajectory generation from SWE instances |
| `subblock/sft/` | Local (needs 8× GPU) | LLaMA-Factory + DeepSpeed ZeRO-3 | SFT on Qwen3-8B |
| `subblock/rl/` | Local (needs 8× GPU) | Harbor + vLLM + verl | Online RL on Qwen3-30B |

Each subblock has its own `CLAUDE.md` with its full agent contract.

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing:

| File | Content |
|---|---|
| `metadata.yaml` | run id, timestamps, phase/stage, results, repo commit ids, copy of inputs |
| `config.yaml` | snapshot of config at run time |
| `scripts/` | copy of scripts executed |
| `repos/` | snapshot of repo state |
| `session.log` | Claude Code session record |
| `monitor.md` | agent monitor output |

Append one entry to `artifacts/index.yaml`:
```yaml
- id: run_001
  started_at: "..."
  completed_at: "..."
  status: completed   # running | completed | failed
  archive: artifacts/archives/run_001/
  notes: "one-line summary"
```

## Memory and Live State

- Long-form notes and decisions: `dashboard/memory.mdx`
- Live state (what's running, what just finished): the newest entry in each subblock's `artifacts/index.yaml`, written automatically by `scripts/archive_run.sh` (invoked from `start.sh`'s EXIT trap). `config.yaml` is one-shot per run and is not edited during execution.
