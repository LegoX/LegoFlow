# tracer

Generates raw agent trajectories with Harbor for downstream SFT data conversion.

This repo is organized as a tree of blocks. The root directory is the root block; every directory under `blocks/` is a child block. Each block is operated by a dedicated agent that reads its own `CLAUDE.md`, and follows the principles in `<repo_root>/.claude/plugins/root-plugin/resources/BLOCK_DEFINITION.md` (resolve from the repo root, not from this block's directory). Every agent with this repo SHOULD READ that file before any actions.

## Block Identity

- **Name**: tracer
- **Role**: Data generation - creates agent trajectories from Harbor SWE or terminal tasks
- **Parent**: legoflow
- **Children**: none (leaf block)

## Read first

1. `config.yaml` — block identity, Harbor config, and runtime values (one-shot per run; live state is `artifacts/index.yaml`)
2. `docs/content/docs/index.mdx` — human-facing overview and quickstart (`README.md` is the short project intro; `docs/` is the user-facing fumadocs site)

## Input / Output contract

Read the effective config (`TRAJGEN_CONFIG`, or this block's `config.yaml`) before running (details in `docs/content/docs/usage/configuration.mdx`):
- `meta_info.repositories.{harbor,legoflow_trace_crafter}` — Git url/branch/commit/path/readonly for the two managed local-only repos
- `meta_info.environment` — `harbor_uv`, `litellm_uv`, `legoflow_trace_crafter_uv`, `legoflow_trace_crafter_extras`
- `runtime_info.input.llm_api` — upstream API used to build the per-job LiteLLM proxy
- `runtime_info.input.litellm_proxy` — proxy config template, port, master key
- `runtime_info.input.task_source` — `provider: local` selects `local_block: curator` by default, `terminal-curator`, or `external`. SWE/external sources use `dataset_name`; terminal sources use `terminal_tasks_dir` and require a nonempty `verifiable_tasks.txt`. `huggingface` uses `dataset_name` without a local producer. The two producer edges have independent conditions and mirrored `dependencies.to` declarations.
- `runtime_info.input.harbor_job` — jobs_dir, concurrency, retries, timeout multiplier
- `runtime_info.input.agent` — agent name, version, runtime image, max turns, temperature
- `runtime_info.input.sft_conversion` — optional post-Harbor conversion (`enabled`, `scaffold`, `out_dir`, …)
- `runtime_info.input.env_extra.HARBOR_EXCLUDE_TASKS` — exclusion sources Harbor must skip: `excluded_tasks.txt` (human decisions, git-tracked) + `artifacts/processed_tasks.yaml` (run history); literal task IDs also accepted

Write to `config.yaml` → `runtime_info.output` after running:
- `raw_trajectories_dir` — `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl` (consumed by `trainer`)
- `sft_data_dir` — `artifacts/sft_data/<job>/lf.json` (LLaMA-Factory LF; consumed by `trainer`)

## Task consumption contract

Terminal tasks must complete the producer skill's instruction/tests/reference-solution semantic audit, followed by fresh Oracle/NOP validation, before downstream rollout. Oracle/NOP alone establishes runnable behavior; the Tracer manifest gate does not certify that semantic audit.

1. `prepare_tasks.sh` filters a source through `verifiable_tasks.txt` when present, and requires that manifest for terminal-curator. Other local/HF sources retain the fallback to valid Harbor child directories. It atomically points `artifacts/tasks/<dataset>/` (or `harbor_job.dataset_path`) at an owned version under `artifacts/tasks/.legoflow-staging-v1/`. Local versions contain one link per selected task; HF versions contain materialized copies. Source, manifest, settings, and file metadata determine reuse. Existing real directories and unrelated symlinks are refused even with `--overwrite`; choose a fresh dataset_path or move old staging yourself after checking active jobs.
2. `start.sh` uses the fixed version returned by its prepare invocation, so another prepare cannot change its selected batch through the public pointer. Terminal publication links resolve to immutable producer batches. Mutable SWE/external task contents remain linked and may change; staging is not a byte snapshot for those sources. Version caches remain until explicit cleanup; do not remove one used by active jobs.
3. `artifacts/processed_tasks.yaml` records processed task IDs (`pending | running | done | failed | skipped`). The `/tracer:run` agent updates this ledger after inspecting actual results; the shell launcher does not automatically record outcomes.
4. `start.sh` resolves `HARBOR_EXCLUDE_TASKS` through that ledger: terminal `done | failed | skipped` entries and retired IDs are excluded next time. This bookkeeping is required to avoid accidental repeats; it is not an exactly-once runtime guarantee. IDs are shared across sources, so use distinct task IDs or a deliberately separate ledger when comparing datasets. Retire a task permanently in git-tracked `excluded_tasks.txt`.

Operating detail for the run + post-run bookkeeping is in the `/tracer:run` skill.

## Repos

- `repos/harbor/` — trajectory generation runtime (managed local-only dependency, gitignored, read-only after checkout)
- `repos/LegoFlow-Trace-Crafter/` — LegoFlow-Trace-Crafter checkout: trajectory → IM → LF SFT converter (managed local-only; uv env lives outside the repo at `artifacts/env/legoflow-trace-crafter-uv`)

Do not edit repo sources here. Use `scripts/update_repos.sh` to clone/fetch/checkout the pinned ref; it refuses to update a worktree with local modifications.

## How to run

Generic lifecycle via the repo-wide `root` plugin: `/root:check tracer` to preflight, `/root:run tracer` to execute `scripts/start.sh` and archive. Tracer-specific procedures live in this block's `.claude/` plugin:

| Skill | Wraps | Purpose |
|---|---|---|
| `/tracer:setup` | `update_repos.sh`, `setup_harbor_env.sh`, `setup_legoflow_trace_crafter_env.sh`, `dryrun.sh` | Clone/update repos, build uv envs |
| `/tracer:check` | `dryrun.sh` | Read-only preflight |
| `/tracer:dashboard` | `dashboard/progress_monitor.py`, `dashboard/run_cloudflare_pages_sync.sh`, `convert_trajectories.sh` | Interactive HTML board (Analyze jobs, quality/pass slices, and trajectories) / Cloudflare online sync / SFT stats refresh. Sources are fixed under `artifacts/` (`tasks/`, `jobs/`, optional `sft_data/`, `index.yaml`) — never configured in `config.yaml`; the skill shows them and waits for confirmation before rendering |
| `/tracer:run` | `dryrun.sh`, `start.sh` | Full pipeline: prepare_tasks → Harbor → optional convert + post-run bookkeeping |

`scripts/clean.sh` (no flags) removes only a run's temporary output — LiteLLM state, logs, launch logs, `dashboard/site/`, `dashboard/.cache/`. It keeps the uv env, `jobs/`, `tasks/`, `sft_data/`, `agent-runtime/` and `processed_tasks.yaml`. `--all` wipes `artifacts/` entirely except git-tracked files and confirms twice. Tracer scripts need PyYAML in the runtime Python; on `ERROR: PyYAML is required`, `pip install pyyaml`.

## Artifact archiving

Each run is archived by `scripts/archive_run.sh`, invoked from `scripts/start.sh`'s EXIT trap: it writes `artifacts/archives/run_NNN/` (metadata.yaml, config snapshot, scripts snapshot, repo SHAs) and appends one entry to `artifacts/index.yaml`. The agent may add `session.log` / `monitor.md`. `config.yaml` is one-shot per run — live state is `artifacts/index.yaml`.

## Memory

Long-form notes, repo policy, and decisions live in `memory/notes.md`.

## Remote execution

This block runs on the host named by `config.yaml` → `meta_info.resources.ip` (currently `local` → run on this host). Always operate inside a named tmux session (e.g. `tracer`) so runs survive disconnects. If `ip` is ever set to a real remote IP, SSH into that node and run inside a tmux session there — never invoke this block's scripts from a different node.
