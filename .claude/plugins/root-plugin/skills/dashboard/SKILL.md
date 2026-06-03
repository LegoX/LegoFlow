---
name: dashboard
description: >
  Open (or bring up) the unified project dashboard for the root block tree.
  Aggregates the live state of every subblock — last run id, status,
  duration, key metrics — into one webui served on a local port. The skill
  starts the dashboard server if it isn't already running, prints the URL,
  and (if a browser is available on the host) opens it. Read-only; never
  mutates block state. Triggers on phrases like "open the dashboard",
  "show me what's running", "bring up the webui", "where do I see the
  pipeline status".
---

# /root:dashboard

**STATUS: stub — fill in.**

Per the block plugin guidelines (`resources/BLOCK_DEFINITION.md` → § Plugin
skills), every block exposes a `:dashboard` skill that surfaces a single
human-readable view of that block's state. At the root level this is the
project-wide aggregate.

## Intent

1. Check whether a dashboard server is already up (well-known port + a
   `/health` probe).
2. If not, launch one (background, log to `./logs/dashboard.log`), passing
   it the locations of every subblock's `artifacts/index.yaml`.
3. Print the URL. If `xdg-open` / `open` is available, open the browser.
4. Surface the same data textually as a fallback (a compact table of
   `subblock | last_run | status | started_at | notes`) so the skill is
   still useful in a headless session.

## TODO

- [ ] Choose the dashboard tech (reuse `subblock/rl/.../webui`, or a new
      lightweight aggregator).
- [ ] Spec the well-known port and `/health` contract.
- [ ] Decide whether the dashboard auto-launches on `/root:run`, or stays
      strictly opt-in via this skill.
