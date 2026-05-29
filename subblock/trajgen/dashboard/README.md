# Trajgen Local Dashboard

A small, local-only HTML dashboard for the `trajgen` subblock. It scans two
artifact directories and renders a single self-contained HTML file you can
either open directly in a browser or serve over the loopback interface.

Modeled after [`dashboard/swegen/`](https://github.com/SWE-Lego/SWE-Lego-Live/tree/swegen/dashboard/swegen)
on the `swegen` branch, but stripped to stdlib-only (no `tiktoken`,
no `tomllib`) and without any Cloudflare Pages publishing.

## Runtime

The script ships with [PEP 723](https://peps.python.org/pep-0723/) inline
script metadata and a `uv run` shebang, so the runtime Python is provided
by `uv` (no `pip install`, no shared venv). Requires `uv` on `PATH` and
a Python interpreter `>= 3.11` (uv will download one if needed).

```text
#!/usr/bin/env -S uv run --no-project --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
```

Because dependencies are empty (stdlib-only), there is no `dashboard-uv`
environment to maintain. If a third-party package is ever needed (e.g.
`tiktoken` for token accounting), add it to the `dependencies = [...]`
list and uv will resolve it transparently on the next run.

## What it monitors

| Source | Files | Shown as |
| --- | --- | --- |
| Harbor jobs | `../artifacts/jobs/<job>/result.json` (only jobs that actually have one) | Harbor Jobs table + per-job eval breakdown |
| SFT conversion | `../artifacts/sft_data/<job>/lf.stats.json` | SFT Datasets table |

Key fields surfaced for each Harbor job:

- `id`, `started_at`, `finished_at`, `n_total_trials`
- `stats.n_trials`, `stats.n_errors`
- For each `stats.evals.<eval>`: `n_trials`, `n_errors`, `metrics[0].mean`,
  number of `reward == 1.0` / `reward == 0.0` outcomes, and a summary of
  `exception_stats`.

Key fields surfaced for each SFT dataset:

- `count`
- `token_lens` (min / mean / max, `gt_128k`)
- `n_turns` (min / mean / max, `gte_100`)
- `scores` (min / mean / max)
- Sizes of `im.jsonl` and `lf.json` on disk.

## Files

```text
dashboard/
├── progress_monitor.py                     # generator (this script)
├── README.md                               # this file
├── site/index.html                         # generated, gitignored
└── memory/.progress_monitor_cache.json     # mtime-based parse cache, gitignored
```

`site/` and `memory/.progress_monitor_cache.json` are added to
[`../.gitignore`](../.gitignore) and wiped by
[`../scripts/clean.sh`](../scripts/clean.sh).

## How to run

From the subblock root (`subblock/trajgen/`). The script is executable;
prefer invoking it directly so the uv-run shebang takes effect:

```bash
./dashboard/progress_monitor.py                                  # one-shot generate
./dashboard/progress_monitor.py --serve --open                   # generate + local preview + open browser
./dashboard/progress_monitor.py --loop 60 --serve --port 8765    # continuous refresh every 60s
```

Equivalent explicit forms (useful in CI or when `./` execution is blocked):

```bash
uv run --no-project --script dashboard/progress_monitor.py [args...]
```

The plain `python3 dashboard/progress_monitor.py` invocation also still
works (the script is stdlib-only), but it bypasses uv's Python pinning.

Then either open the file directly:

```text
subblock/trajgen/dashboard/site/index.html
```

or visit the local server (default port `8765`):

```text
http://127.0.0.1:8765/index.html
```

The HTML also self-refreshes every `--refresh` seconds (default 60) so an
opened tab stays current while `--loop` keeps writing new snapshots.

## CLI flags

| Flag | Default | Purpose |
| --- | --- | --- |
| `--output-html` | `dashboard/site/index.html` | Where to write the HTML. |
| `--cache-file` | `dashboard/memory/.progress_monitor_cache.json` | Per-file mtime cache to skip re-parsing unchanged `result.json` / `lf.stats.json`. |
| `--jobs-dir` | `../artifacts/jobs` | Harbor jobs root. |
| `--sft-dir` | `../artifacts/sft_data` | SFT conversion root. |
| `--refresh` | `60` | Browser-side `<meta refresh>` interval (seconds). |
| `--loop [N]` | off (60 if bare flag) | Regenerate every N seconds in a loop. |
| `--serve` | off | Start `ThreadingHTTPServer` over `site/`. |
| `--host` / `--port` | `127.0.0.1` / `8765` | HTTP bind. |
| `--open` | off | Open the HTML in the default browser after the first write. |
| `--force-full-scan` | off | Ignore cache for this run. |

## Out of scope

- Cloudflare Pages deploy (the swegen reference's `run_cloudflare_pages_sync.sh`).
- Per-trial trajectory browsing (only `result.json` aggregates are shown).
- Historical 1h / 24h deltas via a `state.jsonl` snapshot file.

If any of those are needed later, lift the corresponding helpers directly
from `dashboard/swegen/progress_monitor_all.py` on the `swegen` branch.
