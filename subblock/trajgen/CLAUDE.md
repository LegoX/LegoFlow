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

**Inputs** (read from `config.yaml`):
- `meta_info.repositories.harbor`: Harbor Git URL, branch, commit, path, and read-only policy
- `meta_info.environment`: Harbor uv environment path and LiteLLM venv path
- `runtime_info.input.llm_api`: raw upstream API config (model, api_base_url, api_key, optional token costs) — used to build the per-job LiteLLM proxy config
- `runtime_info.input.litellm_proxy`: LiteLLM config template, port, and master key
- `runtime_info.input.task_source`: SWE task source (provider, dataset_name pointing at swegen's `swe_tasks/`, split)
- `runtime_info.input.harbor_job`: jobs directory, concurrency, retries, timeout multiplier
- `runtime_info.input.agent`: agent name, version, runtime image, max turns, temperature
- `environment.extra.HARBOR_EXCLUDE_TASKS`: space-separated list of task IDs Harbor must skip (prior timeouts/OOMs + tasks already consumed per the ledger)

**Outputs** (written to `config.yaml` → `runtime_info.output`):
- `raw_trajectories`: Harbor job directories with LiteLLM trajectory logs at `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`

## Task Consumption Contract

Trajgen **only** runs trajectories for tasks listed in swegen's `verifiable_tasks.txt`:

1. `scripts/prepare_tasks.sh` detects `<source>/verifiable_tasks.txt` and copies only listed task IDs into `artifacts/tasks/<dataset>/` (manifest-filtered copy via `copy_harbor_tasks_filtered`). If the manifest is missing it falls back to copying every task dir — keep one in the source.
2. `artifacts/consumption_ledger.yaml` is the source of truth for which task IDs have already been processed. Status values: `pending | running | done | failed | skipped`. Every task with status `done`, `failed` (excluded), or `skipped` must also appear in `HARBOR_EXCLUDE_TASKS` so Harbor doesn't re-run it.
3. After each Harbor job completes, append/update entries in the ledger (one entry per task with submitted_at, completed_at, trajectory_path, reward, note) and add any newly-done IDs to `HARBOR_EXCLUDE_TASKS` before the next start.

## Repos

- `repos/harbor/`: Trajectory generation runtime (managed local-only dependency, not committed to Live repo)

## How To Run

- `scripts/update_repos.sh`: clone or update repos/harbor. Note: tests `[[ -d "$HARBOR_DIR/.git" ]]` — if Harbor is checked out as a git submodule (gitlink file, not dir), this errors. The pinned commit may already be correct; skip this script when the dryrun confirms `current commit matches config.yaml pin`.
- `scripts/prepare_tasks.sh`: copy task dirs into `artifacts/tasks/<dataset>/`. Filters by `<source>/verifiable_tasks.txt` when present (see Task Consumption Contract above). Idempotent: skips if target already contains valid Harbor task dirs; pass `--overwrite` to rebuild.
- `scripts/dryrun.sh`: validate config, Harbor repo state, environments, and task directories
- `scripts/start.sh`: run dryrun preflight → generate LiteLLM config → start proxy on `runtime_info.input.litellm_proxy.port` → run Harbor job with `--exclude-task-name` flags built from `HARBOR_EXCLUDE_TASKS`. Set `TRAJGEN_PREPARE_TASKS=1` to re-run `prepare_tasks.sh` first.
- `scripts/clean.sh`: remove gitignored runtime outputs

Trajgen scripts need PyYAML in the runtime Python (used by inline `python3 -` config readers). If you see `ERROR: PyYAML is required`, `pip install pyyaml` into the active interpreter.

## Repository Policy

Harbor is a managed local dependency. Do not edit Harbor source inside this Live block. Use `scripts/update_repos.sh` to clone/fetch/checkout the configured ref. The script refuses to update if the Harbor worktree has local modifications.

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing metadata.yaml, config snapshot, scripts copy, session.log, and monitor.md. Append entry to `artifacts/index.yaml`.

## Memory

Long-form notes, repo policy, and operational decisions are kept in `dashboard/memory/`.

## Remote Execution

This block runs on the node declared in `config.yaml` → `meta_info.resources.ip` (currently `local`).

- If the configured value is `local`: run scripts directly on the current host in a named tmux session (`tmux new-session -d -s trajgen …`).
- If the configured value is a remote host/IP: SSH into that host and operate inside a tmux session there — never invoke this block's scripts from a different node.

Either way, all execution must happen on the configured resource target, in a named tmux session (e.g. `trajgen`), so the run survives shell disconnects.
