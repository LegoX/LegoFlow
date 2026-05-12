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
    ip:              # remote node IP; null = run locally
    pwd:             # working directory on remote node

runtime_info:
  input: {}          # ONLY external values (API keys, human decisions)
  output: {}         # values produced for downstream blocks

status:
  phase:             # idle | running | done | blocked
  progress, next_steps, blockers, last_updated

evolving:
  tunable_params: {} # auto-tuned parameters with bounds
```

**Wiring rule**: inter-block values go in `meta_info.subblocks[].dependencies`, never in `runtime_info.input`. Only values originating outside the block tree go in `runtime_info.input`.

### Remote execution rule

If `meta_info.resources.ip` is set, the agent **must** SSH into that node and run inside a tmux session — never run a remote-resource block locally. Confirm with the user whether code needs to be synced or is already present at the remote path.

## Block Identity (Root)

- **Name**: swe_lego_live
- **Parent**: none
- **Children**: swegen → trajgen → sft → rl

## What To Read First

1. `config.yaml` — block identity, resources, dependency wiring, runtime values, and status
2. `dashboard/overview.mdx` — current state narrative
3. `BLOCK_DEFINITION.md` — full block system specification

## Input/Output Contract

**Inputs** (`config.yaml` → `runtime_info.input`):
- `github_token`: GitHub API token for SWE-gen repo access
- `anthropic_api_key`: Anthropic API key for Claude Code agent
- `openai_api_key`: OpenAI API key for PR evaluation (optional)

**Outputs** (`config.yaml` → `runtime_info.output`):
- `eval_report`: Final evaluation results across all pipeline runs
- `pipeline_version`: Version identifier for this pipeline run

## How To Run

```bash
scripts/dryrun.sh   # validate config, inputs, and required paths (no side effects)
scripts/start.sh    # execute the full pipeline
scripts/clean.sh    # remove temporary working files
```

## Subblocks

| Block | Remote? | Key tool | Status |
|---|---|---|---|
| `subblock/swegen/` | Yes (192.168.35.240) | `swegen` CLI + GitHub API | Adaptive per-language task generation |
| `subblock/trajgen/` | No | Harbor + LiteLLM proxy | Trajectory generation from SWE instances |
| `subblock/sft/` | No (needs 8× GPU) | LLaMA-Factory + DeepSpeed ZeRO-3 | SFT on Qwen3-8B |
| `subblock/rl/` | No (needs 8× GPU) | Harbor + vLLM + verl | Online RL on Qwen3-30B |

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

## Memory and Status

- Long-form notes and decisions: `dashboard/memory.md`
- Keep `status` in `config.yaml` current throughout execution (phase, progress, next_steps, blockers)
