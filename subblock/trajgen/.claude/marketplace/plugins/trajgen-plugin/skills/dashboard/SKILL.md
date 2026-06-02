---
name: dashboard
description: >
  Drive the trajgen progress dashboard. Generate or serve the local
  self-contained HTML board (dashboard/progress_monitor.py, scans
  artifacts/jobs/*/result.json and artifacts/sft_data/*/lf.stats.json), or run /
  restart the Cloudflare Pages sync loop (dashboard/run_cloudflare_pages_sync.sh,
  tmux session trajgen-cf) that publishes it online. Use when asked to "show
  trajgen progress", "open the dashboard", "start/restart the cloudflare sync",
  or "why does dashboard/memory keep coming back". Triggers on "/trajgen:dashboard".
---

# /trajgen:dashboard

Generate, preview, or publish the trajgen progress board. Run from the block
root `subblock/trajgen/`. The generator is stdlib-only via `uv run` (PEP 723
inline metadata + uv-run shebang) — no env to maintain; `uv` must be on `PATH`.
Full flag reference: `docs/reference/dashboard.mdx`.

## Local preview

```bash
./dashboard/progress_monitor.py                              # one-shot generate
./dashboard/progress_monitor.py --serve --open               # generate + local server + open browser
./dashboard/progress_monitor.py --loop 60 --serve --port 8765   # refresh every 60s
```

Outputs `dashboard/site/index.html`; serves at `http://127.0.0.1:8765/index.html`.
The mtime parse cache lives at `dashboard/.cache/.progress_monitor_cache.json`.
Both `dashboard/site/` and `dashboard/.cache/` are gitignored and wiped by
`scripts/clean.sh`.

## Online sync (Cloudflare Pages)

`dashboard/run_cloudflare_pages_sync.sh` loop-generates the HTML and deploys
`dashboard/site/` to Cloudflare Pages via `wrangler`, yielding a public URL
(current: <https://swe-trajgen-databoard.pages.dev>). Each iteration also calls
`scripts/convert_trajectories.sh --skip-unchanged` every `CONVERT_EVERY_SECONDS`
to refresh SFT stats. Config is read from `~/.config/trajgen_progress_cloudflare.env`
(override with `ENV_FILE`); `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`
are required. Run it inside the `trajgen-cf` tmux session so it survives disconnects:

```bash
tmux new-session -d -s trajgen-cf "ENV_FILE=.env.cf bash dashboard/run_cloudflare_pages_sync.sh 2>&1 | tee /tmp/trajgen_cf_sync.log"
```

## Restarting the sync loop (important)

The loop reads `CACHE_FILE` and other path variables **once at startup**. If you
change `run_cloudflare_pages_sync.sh` or the cache path, the running process
keeps using its in-memory (old) values — e.g. it will keep recreating an old
cache directory every iteration. To pick up changes, restart it:

```bash
tmux kill-session -t trajgen-cf
tmux new-session -d -s trajgen-cf "ENV_FILE=.env.cf bash dashboard/run_cloudflare_pages_sync.sh 2>&1 | tee /tmp/trajgen_cf_sync.log"
```

Restarting briefly pauses online refresh (the next iteration regenerates and
redeploys within seconds) and does not affect any running Harbor job.

## `--serve` vs the sync script

- `./dashboard/progress_monitor.py --serve` — local generate + preview only, no deploy.
- `bash dashboard/run_cloudflare_pages_sync.sh` — long-running online sync that loops generation and publishes via `wrangler pages deploy`.
