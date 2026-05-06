# trajgen

Generates raw agent trajectories with Harbor for downstream SFT data conversion.

## Block Identity

- **Name**: trajgen
- **Role**: Data generation - creates agent trajectories from SWE tasks
- **Parent**: swe_lego_live
- **Children**: none (leaf block)

## What To Read First

1. `config.yaml` — block identity, Harbor config, runtime values, and status
2. `dashboard/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml` → `runtime_info.input`):
- `repositories`: Harbor Git URL, branch, commit, path, and read-only policy
- `environment`: Harbor uv environment path and LiteLLM runtime version
- `model_api`: Raw upstream API config (model, base URL, key, token costs)
- `litellm_proxy`: LiteLLM config template, port, and master key
- `task_source`: SWE task source (provider, dataset name, split)
- `harbor_job`: Jobs directory, concurrency, retries, timeout multiplier
- `agent`: Agent name, version, runtime image, max turns, temperature

**Outputs** (written to `config.yaml` → `runtime_info.output`):
- `raw_trajectories`: Harbor job directories with LiteLLM trajectory logs at `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`

## Repos

- `repos/harbor/`: Trajectory generation runtime (managed local-only dependency, not committed to Live repo)

## How To Run

- `scripts/update_repos.sh`: Clone or update repos/harbor
- `scripts/prepare_tasks.sh`: Prepare Harbor task directories under artifacts/tasks
- `scripts/dryrun.sh`: Validate config, Harbor repo state, environments, and task directories
- `scripts/start.sh`: Generate LiteLLM config, start proxy, run Harbor job
- `scripts/clean.sh`: Remove gitignored runtime outputs

## Repository Policy

Harbor is a managed local dependency. Do not edit Harbor source inside this Live block. Use `scripts/update_repos.sh` to clone/fetch/checkout the configured ref. The script refuses to update if the Harbor worktree has local modifications.

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing metadata.yaml, config snapshot, scripts copy, session.log, and monitor.md. Append entry to `artifacts/index.yaml`.

## Memory

Long-form notes, repo policy, and operational decisions are kept in `dashboard/memory/`.

## Remote Execution

This block has no remote resource declared, so execution is local.
