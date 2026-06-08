---
name: dashboard
description: >
  Launch the RL training dashboard (webui under
  `repos/harbor-verl-train/webui/`) and print the URL. Reads logs from
  `repos/harbor-verl-train/logs/` plus any extra log dirs, scans
  `harbor_trials/` for trajectories, exposes real-time metric charts and
  trajectory viewer. Background server; idempotent — if already running on
  the configured port, just print the URL. Read-only — never modifies
  training state. Triggers on phrases like "open the rl dashboard",
  "show training metrics", "launch the webui", "bring up the trajectory
  viewer", "where do I watch the rl run".
---

# /rl:dashboard

**STATUS: stub — fill in.**

Per the block plugin guidelines, the `:dashboard` skill is the per-block
"show me what's happening" surface. For rl, the live dashboard already
exists under `repos/harbor-verl-train/webui/` — this skill is a thin
launcher.

## Intent

1. Check `runtime_info.input.dashboard.port` (default 8090) — is anything
   already listening? If yes and `/health` answers, print URL and exit.
2. Otherwise:
   ```bash
   cd repos/harbor-verl-train/webui
   [ -d node_modules ] || npm install
   [ -d dist ] || npx vite build
   nohup python3 server.py \
     --log-dir ../logs \
     --static-dir dist \
     --port <port> \
     > /tmp/rl_dashboard.log 2>&1 &
   ```
3. Wait for `/health` to come up (≤ 20s), then print the URL and a one-line
   summary of what the dashboard exposes (overview, trajectory viewer,
   compare).
4. If the host is headless, just print the URL — don't try to open a
   browser.

## TODO

- [ ] Add the `dashboard.port` input to `config.yaml` (currently implicit
      via `--port` flag).
- [ ] Decide whether `/rl:run` should auto-launch the dashboard, or keep
      it opt-in via this skill.
- [ ] Surface a fallback textual summary (read latest log + latest
      `artifacts/index.yaml` entry) for headless sessions.
