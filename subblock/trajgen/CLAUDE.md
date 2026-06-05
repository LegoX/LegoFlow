# trajgen

Generates raw agent trajectories with Harbor for downstream SFT data conversion.

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `subblock/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `<repo_root>/.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` (resolve from the repo root, not from this block's directory). Every agent with this repo SHOULD READ that file before any actions.

## Block Identity

- **Name**: trajgen
- **Role**: Data generation - creates agent trajectories from SWE tasks
- **Parent**: swe_lego_live
- **Children**: none (leaf block)

## What To Read First

1. `config.yaml` — block identity, Harbor config, runtime values (one-shot per run; live state in `artifacts/index.yaml`)
2. `dashboard/overview.mdx` — current state narrative

## Input/Output Contract

**Inputs** (read from `config.yaml`):
- `meta_info.repositories.harbor`: Harbor Git URL, branch, commit, path, and read-only policy
- `meta_info.repositories.swe_data_process`: swe_data_process Git URL, branch, commit, path, and read-only policy (managed local-only dependency, mirrors Harbor)
- `meta_info.environment`: Harbor uv environment path, LiteLLM venv path, and swe_data_process uv env (`swe_data_process_uv`, `swe_data_process_extras`)
- `runtime_info.input.llm_api`: raw upstream API config (model, api_base_url, api_key, optional token costs) — used to build the per-job LiteLLM proxy config
- `runtime_info.input.litellm_proxy`: LiteLLM config template, port, and master key
- `runtime_info.input.task_source`: SWE task source (provider, dataset_name pointing at swegen's `swe_tasks/`, split)
- `runtime_info.input.harbor_job`: jobs directory, concurrency, retries, timeout multiplier
- `runtime_info.input.agent`: agent name, version, runtime image, max turns, temperature
- `runtime_info.input.sft_conversion`: optional post-Harbor conversion step (`enabled`, `scaffold` = auto|claude_code|open_code|openhands_sdk|terminus2, `out_dir`, `max_instances`, `exclude_repos_file`) — when `enabled: true`, `start.sh` runs `scripts/convert_trajectories.sh` after Harbor finishes
- `environment.extra.HARBOR_EXCLUDE_TASKS`: space-separated list of task IDs Harbor must skip (prior timeouts/OOMs + tasks already consumed per the ledger)

**Outputs** (written to `config.yaml` → `runtime_info.output`):
- `raw_trajectories_dir`: Harbor job directories with LiteLLM trajectory logs at `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl` (consumed by `sft` and downstream conversion)
- `sft_data_dir`: LLaMA-Factory LF-format SFT data converted from raw trajectories at `artifacts/sft_data/<job>/lf.json` (consumed by `sft`)

## Task Consumption Contract

Trajgen **only** runs trajectories for tasks listed in swegen's `verifiable_tasks.txt`:

1. `scripts/prepare_tasks.sh` detects `<source>/verifiable_tasks.txt` and copies only listed task IDs into `artifacts/tasks/<dataset>/` (manifest-filtered copy via `copy_harbor_tasks_filtered`). If the manifest is missing it falls back to copying every task dir — keep one in the source.
2. `artifacts/consumption_ledger.yaml` is the source of truth for which task IDs have already been processed. Status values: `pending | running | done | failed | skipped`. Every task with status `done`, `failed` (excluded), or `skipped` must also appear in `HARBOR_EXCLUDE_TASKS` so Harbor doesn't re-run it.
3. After each Harbor job completes, append/update entries in the ledger (one entry per task with submitted_at, completed_at, trajectory_path, reward, note) and add any newly-done IDs to `HARBOR_EXCLUDE_TASKS` before the next start.

## Repos

- `repos/harbor/`: Trajectory generation runtime (managed local-only dependency, not committed to Live repo)
- `repos/swe_data_process/`: Trajectory → IM → LF SFT-data converter (managed local-only dependency; uv-managed env lives outside the read-only repo at `artifacts/env/swe-data-process-uv`)

## How To Run

- `scripts/update_repos.sh`: clone or update every entry under `meta_info.repositories`. Defaults to all; narrow with `--repo harbor` or `--repo swe_data_process`. Pass `--ref X` together with `--repo <name>` to override the configured branch/ref for that one repo. The script refuses to update a repo whose worktree has local modifications.
- `scripts/setup_swe_data_process_env.sh`: create or refresh the uv project environment for `repos/swe_data_process` at `artifacts/env/swe-data-process-uv`. Reads `meta_info.environment.swe_data_process_extras` and runs `UV_PROJECT_ENVIRONMENT=… uv sync --extra <each>` from inside the repo so it works while the repo is read-only.
- `scripts/prepare_tasks.sh`: copy task dirs into `artifacts/tasks/<dataset>/`. Filters by `<source>/verifiable_tasks.txt` when present (see Task Consumption Contract above). Idempotent: skips if target already contains valid Harbor task dirs; pass `--overwrite` to rebuild.
- `scripts/dryrun.sh`: validate config, both managed repos' state, both uv environments (`harbor_uv`, `swe_data_process_uv`), LiteLLM env, task directories, and the `sft_conversion` block.
- `scripts/start.sh`: run dryrun preflight → generate LiteLLM config → start proxy on `runtime_info.input.litellm_proxy.port` → run Harbor job with `--exclude-task-name` flags built from `HARBOR_EXCLUDE_TASKS`. If `runtime_info.input.sft_conversion.enabled: true`, runs `scripts/convert_trajectories.sh --job "$JOB_NAME"` after Harbor exits. Set `TRAJGEN_PREPARE_TASKS=1` to re-run `prepare_tasks.sh` first.
- `scripts/convert_trajectories.sh`: convert one Harbor job's trajectories into `<out_dir>/<job>/im.jsonl` (intermediate) and `<out_dir>/<job>/lf.json` (LLaMA-Factory ShareGPT). Defaults read from `runtime_info.input.sft_conversion`; flags `--job <name|latest>`, `--scaffold <auto|claude_code|open_code|openhands_sdk|terminus2>`, `--out-dir`, `--max-instances`, `--exclude-repos-file` override per-run. Scaffold `auto` derives from `runtime_info.input.agent.name` (custom-claude-code → claude_code, etc.). `--skip-unchanged` exits early without reconverting when the job's resolved (reward=1.0) instance set and conversion inputs are unchanged since the last run (tracked via `<out_dir>/<job>/.convert_sig.json`); the dashboard's Cloudflare sync loop uses this to refresh SFT stats periodically.
- `scripts/clean.sh`: remove gitignored runtime outputs, including `artifacts/sft_data`, `dashboard/site/`, and `dashboard/memory/.progress_monitor_cache.json`.
- `dashboard/progress_monitor.py`: local-only HTML dashboard that scans `artifacts/jobs/` (only jobs with `result.json`) and `artifacts/sft_data/` (per-job `lf.stats.json`). The script carries a PEP 723 inline header and a `#!/usr/bin/env -S uv run --no-project --quiet --script` shebang, so the Python runtime is supplied by `uv` (stdlib-only, no env to maintain). Run `./dashboard/progress_monitor.py` for a one-shot generation, or `./dashboard/progress_monitor.py --loop 60 --serve --open` for a self-refreshing local preview at `http://127.0.0.1:8765/index.html`. Equivalent: `uv run --no-project --script dashboard/progress_monitor.py [args...]`. See `dashboard/README.md` for full flag list. For remote viewing, `dashboard/run_cloudflare_pages_sync.sh` loop-generates and deploys `dashboard/site/` to Cloudflare Pages via `wrangler` (config read from `~/.config/trajgen_progress_cloudflare.env`).

Trajgen scripts need PyYAML in the runtime Python (used by inline `python3 -` config readers). If you see `ERROR: PyYAML is required`, `pip install pyyaml` into the active interpreter.

## Repository Policy

Both `repos/harbor/` and `repos/swe_data_process/` are managed local-only dependencies. Do not edit their sources inside this Live block. Use `scripts/update_repos.sh` to clone/fetch/checkout the configured ref. The script refuses to update a repo whose worktree has local modifications, and each repo is set read-only after checkout when `repositories.<name>.readonly: true`. uv environments for read-only repos must live outside their checkout directory (see `meta_info.environment.harbor_uv` and `meta_info.environment.swe_data_process_uv`).

## Artifact Archiving

After each run, create `artifacts/archives/run_NNN/` containing metadata.yaml, config snapshot, scripts copy, session.log, and monitor.md. Append entry to `artifacts/index.yaml`.

## Memory

Long-form notes, repo policy, and operational decisions are kept in `dashboard/memory/`.

## Remote Execution

This block runs on the node declared in `config.yaml` → `meta_info.resources.ip` (currently `192.168.35.240`).

- If your shell is on a **different** host: SSH into `192.168.35.240` and operate inside a tmux session there — never invoke this block's scripts from a different node.
- If your shell is **already on** `192.168.35.240`: skip the SSH step and run scripts directly in a local tmux session (`tmux new-session -d -s trajgen …`). The remote-execution rule is satisfied by being on the named host; SSH would be a self-loop.

Either way, all execution must happen on the configured IP, in a named tmux session (e.g. `trajgen`), so the run survives shell disconnects.
