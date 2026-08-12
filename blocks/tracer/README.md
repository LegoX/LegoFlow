# tracer

The trajectory-generation block of [LegoFlow](../../README.md). It runs a
coding agent (via [Harbor](https://github.com/SWE-Lego/harbor)) on verified SWE
tasks, captures each rollout as a raw trajectory, and optionally converts those
trajectories into SFT training data for the downstream `trainer` block.

## Role in the pipeline

```
curator ─ verifiable_tasks.txt ─► tracer ─ trajectories / SFT data ─► trainer ─► rl
```

tracer sits between task generation and training. It **only** consumes tasks
that curator has marked verified in `verifiable_tasks.txt`, and tracks which
tasks it has already processed so no task is run twice.

## What it does

- Prepares Harbor task directories from curator's verified tasks (manifest-filtered).
- Starts a per-job **LiteLLM proxy** in front of the configured upstream model API.
- Runs a **Harbor job** that rolls the agent out across tasks, writing per-task
  logs to `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`.
- Optionally converts a finished job into **LLaMA-Factory SFT data**
  (`artifacts/sft_data/<job>/lf.json`).
- Publishes a live **progress dashboard** (local HTML, optionally synced to Cloudflare Pages).

## Block layout

```
blocks/tracer/
├── README.md            # this file — project intro (humans)
├── CLAUDE.md            # agent contract — how the agent operates the block
├── config.yaml          # identity, repos, inputs/outputs, status (one-shot per run)
├── docs/                # fumadocs site (user-facing docs) → Cloudflare Pages
│   ├── content/docs/    #   MDX: motivation, getting-started, core-concepts, run-jobs, sft-data, dashboard, reference
│   ├── src/             #   minimal Next.js + fumadocs app shell
│   └── deploy_cloudflare_pages.sh  # build (static export) + deploy to legoflow-tracer-docs
├── dashboard/           # progress board: progress_monitor.py + Cloudflare sync + site/
├── memory/notes.md      # long-form notes, repo policy, decisions
├── scripts/             # update_repos, setup env, prepare_tasks, dryrun, start, convert, clean
├── repos/               # managed local-only deps (gitignored): harbor, swe_data_process
├── artifacts/           # tasks/, jobs/, sft_data/, env/, index.yaml, processed_tasks.yaml
└── .claude/             # block-local plugin: /tracer:* operating skills
```

## Inputs / Outputs at a glance

Everything is declared in [`config.yaml`](config.yaml); see
[`docs/content/docs/reference/io.mdx`](docs/content/docs/reference/io.mdx) for the full reference.

| | Key items |
|---|---|
| **Inputs** | `llm_api` (api_key, api_base_url, model); `litellm_proxy`; `task_source` (wired from curator); `harbor_job`; `agent`; `sft_conversion`; `HARBOR_EXCLUDE_TASKS` (names `artifacts/processed_tasks.yaml`) |
| **Outputs** | `raw_trajectories_dir` → `artifacts/jobs/` (consumed by `trainer`); `sft_data_dir` → `artifacts/sft_data/` (LF format, consumed by `trainer`) |

The only external value you normally fill is `runtime_info.input.llm_api`; the
task source is wired from curator via `meta_info.dependencies`.

## Quick start

Run from the repo root with the `block` plugin loaded, or from this directory.

```text
/root:check tracer     # preflight: config, repos, envs, tasks, LLM endpoint
/tracer:setup           # clone/update repos, build uv envs, run dryrun
/root:run tracer       # execute scripts/start.sh (proxy + Harbor) and archive
/tracer:dashboard       # view overview/instances/trajectories/operations locally or sync online
```

After a job, do the tracer-specific bookkeeping (ledger + `HARBOR_EXCLUDE_TASKS`
+ status) described in `/tracer:run`, and use `/tracer:dashboard` to refresh
SFT data/stats when needed.

## Operating skills (`.claude/`)

This block ships a local plugin under [`.claude/`](.claude/) with the detailed
procedures, so `CLAUDE.md` stays short. The repo-wide `block` plugin handles the
generic lifecycle (`/root:check`, `/root:run`); these are tracer-specific:

| Command | What it does |
|---|---|
| `/tracer:setup` | Clone/update `harbor` + `swe_data_process`, build uv envs, initialise ledger if needed, run dryrun |
| `/tracer:check` | Read-only preflight |
| `/tracer:run` | LiteLLM proxy + Harbor job + post-run ledger / exclude-list / status bookkeeping |
| `/tracer:dashboard` | Interactive HTML board (Overview, Instances, Trajectories, Operations), Cloudflare Pages online sync, optional R2 full trajectory loading, or SFT stats refresh |

## Where things live

- **Run it**: `scripts/` (or the skills above). `scripts/start.sh` runs the job; `scripts/dryrun.sh` validates without side effects.
- **Read status and quality**: `config.yaml` → `status` and `artifacts/index.yaml` (newest entry). For a visual view, the dashboard published to the `legoflow-tracer` Pages project includes instance/trajectory analysis by programming language/domain/category/difficulty/source/model/scaffold/job, status, artifact tables, bounded previews, and optional R2 full trajectory loading.
- **Outputs**: trajectories under `artifacts/jobs/`, SFT data under `artifacts/sft_data/`.
- **History**: per-run snapshots under `artifacts/archives/run_NNN/`.

## Links

- **Docs site**: source under [`docs/content/docs/`](docs/content/docs/); fumadocs site published to the `legoflow-tracer-docs` Cloudflare Pages project by [`docs/deploy_cloudflare_pages.sh`](docs/deploy_cloudflare_pages.sh)
- [`docs/content/docs/getting-started.mdx`](docs/content/docs/getting-started.mdx) — step-by-step setup and first job
- [`docs/content/docs/dashboard.mdx`](docs/content/docs/dashboard.mdx) — progress board and Cloudflare sync reference
- [`docs/content/docs/reference/config-variants.mdx`](docs/content/docs/reference/config-variants.mdx) — config variants + how to build/deploy the docs site
- [`CLAUDE.md`](CLAUDE.md) — agent contract
- [root `README.md`](../../README.md) and [`BLOCK_DEFINITION.md`](../../BLOCK_DEFINITION.md) — the block system
