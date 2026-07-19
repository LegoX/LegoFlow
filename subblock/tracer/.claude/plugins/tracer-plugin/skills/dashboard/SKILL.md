---
name: dashboard
description: >
  Drive the tracer progress dashboard. Generate or serve the local
  self-contained HTML board (dashboard/progress_monitor.py, scans
  artifacts/jobs/*/result.json and artifacts/sft_data/*/lf.stats.json), or run /
  restart the Cloudflare Pages sync loop (dashboard/run_cloudflare_pages_sync.sh,
  tmux session tracer-cf) that publishes it online. Also covers manually
  refreshing one job's SFT data/stats with scripts/convert_trajectories.sh.
  Use when asked to "show tracer progress", "open the dashboard",
  "start/restart the cloudflare sync", "convert trajectories to SFT data",
  "make the LF dataset", "refresh SFT stats", or "why does dashboard/.cache
  keep coming back". Triggers on "/tracer:dashboard".
---

# /tracer:dashboard

Generate, preview, or publish the tracer progress board. Run from the block
root `subblock/tracer/`. The generator is stdlib-only via `uv run` (PEP 723
inline metadata + uv-run shebang) — no env to maintain; `uv` must be on `PATH`.
Full flag reference: `docs/content/docs/dashboard.mdx`.

This skill also owns SFT stats refresh. The dashboard reads
`artifacts/sft_data/<job>/lf.stats.json`, and the Cloudflare sync loop can keep
those stats fresh by running `scripts/convert_trajectories.sh --skip-unchanged`.

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
(current: <https://swe-tracer-databoard.pages.dev>). Each iteration also calls
`scripts/convert_trajectories.sh --skip-unchanged` every `CONVERT_EVERY_SECONDS`
to refresh SFT stats. Config is read from `~/.config/trajgen_progress_cloudflare.env`
(override with `ENV_FILE`); `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`
are required. Run it inside the `tracer-cf` tmux session so it survives disconnects:

```bash
tmux new-session -d -s tracer-cf "ENV_FILE=.env.cf bash dashboard/run_cloudflare_pages_sync.sh 2>&1 | tee /tmp/tracer_cf_sync.log"
```

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
