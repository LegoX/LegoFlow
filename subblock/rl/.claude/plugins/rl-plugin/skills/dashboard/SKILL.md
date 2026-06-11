---
name: dashboard
description: >
  Read-only progress surface for the rl block. Default is a textual
  summary: live job state (pgrep), the newest artifacts/index.yaml run
  entry, the latest launch/upstream log tails, LiteLLM health, and the
  wandb project/mode. On request it also manages the optional vendored web
  dashboard (dashboard/webui, stdlib server.py pointed at logs/launch_*.log
  and repos/harbor-verl-train/logs/*.log) via dashboard/serve.sh
  (start|stop|status|restart), or publishes it to Cloudflare Pages via
  dashboard/run_cloudflare_pages_sync.sh (deploy). Never modifies training
  state. Triggers on phrases like "rl dashboard", "show rl progress",
  "how's rl training going", "start the dashboard", "launch the training
  dashboard", "open the rl webui", "stop the dashboard", "is the dashboard
  running", "deploy the dashboard", "publish the dashboard to cloudflare".
---

# /rl:dashboard

The per-block "show me what's happening" surface for rl. Read-only — it
never launches training, edits config, or touches `repos/` state.

Two modes:

- **`summary`** (default, no subcommand) — print a textual state table.
  Works on any host, needs no server.
- **`start` / `status` / `stop` / `restart` / `deploy`** — manage the
  optional web dashboard, vendored at `dashboard/webui/` (prebuilt `dist/`
  ships in the repo, so no `npm` build is needed to serve).

## Step 0 — Orient

Run only from inside the rl block. Validate `./config.yaml` exists and
`meta_info.name == 'rl'`; otherwise abort
("/rl:dashboard must be run from inside the rl block (`subblock/rl/`)").

Parse the subcommand from the user's request: `summary` | `start` |
`status` | `stop` | `restart` | `deploy`. **Default to `summary`** when
the user just asks how training is going; default to `status` only when
they clearly mean the web server ("is the dashboard running").

For the webui subcommands, also validate `./dashboard/serve.sh` and
`./dashboard/webui/server.py` exist. If the webui is missing, tell the
user to vendor it (see `dashboard/README.md`) and fall back to `summary`.

## Step 1 — Textual summary (default)

Gather, read-only:

1. **Live job**: `pgrep -af 'sync_1node_cc|main_ppo'` → running or not;
   if running, `ps -p <pid> -o pid=,etime=` and a LiteLLM probe
   `curl -sS --max-time 5 http://127.0.0.1:<litellm_port>/health/liveliness`
   (port from `runtime_info.input.infrastructure.litellm_port`, default 8002).
2. **Run history**: newest entry in `artifacts/index.yaml` (written by
   `archive_run.sh` on each run's exit): `id`, `started_at`,
   `completed_at`, `status`, `archive`, `notes`.
3. **Logs**: newest `logs/launch_*.log` and, if an exp_name is known (from
   the launch log's first lines), the tail of
   `repos/harbor-verl-train/logs/<exp_name>.log` — quote the last few
   meaningful lines (step number, reward, errors).
4. **Config at a glance** (from `config.yaml`, don't echo the file): model
   basename, backend (K8s / Docker local / Docker remote), num_workers,
   adv_estimator/policy_loss_mode, wandb mode + project.

Print:

```
## rl dashboard — <CWD>

Status: <idle | training (pid=<P>, up <etime>) | last run <status>>
Model:  <basename of model_path>   Backend: <k8s|docker>   Workers: <N>
Algo:   <adv_estimator>/<policy_loss_mode>   wandb: <mode> (<project_name>)

Live (if training):
  litellm: <up | warming | down>   gpu: <util summary from nvidia-smi>
  upstream log: repos/harbor-verl-train/logs/<exp>.log
  last lines: <1–3 quoted lines>

Latest run (artifacts/index.yaml): <id> — <status> — <notes>

Web dashboard: <running at http://<host>:<port> | not running — `/rl:dashboard start`>
```

On a headless host just print paths — don't try to open a browser.

## Step 2 — Local webui (`start` / `status` / `stop` / `restart`)

Run the wrapper, which manages a background `server.py` via
`dashboard/.server.pid` and logs to `dashboard/.server.log`:

```bash
PORT=8090 bash dashboard/serve.sh <start|status|stop|restart>
```

Notes and guardrails:

- **Port**: default `8090`. If the user wants another port, pass `PORT=<n>`.
- **Public access (optional)**: for a quick public URL, set `TUNNEL=true`
  (uses `cloudflared` if installed). Only do this when the user asks — it
  exposes the dashboard publicly.
- **Data sources** are wired by `serve.sh`: `logs/` (launch_*.log) plus
  `repos/harbor-verl-train/logs/` (per-exp `<exp>.log`). `server.py` discovers
  `.log`/`.out` files non-recursively and skips `*_vllm.log`, so nested
  `outputs/<date>/<time>/main_ppo.log` is intentionally NOT a source.
- After `start`, report the URL (`http://<host>:8090`), the pid, and the
  log/extra-log dirs that `serve.sh` printed. Confirm health by quoting the
  `status` probe (`/api/config`).
- Never `start` a second instance on a busy port — `serve.sh` already refuses;
  surface that message rather than killing the running one.

## Step 3 — Publish to Cloudflare Pages (`deploy`)

```bash
bash dashboard/run_cloudflare_pages_sync.sh
```

Before running, confirm prerequisites and explain the model:

- The cloud (Pages Functions, `webui/functions/api`) is **wandb-only** — it
  reads runs/metrics straight from the wandb API, NOT from local logs. So the
  published dashboard shows whatever is in the configured wandb project.
- Requires `~/.config/rl_dashboard_cloudflare.env` with `CLOUDFLARE_API_TOKEN`
  and `CLOUDFLARE_ACCOUNT_ID` (and ideally `WANDB_API_KEY` / `WANDB_ENTITY` /
  `WANDB_PROJECT`, which the script pushes as Pages secrets). If that file is
  missing, the script prints exactly what to put in it — relay that to the user
  instead of inventing values.
- This is an outward-facing publish. Confirm with the user before deploying if
  they didn't explicitly ask to deploy.
- A single deploy is enough (the cloud reads wandb live). Only set
  `LOOP_SECONDS=<n>` if the user wants periodic redeploys of a rebuilt frontend.

## Guardrails — never do these

- Never modify training state, logs, checkpoints, or `config.yaml`. The
  dashboard is read-only monitoring.
- Never modify anything under `repos/` — pinned, read-only code.
- Never hardcode or echo wandb / Cloudflare API keys. They come from the user's
  env file and stay there.
- Never `npm install` / rebuild the frontend unless the user explicitly asks —
  the vendored `dist/` already serves. A rebuild needs `dashboard/webui`'s
  `node_modules` (large) and network.
- Never expose a public tunnel or deploy to Cloudflare without the user's
  intent — both are outward-facing.
