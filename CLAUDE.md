# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# SWE Lego Live

Root orchestration block for the self-evolving LLM development pipeline. Coordinates data curation (swegen), trajectory generation (trajgen), supervised fine-tuning (sft), and reinforcement learning (rl) in sequence.

## Block System

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md`. Every agent with this repo SHOULD READ that file before any actions.

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
    directory:       # working directory on remote node (only used when ip is a remote IP)

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

1. `subblock/swegen/config.yaml` and `subblock/trajgen/config.yaml` — identity, resources, dependency wiring, and runtime values of the two active subblocks. Live state is in each subblock's `artifacts/index.yaml`, not `config.yaml`.
2. `.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` — full block system specification

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
- `terminalgen.output.terminal_tasks_dir`: verified **terminal** tasks (terminal-lego v1.0) under `subblock/terminalgen/artifacts/terminal_tasks/{domain}-tl/`, with per-domain manifests `verifiable_tasks.txt`. `extract_verified_tasks.py` optionally flattens them into `merged_terminal_tasks/` (same format). terminalgen is a task source **parallel to swegen** — trajgen can consume either via its `task_source.provider` selector.
- `trajgen.output.raw_trajectories_dir`: raw agent trajectories under `subblock/trajgen/artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`
- `trajgen.output.sft_data_dir`: LLaMA-Factory LF-format SFT JSON converted from those trajectories at `subblock/trajgen/artifacts/sft_data/<job>/lf.json` (produced by `subblock/trajgen/scripts/convert_trajectories.sh`, which runs the `swe_data_process` converters under their own uv env at `subblock/trajgen/artifacts/env/swe-data-process-uv`)

**Producer→consumer contract**: trajgen consumes **only** tasks listed in swegen's `verifiable_tasks.txt`. `subblock/trajgen/scripts/prepare_tasks.sh` enforces this by filtering through the manifest when copying from a local task source; task IDs already processed are tracked in `subblock/trajgen/artifacts/consumption_ledger.yaml` and re-excluded via `HARBOR_EXCLUDE_TASKS` in trajgen's `config.yaml`. When the `sft` subblock is added, it should wire `sft.meta_info.subblocks[].dependencies.training_data: trajgen.output.sft_data_dir` rather than reading raw trajectories directly.

## How To Run

**Mandatory workflow: check → confirm → run.** Agents must never skip the confirmation step.

1. **Check**: Run `/root:check` (or `bash scripts/dryrun.sh` for a single block). This validates config, inputs, paths, GPUs, Docker/K8s connectivity, credentials, and model compatibility — all in one pass, with no side effects.
2. **Confirm**: Present the check results and run configuration summary to the user. **Wait for explicit user confirmation** ("yes", "go ahead", etc.) before proceeding. Never auto-launch — heavy operations (multi-hour GPU training, multi-container rollouts) are expensive and hard to reverse.
3. **Run**: Only after user confirmation, execute `/root:run` (or `bash scripts/start.sh`).

```bash
scripts/dryrun.sh   # validate config, inputs, and required paths (no side effects)
scripts/start.sh    # execute the full pipeline (ONLY after user confirms)
scripts/clean.sh    # remove temporary working files
```

## Subblocks

All subblocks run **locally** by default (`meta_info.resources.ip: local`). Override to a remote IP only on explicit user request.

| Block | Execution | Key tool | Status |
|---|---|---|---|
| `subblock/swegen/` | Local (CPU + Docker) | `swegen` CLI + GitHub API | Adaptive per-language task generation |
| `subblock/terminalgen/` | Local (CPU + Docker) | `terminal-lego` pipeline + StackExchange API | Adaptive per-domain terminal task generation (parallel to swegen) |
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

## Live State

Live state (what's running, what just finished) lives in the newest entry of each subblock's `artifacts/index.yaml`, written automatically by `scripts/archive_run.sh` (invoked from `start.sh`'s EXIT trap). `config.yaml` is one-shot per run and is not edited during execution.
