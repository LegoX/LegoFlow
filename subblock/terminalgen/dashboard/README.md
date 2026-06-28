# Terminal-gen Progress Dashboard

This directory holds the progress-dashboard generator (and optional deploy
script) for the terminalgen block. `progress_monitor_all.py` reads this block's
own `artifacts/terminal_tasks/{domain}-tl/` and `artifacts/collected_questions/`,
aggregates per the 13 domains, writes a static HTML page, and appends a snapshot
to a JSONL state file.

> Unlike swegen, terminalgen runs locally (`meta_info.resources.ip: local`); the
> generator reads this block's own `artifacts/` and needs no separate data root.

## Generate locally

From the repository root:

```bash
python3 subblock/terminalgen/dashboard/progress_monitor_all.py \
  --output-html subblock/terminalgen/dashboard/site/index.html \
  --state-file subblock/terminalgen/dashboard/memory/.progress_monitor_all_state.jsonl \
  --cache-file subblock/terminalgen/dashboard/memory/.progress_monitor_all_cache.json
```

Open `subblock/terminalgen/dashboard/site/index.html`, or serve it:

```bash
python3 subblock/terminalgen/dashboard/progress_monitor_all.py --serve --port 8000
```

## State and cache files

- `memory/.progress_monitor_all_state.jsonl` — history file. Each run appends one
  JSON line recording per-domain scraped / generated / verified / rate.
- `memory/.progress_monitor_all_cache.json` — a copy of the latest snapshot (kept
  for swegen-style CLI compatibility).

Both are gitignored runtime artifacts.

## Cloudflare Pages sync (optional, off by default)

`run_cloudflare_pages_sync.sh` loops the generator and deploys `site/` via
`wrangler pages deploy`. Config is read from
`~/.config/terminalgen_progress_cloudflare.env` by default (override with
`ENV_FILE=/path/to/file`):

```bash
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
PROJECT_NAME="terminalgen-databoard"
BRANCH_NAME="terminalgen"
LOOP_SECONDS="3600"
PORT="8000"
```

| Variable | Purpose | Default |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API Token (needs Pages edit/deploy) | required |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID | required |
| `PROJECT_NAME` | Cloudflare Pages project name | `terminalgen-databoard` |
| `BRANCH_NAME` | Deployment branch | `terminalgen` |
| `LOOP_SECONDS` | Wait between generate+deploy rounds | `3600` |
| `PORT` | Local preview port | `8000` |

```bash
bash subblock/terminalgen/dashboard/run_cloudflare_pages_sync.sh
```
