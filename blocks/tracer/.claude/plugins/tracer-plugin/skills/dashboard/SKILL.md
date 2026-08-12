---
name: dashboard
description: >
  Drive the tracer progress dashboard. Sources are fixed, not configured: the
  board always reads artifacts/tasks/ (one batch per subdirectory),
  artifacts/jobs/ (one Harbor job per subdirectory), the optional
  artifacts/sft_data/, and artifacts/index.yaml. Always presents the resolved
  paths and discovered batches and waits for explicit confirmation before
  rendering. Generate or serve the local self-contained HTML board
  (dashboard/progress_monitor.py), or run / restart the Cloudflare Pages sync
  loop (dashboard/run_cloudflare_pages_sync.sh, tmux session tracer-cf) that
  publishes it online. Also covers manually refreshing one job's SFT data/stats
  with scripts/convert_trajectories.sh. Use when asked to "show tracer
  progress", "open the dashboard", "start/restart the cloudflare sync",
  "convert trajectories to SFT data", "make the LF dataset", "refresh SFT
  stats", or "why does dashboard/.cache keep coming back". Triggers on
  "/tracer:dashboard".
---

# /tracer:dashboard

Generate, preview, or publish the tracer progress board. Run from the block
root `blocks/tracer/`. The generator is stdlib-only via `uv run` (PEP 723
inline metadata + uv-run shebang) — no env to maintain; `uv` must be on `PATH`.
Full flag reference: `docs/content/docs/dashboard.mdx`.

## Step 1 - The sources are fixed

There is nothing to configure. `config.yaml` does not name what the board reads,
and must never be edited to change it. The board always reads:

| What | Where |
| --- | --- |
| Task batches | `artifacts/tasks/` — **each immediate subdirectory is one batch** |
| Harbor jobs | `artifacts/jobs/` — **each immediate subdirectory is one job** |
| SFT data | `artifacts/sft_data/` — each immediate subdirectory is one converted dataset. **Optional** |
| Run state | `artifacts/index.yaml` — newest archived run |

**A batch's name on the board is its directory name.** There is no place to
rename it. To change what the board shows, change what is under `artifacts/`.

Staged task batches are **directories of symlinks** pointing back at the source
pool (`scripts/prepare_tasks.sh` links rather than copies), so a batch's contents
can live anywhere on disk. The report prints the link destination — show it, do
not assume a batch is local just because it is listed.

### SFT data is optional

`artifacts/sft_data/` is the only optional source. Without it the board drops
every trajectory-score surface — the score distribution, the score matrix, and
the scoring rubric — rather than rendering them empty. That absence is correct
and is **not** a defect to report or work around: it means nothing has been
scored, not that trajectories scored zero. Jobs, trials, pass rate and task
coverage all still render. Never run `convert_trajectories.sh` just to populate
those surfaces; conversion is a separate action the user must ask for.

## Step 2 - Show the sources and confirm

Scan read-only and print the report, then **wait for an explicit "yes"**:

```bash
./dashboard/progress_monitor.py --report-only
```

It writes nothing, parses no trajectories, and prints the resolved absolute
paths plus what was found under each:

```text
tracer dashboard sources
  task batches           <abs path>
    <batch name>           <N> task(s)
  harbor jobs            <abs path>
    <job name>             <N> trial dir(s)
  sft data               <abs path>
    <dataset name>         lf.json, lf.stats.json, im.jsonl
  run index              <abs path>
```

Present this to the user verbatim and ask whether to proceed. Never render
without an explicit "yes". Raise anything the report flags rather than folding it
into the totals:

| What the report says | What to tell the user |
| --- | --- |
| `none staged` under task batches | nothing has been staged yet; `prepare_tasks.sh` links tasks in at launch |
| `N non-task dir(s) ignored` | name them — a batch is meant to hold only harbor tasks |
| `no result.json` on a job | that job is still running or was aborted; its trial counts are partial |
| `no converted files` on an SFT dataset | conversion produced nothing for that job |
| `-> <path>` under a batch | the batch is a symlink; that path is where the tasks really live |

## Step 3 - Render

The generated page has `Overview`, `Instances`, `Trajectories`, and
`Operations` sidebar sections. `Operations` reads `artifacts/index.yaml` and
exposes searchable Harbor/SFT tables; `Instances` slices quality score and
pass-rate metrics by programming language/domain/category/difficulty/source/
model/scaffold/job; `Trajectories` shows concrete trial/SFT cards with bounded
previews and optional `/api/traj` full loading from R2.

Each generation also writes analysis exports under `dashboard/site/data/`:
`summary.json`, `task_dim.json`, `trial_fact.jsonl`, `quality_fact.jsonl`,
`segments.json`, `instances.jsonl`, `traj_cards.jsonl`, and
`error_summary.json`.

This skill also owns SFT stats refresh. The dashboard reads
`artifacts/sft_data/<job>/lf.stats.json`, and the Cloudflare sync loop can keep
those stats fresh by running `scripts/convert_trajectories.sh --skip-unchanged`.

### Local preview

```bash
./dashboard/progress_monitor.py                              # one-shot generate
./dashboard/progress_monitor.py --serve --open               # generate + local server + open browser
./dashboard/progress_monitor.py --loop 60 --serve --port 8765   # refresh every 60s
./dashboard/progress_monitor.py --sample-limit 50            # smaller page
./dashboard/progress_monitor.py --no-include-samples         # omit embedded sample previews
./dashboard/progress_monitor.py --index-job <harbor-job>     # add a local Harbor batch to Instances/Trajectories
./dashboard/progress_monitor.py --local-mode public --public-no-samples  # public metrics-only payload
```

Outputs `dashboard/site/index.html`; serves at `http://127.0.0.1:8765/index.html`.
The mtime parse cache lives at `dashboard/.cache/.progress_monitor_cache.json`.
Both `dashboard/site/` and `dashboard/.cache/` are gitignored and wiped by
`scripts/clean.sh`.

Trajectory previews are bounded by `--sample-limit`, `--sample-preview-chars`,
and `--sample-message-limit`. Defaults are good for local inspection; reduce
them or use `--no-include-samples` before publishing sensitive or very large data.
Instances/Trajectories read trial-level facts from `artifacts/jobs/` like everything
else; `--harbor-jobs-dir` can point at a Harbor jobs directory outside the block,
but that is an escape hatch, not the normal path. Use `--max-trials-per-job` and
`--max-quality-records-per-dataset` for faster smoke tests or lighter public
pages.

## Online sync (Cloudflare Pages)

`dashboard/run_cloudflare_pages_sync.sh` loop-generates the HTML and deploys
`dashboard/site/` to Cloudflare Pages via `wrangler`, yielding a public URL
(project `legoflow-tracer`, whose assigned hostname the script reads back and
prints — never guess it from the project name). Each iteration also calls
`scripts/convert_trajectories.sh --skip-unchanged` every `CONVERT_EVERY_SECONDS`
to refresh SFT stats. Config is read from `~/.config/trajgen_progress_cloudflare.env`
(override with `ENV_FILE`); `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`
are required. Run it inside the `tracer-cf` tmux session so it survives disconnects:

```bash
tmux new-session -d -s tracer-cf "ENV_FILE=.env.cf bash dashboard/run_cloudflare_pages_sync.sh 2>&1 | tee /tmp/tracer_cf_sync.log"
```

Public payload controls:

| Env var | Default | Meaning |
| --- | --- | --- |
| `DASHBOARD_INCLUDE_SAMPLES` | `1` | Set `0` to deploy without embedded sample previews. |
| `DASHBOARD_SAMPLE_LIMIT` | `200` | Max samples per SFT dataset. |
| `DASHBOARD_SAMPLE_PREVIEW_CHARS` | `1200` | Max characters per message preview. |
| `DASHBOARD_SAMPLE_MESSAGE_LIMIT` | `12` | Max messages per sample preview. |
| `DASHBOARD_LOCAL_MODE` | `public` | `full` includes bounded analysis text previews. |
| `DASHBOARD_HARBOR_JOBS_DIR` | `artifacts/jobs` | Local Harbor trial source. |
| `DASHBOARD_MAX_TRIALS_PER_JOB` | `0` | Max trial facts per Harbor job; 0 = all. |
| `DASHBOARD_MAX_QUALITY_RECORDS_PER_DATASET` | `0` | Max quality facts per SFT dataset; 0 = all. |
| `TRACER_R2_UPLOAD` | `0` | Upload full local Harbor `trajectory.json` objects to R2. |
| `TRACER_R2_BUCKET` | empty | R2 bucket used for `trajs/...` objects. |
| `TRACER_R2_UPLOAD_LIMIT` | `0` | Max R2 objects per loop; 0 = all manifest rows. |

To enable full online trajectory loading, bind the R2 bucket to the Cloudflare
Pages project as `TRACER_TRAJ_BUCKET`. Without the binding, the dashboard still
serves metrics and bounded previews; `/api/traj` returns a controlled error.

## Restarting the sync loop (important)

The loop reads `CACHE_FILE` and other path variables **once at startup**. If you
change `run_cloudflare_pages_sync.sh` or the cache path, the running process
keeps using its in-memory (old) values — e.g. it will keep recreating an old
cache directory every iteration. To pick up changes, restart it:

```bash
tmux kill-session -t tracer-cf
tmux new-session -d -s tracer-cf "ENV_FILE=.env.cf bash dashboard/run_cloudflare_pages_sync.sh 2>&1 | tee /tmp/tracer_cf_sync.log"
```

Restarting briefly pauses online refresh (the next iteration regenerates and
redeploys within seconds) and does not affect any running Harbor job.

## `--serve` vs the sync script

- `./dashboard/progress_monitor.py --serve` — local generate + preview only, no deploy.
- `bash dashboard/run_cloudflare_pages_sync.sh` — long-running online sync that loops generation and publishes via `wrangler pages deploy`.

## Refresh SFT data and stats

Use `scripts/convert_trajectories.sh` when a finished Harbor job needs
LLaMA-Factory SFT data, or when the dashboard's SFT table needs fresh stats.
Requires the `swe_data_process` repo and its uv env
(`artifacts/env/swe-data-process-uv`) — provision via `/tracer:setup` if
missing.

```bash
scripts/convert_trajectories.sh                                   # job=latest, defaults from config
scripts/convert_trajectories.sh --job <name|latest>
scripts/convert_trajectories.sh --job latest --scaffold claude_code
scripts/convert_trajectories.sh --job <name> --out-dir artifacts/sft_data --max-instances 100
scripts/convert_trajectories.sh --job latest --skip-unchanged
```

Defaults are read from `runtime_info.input.sft_conversion` in `config.yaml`
(`enabled`, `scaffold`, `out_dir`, `max_instances`, `exclude_repos_file`).

Outputs are written under `<out_dir>/<job>/` (default
`artifacts/sft_data/<job>/`):

- `im.jsonl` — intermediate OpenAI-style messages
- `lf.json` — LLaMA-Factory ShareGPT array (consumed by the `trainer` block)
- `lf.stats.json` — counts, token lengths, turns, scores (surfaced by the dashboard)
- `.convert_sig.json` — signature of the resolved instance set + inputs (drives `--skip-unchanged`)

| Flag | Meaning |
| --- | --- |
| `--job <name\|latest>` | Which job under `artifacts/jobs/`. `latest` = most recently modified dir. |
| `--scaffold <auto\|claude_code\|open_code\|openhands_sdk\|terminus2>` | Trajectory format. `auto` derives from the **job name** and falls back to `runtime_info.input.agent.name`, e.g. `custom-claude-code → claude_code`. Override when job name and agent disagree. |
| `--out-dir <path>` | Output root (default `artifacts/sft_data`). |
| `--max-instances <N>` | Cap converted instances. |
| `--exclude-repos-file <path>` | Repos to drop; `""` = converter default `artifacts/excluded_repos.txt`. |
| `--skip-unchanged` | Exit early without reconverting when the job's resolved (reward=1.0) instance set and inputs are unchanged since the last run. Used by the dashboard sync loop. |

If `runtime_info.input.sft_conversion.enabled: true`, `scripts/start.sh` calls
`scripts/convert_trajectories.sh --job "$JOB_NAME"` after Harbor exits; no
manual step is needed.

## Guardrails

- The board has **no configuration**. Never edit `config.yaml` to change what it
  reads — the sources are fixed under `artifacts/`. To change what it shows,
  change what is under `artifacts/`.
- Never render before showing the `--report-only` output and getting an explicit
  "yes".
- The only writes are `dashboard/site/` and `dashboard/.cache/`. Reading jobs,
  tasks and SFT data is read-only. Both write targets are gitignored and wiped by
  `scripts/clean.sh`.
- A missing SFT dataset is a normal state, not a fault: the board simply carries
  no trajectory-score surfaces. Do not substitute zeros, and do not run a
  conversion to fill them in unless the user asks for that separate action.
- Never launch `scripts/start.sh`, `prepare_tasks.sh`, a Harbor job, or a deploy
  unless the user asks for that separate action.
