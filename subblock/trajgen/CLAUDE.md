# trajgen

Generates raw agent trajectories with Harbor for downstream SFT data conversion.

**Parent:** swe_lego_live  
**Children:** none (leaf block)

## Read first

1. `config.yaml` — block identity, Harbor config, runtime values, and live `status` (source of truth alongside `artifacts/index.yaml`)
2. `docs/index.mdx` — human-facing overview and quickstart (`README.md` is the short project intro; `docs/` holds the detailed docs)

## Input / Output contract

Read from `config.yaml` before running (details in `docs/reference/io.mdx`):
- `meta_info.repositories.{harbor,swe_data_process}` — Git url/branch/commit/path/readonly for the two managed local-only repos
- `meta_info.environment` — `harbor_uv`, `litellm_uv`, `swe_data_process_uv`, `swe_data_process_extras`
- `runtime_info.input.llm_api` — upstream API used to build the per-job LiteLLM proxy
- `runtime_info.input.litellm_proxy` — proxy config template, port, master key
- `runtime_info.input.task_source` — SWE task source (wired from swegen via `meta_info.dependencies.task_source_dir`)
- `runtime_info.input.harbor_job` — jobs_dir, concurrency, retries, timeout multiplier
- `runtime_info.input.agent` — agent name, version, runtime image, max turns, temperature
- `runtime_info.input.sft_conversion` — optional post-Harbor conversion (`enabled`, `scaffold`, `out_dir`, …)
- `environment.extra.HARBOR_EXCLUDE_TASKS` — space-separated task IDs Harbor must skip

Write to `config.yaml` → `runtime_info.output` after running:
- `raw_trajectories_dir` — `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl` (consumed by `sft`)
- `sft_data_dir` — `artifacts/sft_data/<job>/lf.json` (LLaMA-Factory LF; consumed by `sft`)

## Task consumption contract

Trajgen **only** runs tasks listed in swegen's `verifiable_tasks.txt`, and never re-runs one it already processed:
1. `prepare_tasks.sh` copies only manifest-listed task IDs into `artifacts/tasks/<dataset>/`.
2. `artifacts/consumption_ledger.yaml` is the source of truth for processed task IDs (`pending | running | done | failed | skipped`).
3. Every `done`/`failed`/`skipped` task must also appear in `HARBOR_EXCLUDE_TASKS` so Harbor skips it next time.

Operating detail for the run + post-run bookkeeping is in the `/trajgen:run-job` skill.

## Repos

- `repos/harbor/` — trajectory generation runtime (managed local-only dependency, gitignored, read-only after checkout)
- `repos/swe_data_process/` — trajectory → IM → LF SFT converter (managed local-only; uv env lives outside the repo at `artifacts/env/swe-data-process-uv`)

Do not edit repo sources here. Use `scripts/update_repos.sh` to clone/fetch/checkout the pinned ref; it refuses to update a worktree with local modifications.

## How to run

Generic lifecycle via the repo-wide `block` plugin: `/block:check trajgen` to preflight, `/block:run trajgen` to execute `scripts/start.sh` and archive. Trajgen-specific procedures live in this block's `.claude/` plugin:

| Skill | Wraps | Purpose |
|---|---|---|
| `/trajgen:setup` | `update_repos.sh`, `setup_swe_data_process_env.sh`, `prepare_tasks.sh`, `dryrun.sh` | Clone/update repos, build uv envs, copy manifest-filtered tasks |
| `/trajgen:run-job` | `dryrun.sh`, `start.sh` | LiteLLM proxy + Harbor job + post-run ledger / `HARBOR_EXCLUDE_TASKS` / status |
| `/trajgen:convert-sft` | `convert_trajectories.sh` | One job's trajectories → `im.jsonl` + `lf.json` |
| `/trajgen:dashboard` | `dashboard/progress_monitor.py`, `dashboard/run_cloudflare_pages_sync.sh` | Local HTML board / Cloudflare online sync |

`scripts/clean.sh` removes gitignored runtime outputs (`artifacts/sft_data`, `dashboard/site/`, `dashboard/.cache/`). Trajgen scripts need PyYAML in the runtime Python; on `ERROR: PyYAML is required`, `pip install pyyaml`.

## Artifact archiving

Each run is archived by `scripts/archive_run.sh`, invoked from `scripts/start.sh`'s EXIT trap: it writes `artifacts/archives/run_NNN/` (metadata.yaml, config snapshot, scripts snapshot, repo SHAs) and appends one entry to `artifacts/index.yaml`. The agent may add `session.log` / `monitor.md`. `config.yaml` is one-shot per run — live state is `artifacts/index.yaml`.

## Memory

Long-form notes, repo policy, and decisions live in `memory/notes.md` (human-readable summary mirrored at `docs/memory.mdx`).

## Remote execution

This block runs on the host named by `config.yaml` → `meta_info.resources.ip` (currently `local` → run on this host). Always operate inside a named tmux session (e.g. `trajgen`) so runs survive disconnects. If `ip` is ever set to a real remote IP, SSH into that node and run inside a tmux session there — never invoke this block's scripts from a different node.
