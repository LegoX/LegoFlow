# LegoFlow-Trainer Training Dashboard

Real-time, read-only web dashboard for monitoring LLaMA-Factory (HuggingFace
Trainer) training runs. It parses the standard `saves/<run>/` output —
`trainer_log.jsonl`, `trainer_state.json`, and the `*_results.json` summaries —
and optionally connects to wandb. Multiple runs can be compared on the same
charts, and an LLM can generate a diagnostic analysis report.

> This is a **monitoring** dashboard. It does not launch or control training —
> for that, use the built-in Gradio LLaMA Board (`llamafactory-cli webui`).

## Quick Start

```bash
cd dashboard
./start_dashboard.sh          # builds the frontend (first run) then serves :8091
```

Open <http://localhost:8091>. By default it reads `../artifacts/model` (runs) and
`../artifacts/logs` (raw console logs).

## Expose externally (Cloudflare quick tunnel)

To reach the dashboard from a browser outside this network, forward the local
port through a Cloudflare quick tunnel — no DNS / account / login needed; the
tunnel prints a temporary `https://<random>.trycloudflare.com` URL that stays
live until the `cloudflared` process exits.

> ⚠️ Quick tunnels have **no authentication**. Anyone with the URL can read the
> dashboard (logs, metrics, run names). Treat the URL as a secret and stop the
> tunnel when done. For persistent / access-controlled exposure, use a
> [named tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
> + Cloudflare Access instead.

### Option A — bundled in `start_dashboard.sh`

```bash
TUNNEL=true ./start_dashboard.sh
```

The script auto-discovers `cloudflared` from `PATH`, then from the usual local
install locations (`~/.local/bin`, `/usr/local/bin`, `/opt/cloudflared/bin`).
Override with `CLOUDFLARED_BIN=/path/to/cloudflared` if installed elsewhere. cloudflared's
output is redirected to `/tmp/dashboard-tunnel-<port>.log` (override with
`TUNNEL_LOG=...`), and the public URL is printed in a banner on the dashboard
terminal once it's ready. If you lost the banner, recover it with:

```bash
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/dashboard-tunnel-8091.log | head -1
# or from a tmux pane history:
tmux capture-pane -t <session> -p -S -5000 \
  | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | sort -u
```

### Option B — decoupled (dashboard already running)

If the dashboard is already up (e.g. via `nohup ./start_dashboard.sh &`),
start the tunnel as an independent process:

```bash
nohup "${CLOUDFLARED_BIN:-cloudflared}" \
  tunnel --url http://127.0.0.1:8091 \
  > /tmp/dashboard-tunnel.log 2>&1 &

# Grab the public URL once it's printed:
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/dashboard-tunnel.log | head -1
```

Stop it later with `pkill -f 'cloudflared tunnel --url http://127.0.0.1:8091'`.

If `cloudflared` is not installed, grab it once:

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o ~/bin/cloudflared && chmod +x ~/bin/cloudflared
```

## Manual Setup

```bash
cd dashboard
npm install        # requires Node 18+
npm run build      # produces dist/
python server.py --port 8091 --save-dir ../artifacts/model --log-dir ../artifacts/logs --static-dir dist
```

### Development mode (hot reload)

```bash
# Terminal 1 — API server
python server.py --port 8091 --save-dir ../artifacts/model --log-dir ../artifacts/logs
# Terminal 2 — Vite dev server (proxies /api to :8091)
npm run dev        # http://localhost:3000
```

### wandb integration (optional)

```bash
export WANDB_API_KEY=...   WANDB_ENTITY=...   WANDB_PROJECT=llama-factory
python server.py --port 8091 --save-dir ../artifacts/model --wandb-entity "$WANDB_ENTITY" \
  --wandb-project "$WANDB_PROJECT" --static-dir dist
```

## Server CLI Options

```
python server.py [OPTIONS]
  --port PORT              Server port (default: 8091)
  --host HOST              Bind address (default: 0.0.0.0)
  --save-dir DIR           Directory containing saves/<run>/ output dirs (default: ../artifacts/model)
  --extra-save-dir DIR     Additional saves dirs (repeatable)
  --log-dir DIR            Directory with train_*.log console logs (default: ../artifacts/logs)
  --static-dir DIR         Directory with built frontend (default: dist/)
  --wandb-entity ENTITY    wandb entity
  --wandb-project PROJECT  wandb project (default: llama-factory)
  --wandb-api-key KEY      wandb API key
```

## Environment Variables (start_dashboard.sh)

| Variable | Default | Description |
|---|---|---|
| `SAVE_DIR` | `../artifacts/model` | Run output directories |
| `LOG_DIR` | `../artifacts/logs` | Raw console logs (`train_*.log`) |
| `PORT` | `8091` | Server port |
| `TUNNEL` | `false` | Open a Cloudflare quick tunnel (needs `cloudflared`) |
| `WANDB_ENTITY` / `WANDB_PROJECT` / `WANDB_API_KEY` | | wandb integration |

## Panels

- **Overview** — step, epoch, progress, train/eval loss, lr, grad norm, step time, ETA
- **Training** — loss, learning-rate schedule, gradient norm, epoch progress
- **Evaluation** — `eval_loss` over steps, best checkpoint (empty if no eval split)
- **Performance** — samples/s, steps/s, runtime, total FLOPs (from `all_results.json`) + per-step timing
- **Compare** — overlay metrics from multiple runs on shared charts (PNG/CSV export)
- **AI Analysis** — LLM-generated diagnostic report (configure a profile in Settings)
- **Logs** — raw `train_*.log` console viewer
- **Explorer** — plot any available metric key
- **Settings** — LLM API profiles (stored in browser localStorage only)

UI supports a Chinese/English toggle and dark/light themes.

## How a "run" is detected

Any immediate sub-directory of a `--save-dir` containing `trainer_log.jsonl`
or `trainer_state.json` is treated as a run. State is `running` (jsonl updated
< 3 min ago and `percentage` < 100), `finished` (`all_results.json` present or
`percentage` ≈ 100), or `unknown`.

## Data normalization

Per-step metrics are normalized to `{step, loss, eval_loss, lr, grad_norm,
epoch, percentage, elapsed_sec, remaining_sec, step_time_sec, total_steps}`.
`step_time_sec` is derived from consecutive `elapsed_time` deltas. The live
`trainer_log.jsonl` is the primary source; eval points and extra keys are
merged in from `trainer_state.json`'s `log_history`.
