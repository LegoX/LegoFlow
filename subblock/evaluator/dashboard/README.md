# Harbor Job Dashboard

Web UI for browsing Harbor job results, analysis reports, and agent trajectories.

## Features

- **Overview**: aggregate stats across all jobs (scaffolds, datasets, models, resolve rates)
- **All jobs**: sortable table of jobs with key metrics
- **Single job**: drill down into analysis reports, primary failure distributions, task breakdowns, trial-level details
- **Compare**: side-by-side comparison of multiple jobs (shift-click jobs to add to compare set)
- **Trajectory viewer**: step-by-step agent execution browser with message/tool-call/observation inspection

Styled after the LLaMA-Factory webui: slate-950 dark theme (light theme toggle), sidebar navigation, indigo accents.

## Quick start

```bash
cd subblock/evaluator/dashboard
python3 server.py --port 8092
```

Open http://localhost:8092 in your browser.

The server auto-discovers jobs under `../artifacts/jobs/` (customizable with
`HARBOR_JOBS_DIR` or `--jobs-dir`).

## Long-term public sharing with Cloudflare Pages

For long-term public access, export the dynamic dashboard data into static JSON/HTML and deploy it to Cloudflare Pages. The public site remains interactive in the browser (search, sorting, filters, compare, charts, trajectory browsing), but it updates only after each export/deploy cycle instead of reading local `artifacts/jobs/` live on every request.

### Free no-R2 trajectory mode

If you do not want to enable Cloudflare R2, use static trajectory chunks. The exporter groups many trajectories into size-limited chunk JSON files and writes a manifest. Opening the dashboard or a job does not download trajectories; clicking a trial downloads only the chunk containing that trial, then the browser reuses the cached chunk for nearby trials.

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
python3 export_static.py --output-dir site --trajectory-target chunks --trajectory-chunk-mb 8
python3 -m http.server 8093 --bind 127.0.0.1 --directory site
```

Use smaller chunks for lighter trial clicks, or larger chunks for fewer files:

```bash
python3 export_static.py --output-dir site --trajectory-target chunks --trajectory-chunk-mb 5
python3 export_static.py --output-dir site --trajectory-target chunks --trajectory-chunk-mb 12
```

Deploy the generated `site/` directory to Cloudflare Pages:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
set -a
. "$HOME/.config/harbor_webui_cloudflare.env"
set +a
npx --yes wrangler pages project create "$PROJECT_NAME" --production-branch "$BRANCH_NAME" || true
npx --yes wrangler pages deploy site \
  --project-name "$PROJECT_NAME" \
  --branch "$BRANCH_NAME" \
  --commit-dirty=true \
  --commit-message "Update Harbor dashboard $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
```

The long-running sync script defaults to this free chunk mode:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
bash run_cloudflare_pages_sync.sh
```

Useful overrides:

```bash
LOOP_SECONDS=600 bash run_cloudflare_pages_sync.sh
TRAJECTORY_CHUNK_MB=5 bash run_cloudflare_pages_sync.sh
PUBLIC_DIR=/tmp/harbor-webui-site bash run_cloudflare_pages_sync.sh
```

### Optional R2 trajectory mode

For the fastest per-trial loads, trajectories can also be served on demand from Cloudflare R2 instead of Pages chunks. This keeps the Pages site smaller: opening the dashboard or a job does not download trajectories, and clicking a trial downloads only that one trajectory JSON. R2 may require enabling R2/billing in the Cloudflare Dashboard.

Create a config file at `~/.config/harbor_webui_cloudflare.env`:

```bash
CLOUDFLARE_API_TOKEN="..."
CLOUDFLARE_ACCOUNT_ID="..."
PROJECT_NAME="harbor-dashboard"
BRANCH_NAME="harbor-webui"
LOOP_SECONDS="3600"
HARBOR_JOBS_DIR="/path/to/SWE-Lego-Live/subblock/evaluator/artifacts/jobs"
TRAJECTORY_TARGET="none"
R2_BUCKET_NAME="harbor-trajectories"
R2_UPLOAD_WORKERS="8"
```

`TRAJECTORY_TARGET=none` is required for R2-backed sync. The sync
script's default is `chunks`, which deliberately skips R2 upload.

One-time R2 setup:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
npx --yes wrangler r2 bucket create harbor-trajectories
```

In the Cloudflare Pages project, add an R2 binding named exactly
`TRAJECTORIES` that points to this bucket. The generated `_worker.js`
uses that binding for on-demand trajectory requests.

Generate a fast local static snapshot without local trajectory files:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
python3 export_static.py --output-dir site --trajectory-target none
python3 -m http.server 8093 --bind 127.0.0.1 --directory site
```

Open http://127.0.0.1:8093 to preview the exported site. Local preview cannot read R2 through the Pages Worker, so trajectory requests are verified after Cloudflare Pages deployment.

Upload trajectories to R2:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
python3 upload_trajectories_r2.py \
  --jobs-dir /path/to/SWE-Lego-Live/subblock/evaluator/artifacts/jobs \
  --bucket harbor-trajectories
```

Start the long-running Cloudflare Pages sync loop:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
bash run_cloudflare_pages_sync.sh
```

The sync script exports the fast Pages site, ensures the R2 bucket exists, uploads trajectories to R2, deploys `site/` with `wrangler pages deploy`, then repeats after `LOOP_SECONDS`.

Useful overrides:

```bash
LOOP_SECONDS=600 bash run_cloudflare_pages_sync.sh
PUBLIC_DIR=/tmp/harbor-webui-site bash run_cloudflare_pages_sync.sh
R2_UPLOAD_WORKERS=16 bash run_cloudflare_pages_sync.sh
```

Notes:

- `site/_worker.js` handles public trajectory requests and fetches the matching object from the `TRAJECTORIES` R2 binding.
- The public Cloudflare Pages URL is stable for the project/branch, unlike free Pinggy URLs.
- New or changed jobs and trajectories appear publicly after the next sync loop finishes.
- Use `python3 export_static.py --trajectory-target pages` only for local/full debugging; it writes all trajectories into `site/` and can create a multi-GB directory that is too large for Pages.

## Temporary sharing with Pinggy

Pinggy is still useful for debugging the live dynamic Python API, but free tunnels are temporary and can change or time out.

Start the dashboard locally first:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
./start.sh
```

In another terminal, start the auto-reconnect Pinggy tunnel:

```bash
cd /path/to/SWE-Lego-Live/subblock/evaluator/dashboard
bash share_pinggy.sh
```

The script checks that `http://127.0.0.1:8092/` is reachable before opening the tunnel. If the free Pinggy tunnel times out or disconnects, it reconnects automatically after a short delay. Pinggy prints the current public URL in the terminal each time it connects.

Useful overrides:

```bash
PORT=9000 bash share_pinggy.sh
RECONNECT_DELAY=10 bash share_pinggy.sh
PINGGY_HOST=a.pinggy.io bash share_pinggy.sh
```

Notes:

- Free Pinggy URLs are temporary and can change after reconnect.
- If Pinggy asks for a password, press Enter.

## Architecture

- **Backend**: `server.py` — stdlib-only HTTP server, JSON API
- **Frontend**: `static/` — vanilla JS SPA (no build step)
  - `index.html` — layout shell
  - `app.js` — state, routing, rendering
  - `styles.css` — theme variables, component styles
  - `favicon.svg` — harbor icon

## API endpoints

- `GET /api/jobs` — list all jobs with summary stats
- `GET /api/overview` — aggregate overview (job count, scaffolds, datasets, models, resolve rates)
- `GET /api/jobs/<name>` — detailed job view (config, analysis reports, trials)
- `GET /api/jobs/<name>/trials` — list trial summaries
- `GET /api/jobs/<name>/trials/<trial>` — single trial detail
- `GET /api/jobs/<name>/trials/<trial>/trajectory?kind=agent` — trajectory JSON
- `GET /api/jobs/<name>/rule_score_instances?kind=resolved&limit=50` — scored instance details
- `GET /api/compare?name=<job1>&name=<job2>` — side-by-side comparison

## Usage tips

- **Shift-click** a job in the sidebar to add it to the compare set (indicated by left accent border)
- Click a trial row to view its trajectory
- Use the job filter input to narrow the sidebar list
- Theme toggle (☀/☾) in the top-right corner
- Browser localStorage persists: theme, compare set

## Development notes

All dependencies are stdlib + Chart.js CDN. No npm, no build step.

To add a new panel:
1. Add route to `NAV` array in `app.js`
2. Add case to `render()` switch
3. Implement `render<PanelName>()` function
4. Update `applyHash()` if the route needs params

Analysis data schema follows the job-level `analysis/` output:
- `report_failed.json` / `report_resolved.json` — primary/axis distributions
- `score_comparison.json` — resolved vs unresolved deterministic features
- `report_task_analysis.json` — task difficulty tiers and domain/bug-type breakdowns
- `traj_analysis/score_comparison.json` — rule-based composite scoring
- `instance_analysis/{summary,correlations}.json` — tag breakdowns and correlations

Trajectory schema: ATIF v1.5 (`agent/trajectory.json`).
