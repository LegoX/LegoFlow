# trajgen

The trajectory-generation block of [SWE-Lego-Live](../../README.md). It runs a
coding agent (via [Harbor](https://github.com/SWE-Lego/harbor)) on verified SWE
tasks, captures each rollout as a raw trajectory, and optionally converts those
trajectories into SFT training data for the downstream `sft` block.

## Role in the pipeline

```
swegen ─ verifiable_tasks.txt ─► trajgen ─ trajectories / SFT data ─► sft ─► rl
```

trajgen sits between task generation and training. It **only** consumes tasks
that swegen has marked verified in `verifiable_tasks.txt`, and tracks which
tasks it has already processed so no task is run twice.

## What it does

- Prepares Harbor task directories from swegen's verified tasks (manifest-filtered).
- Starts a per-job **LiteLLM proxy** in front of the configured upstream model API.
- Runs a **Harbor job** that rolls the agent out across tasks, writing per-task
  logs to `artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`.
- Optionally converts a finished job into **LLaMA-Factory SFT data**
  (`artifacts/sft_data/<job>/lf.json`).
- Publishes a live **progress dashboard** (local HTML, optionally synced to Cloudflare Pages).

## Block layout

```
subblock/trajgen/
├── README.md            # this file — project intro (humans)
├── CLAUDE.md            # agent contract — how the agent operates the block
├── config.yaml          # identity, repos, inputs/outputs, status (one-shot per run)
├── docs/                # detailed human docs and records
│   ├── index.mdx        #   overview + quickstart
│   ├── status.mdx       #   where live status lives
│   ├── reference/io.mdx #   full inputs/outputs reference
│   └── memory.mdx       #   notes summary
├── dashboard/           # progress board: progress_monitor.py + Cloudflare sync + site/
├── memory/notes.md      # long-form notes, repo policy, decisions
├── scripts/             # update_repos, setup env, prepare_tasks, dryrun, start, convert, clean
├── repos/               # managed local-only deps (gitignored): harbor, swe_data_process
├── artifacts/           # tasks/, jobs/, sft_data/, env/, index.yaml, consumption_ledger.yaml
└── .claude/             # block-local plugin: /trajgen:* operating skills
```

## Inputs / Outputs at a glance

Everything is declared in [`config.yaml`](config.yaml); see
[`docs/reference/io.mdx`](docs/reference/io.mdx) for the full reference.

| | Key items |
|---|---|
| **Inputs** | `llm_api` (api_key, api_base_url, model); `litellm_proxy`; `task_source` (wired from swegen); `harbor_job`; `agent`; `sft_conversion`; `HARBOR_EXCLUDE_TASKS` |
| **Outputs** | `raw_trajectories_dir` → `artifacts/jobs/` (consumed by `sft`); `sft_data_dir` → `artifacts/sft_data/` (LF format, consumed by `sft`) |

The only external value you normally fill is `runtime_info.input.llm_api`; the
task source is wired from swegen via `meta_info.dependencies`.

## Quick start

Run from the repo root with the `block` plugin loaded, or from this directory.

```text
/block:check trajgen     # preflight: config, repos, envs, tasks, LLM endpoint
/trajgen:setup           # clone/update repos, build uv envs, copy verified tasks
/block:run trajgen       # execute scripts/start.sh (proxy + Harbor) and archive
/trajgen:dashboard       # view progress locally or sync online
```

After a job, do the trajgen-specific bookkeeping (ledger + `HARBOR_EXCLUDE_TASKS`
+ status) described in `/trajgen:run-job`, and optionally `/trajgen:convert-sft`
to produce SFT data.

## Operating skills (`.claude/`)

This block ships a local plugin under [`.claude/`](.claude/) with the detailed
procedures, so `CLAUDE.md` stays short. The repo-wide `block` plugin handles the
generic lifecycle (`/block:check`, `/block:run`); these are trajgen-specific:

| Command | What it does |
|---|---|
| `/trajgen:setup` | Clone/update `harbor` + `swe_data_process`, build uv envs, copy manifest-filtered tasks |
| `/trajgen:run-job` | LiteLLM proxy + Harbor job + post-run ledger / exclude-list / status bookkeeping |
| `/trajgen:convert-sft` | Convert one job's trajectories into `im.jsonl` + `lf.json` |
| `/trajgen:dashboard` | Local HTML board or Cloudflare Pages online sync |

## Where things live

- **Run it**: `scripts/` (or the skills above). `scripts/start.sh` runs the job; `scripts/dryrun.sh` validates without side effects.
- **Read status**: `config.yaml` → `status` and `artifacts/index.yaml` (newest entry). For a visual view, the dashboard at <https://swe-trajgen-databoard.pages.dev>.
- **Outputs**: trajectories under `artifacts/jobs/`, SFT data under `artifacts/sft_data/`.
- **History**: per-run snapshots under `artifacts/archives/run_NNN/`.

## Links

- [`docs/index.mdx`](docs/index.mdx) — detailed overview and step-by-step quickstart
- [`CLAUDE.md`](CLAUDE.md) — agent contract
- [`docs/reference/dashboard.mdx`](docs/reference/dashboard.mdx) — progress board and Cloudflare sync reference
- [root `README.md`](../../README.md) and [`BLOCK_DEFINITION.md`](../../BLOCK_DEFINITION.md) — the block system
