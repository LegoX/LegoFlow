# SWE-gen Task Progress Dashboard

This directory holds the generation and deployment code for the SWE-gen task
progress monitoring page. By default the generator reads live SWE task data
under `$SWEGEN_HOME/SWE-gen` and writes runtime files into the current
`subblock/swegen/dashboard/` directory.

The page style follows the MDX dashboard contract from `subblock/eval/dashboard`
on the `yuxin/eval` branch of `SWE-Lego-Live`: a restrained, document-style
layout with clear Overview / Inputs & Outputs / Status / Method Notes sections,
compact tables, and operational handoff notes. The board shows only SWE task
progress, no other data panels.

## Configuration variables

The Cloudflare sync script reads its configuration from
`~/.config/swegen_progress_cloudflare.env` by default. If the config file lives
elsewhere, set `ENV_FILE=/path/to/file` when starting it.

```bash
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
PROJECT_NAME="swe-databoard"
BRANCH_NAME="swegen"
LOOP_SECONDS="3600"
PORT="8000"
SWEGEN_HOME="$HOME"
SWEGEN_DATA_ROOT="$SWEGEN_HOME/SWE-gen"
SWEGEN_TASK_ROOT="$SWEGEN_DATA_ROOT/tasks/March"
SWEGEN_PR_DIR="$SWEGEN_DATA_ROOT/collected_prs"
SWEGEN_DASHBOARD_ROOT="subblock/swegen/dashboard"
```

| Variable | How to fill | Default |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token with access to the target account and Pages project edit/deploy permissions. | none, required |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID, from the Cloudflare console account page. | none, required |
| `PROJECT_NAME` | Cloudflare Pages project name. | `swe-databoard` |
| `BRANCH_NAME` | Cloudflare Pages deploy branch name. | `swegen` |
| `LOOP_SECONDS` | Seconds the sync script waits between generate-and-deploy rounds. | `3600` |
| `PORT` | Local preview HTTP server port. | `8000` |
| `SWEGEN_HOME` | Base home directory on the machine holding SWE-gen data. | current user's `$HOME` |
| `SWEGEN_DATA_ROOT` | SWE-gen data and code root. | `$SWEGEN_HOME/SWE-gen` |
| `SWEGEN_TASK_ROOT` | SWE-gen task output directory. | `$SWEGEN_DATA_ROOT/tasks/March` |
| `SWEGEN_PR_DIR` | PR ID file directory. | `$SWEGEN_DATA_ROOT/collected_prs` |
| `SWEGEN_DASHBOARD_ROOT` | Dashboard runtime output directory. | the script's own directory, i.e. `subblock/swegen/dashboard` |

## Local generation

Run from the repository root:

```bash
python3 subblock/swegen/dashboard/progress_monitor_all.py \
  --output-html subblock/swegen/dashboard/site/index.html \
  --state-file subblock/swegen/dashboard/memory/.progress_monitor_all_state.jsonl \
  --cache-file subblock/swegen/dashboard/memory/.progress_monitor_all_cache.json
```

After generation, open `subblock/swegen/dashboard/site/index.html`, or start a
local server:

```bash
python3 subblock/swegen/dashboard/progress_monitor_all.py --serve
```

## State and cache files

The repo contains two generator runtime-state files:

- `memory/.progress_monitor_all_state.jsonl`: historical snapshot file. Each
  generator run appends one JSON line recording per-language PR counts,
  processed counts, verifiable task counts, and other summary data at that
  moment. The 1-hour and 24-hour deltas on the page come from this file.
- `memory/.progress_monitor_all_cache.json`: incremental cache file. When the
  generator scans task directories and batch state, it writes file signatures
  and statistics here, so the next run can reuse stats for unchanged files
  instead of fully re-parsing large amounts of task data each time.

These two files are generated or updated by the command below:

```bash
python3 subblock/swegen/dashboard/progress_monitor_all.py \
  --output-html subblock/swegen/dashboard/site/index.html \
  --state-file subblock/swegen/dashboard/memory/.progress_monitor_all_state.jsonl \
  --cache-file subblock/swegen/dashboard/memory/.progress_monitor_all_cache.json
```

The sync script `subblock/swegen/dashboard/run_cloudflare_pages_sync.sh` calls
the same generator internally, so running the sync script also updates these
two files.

## Cloudflare Pages sync

Start the sync:

```bash
bash subblock/swegen/dashboard/run_cloudflare_pages_sync.sh
```

Each round, the script first generates
`subblock/swegen/dashboard/site/index.html`, then deploys it to Cloudflare Pages
with `wrangler pages deploy`.

## Difference between `--serve` and the sync script

- `python3 subblock/swegen/dashboard/progress_monitor_all.py --serve`: generates
  and previews the page locally only, starting a local HTTP server — good for
  debugging or viewing local results. It does not deploy to Cloudflare.
- `bash subblock/swegen/dashboard/run_cloudflare_pages_sync.sh`: for long-running
  online sync. It loops generating the page, starts a local preview server, and
  publishes `subblock/swegen/dashboard/site/` to Cloudflare Pages via
  `wrangler pages deploy`.
