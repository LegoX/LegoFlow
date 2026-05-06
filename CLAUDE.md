# SWE Lego Live

Root orchestration block for the self-evolving LLM development pipeline.

## Block Identity

- **Name**: swe_lego_live
- **Role**: Coordinates the full pipeline across data curation, training, and evaluation
- **Parent**: none (root block)
- **Children**: swegen, trajgen, sft, rl

## What To Read First

1. `config.yaml` — block identity, resources, dependency wiring, runtime values, and status
2. `dashboard/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml` → `runtime_info.input`):
- `github_token`: GitHub API token for SWE-gen repo access
- `anthropic_api_key`: Anthropic API key for Claude Code agent
- `openai_api_key`: OpenAI API key for PR evaluation (optional)

**Outputs** (written to `config.yaml` → `runtime_info.output`):
- `eval_report`: Final evaluation results across all pipeline runs
- `pipeline_version`: Version identifier for this pipeline run

## Repos

- `repos/SWE-gen/`: SWE instance generation tooling (git submodule pinned to specific commit)

## How To Run

- `scripts/start.sh`: Execute the full pipeline
- `scripts/dryrun.sh`: Validate config, inputs, and required paths without side effects
- `scripts/clean.sh`: Remove temporary working files

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing:
- `metadata.yaml`: run id, timestamps, stage, results, repo commits, copy of inputs
- `config.yaml`: snapshot of config at run time
- `scripts/`: copy of scripts used
- `repos/`: snapshot of repo state
- `session.log`: Claude Code session record
- `monitor.md`: agent monitor output

Append one entry to `artifacts/index.yaml` with `archive: artifacts/archives/run_NNN/`.

## Memory

Long-form notes and decisions are kept in `dashboard/memory.md` alongside the overview.

## Inter-Block Wiring

Values from child blocks are declared in `meta_info.subblocks[].dependencies`. Do not duplicate them in `runtime_info.input`. Only external values (API keys, human decisions) go in `runtime_info.input`.

## Status Updates

Keep `status` section in `config.yaml` current throughout execution. Update `phase`, `progress`, `next_steps`, and `blockers` as work progresses.

## Remote Execution

This block has no remote resource declared (`meta_info.resources.ip` is null), so execution is local.
