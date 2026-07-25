---
name: setup
description: >
  One-shot bootstrap for the root block tree — verify shared tooling, ensure the
  root config.yaml exists and matches the contract, and (optionally, on user
  confirmation) recurse into each subblock listed under `meta_info.subblocks`
  and invoke its own `:setup` skill in order. Idempotent: safe to re-run; only
  touches fields the user has not already filled in. Triggers on phrases like
  "set up the project", "bootstrap the pipeline", "fill in the root config",
  "initial setup", "wire up API keys", "prepare everything before /root:run".
---

# /root:setup

Per the block plugin guidelines (`resources/BLOCK_DEFINITION.md` → § Plugin
skills), every block exposes a `:setup` skill that bootstraps everything the
block needs before `:check` can pass. At the root level this covers:

1. **Shared tooling** — verify (or install) the global python3+PyYAML / uv /
   docker prerequisites the pipeline assumes; abort with actionable next steps
   if missing.
2. **Root config** — ensure `./config.yaml` exists and matches the contract
   (orchestration identity only: `meta_info.subblocks` roster with role
   one-liners, explicit `dependencies: {from: {}, to: {}}`, empty `runtime_info.input`/`output`).
   If missing, scaffold it from
   `resources/config.template.yaml`. **The root owns no shared inputs**: the
   blocks intentionally use different LLM endpoints/keys, so every external
   value is filled per-block by that block's own `:setup` — do not invent a
   shared `runtime_info.input` at the root.
3. **Recurse (optional)** — for each `name` in `meta_info.subblocks`, ask the
   user "set up `<name>` now?" and on yes hand off to `/<name>:setup`. Each
   subblock's setup fills its own `runtime_info.input`, replacing `human`
   markers with real values (never writing secrets into config — env/file
   channels only, as each field's inline comment directs).

## Optional extras

These two are **off by default** — always ask before doing either, even if
the user has run `/root:setup` before. Neither is required for `/root:check`
or `/root:run` to pass; they only unlock specific quality-of-life gains.

4. **Docker registry login** — ask "log in to Docker Hub now to raise the
   anonymous pull rate limit?" This requires an interactive `docker login`
   (the user supplies their own credentials or a token; never prompt for or
   store a password on their behalf — just run `docker login` and let the
   user's own terminal handle the prompt). Tell the user plainly **why**
   before asking: `tracer` and `evaluator` both pull large numbers of Docker
   images per run (one per Harbor task environment), and anonymous Docker
   Hub pulls are aggressively rate-limited — a logged-in session raises that
   ceiling substantially and avoids mid-run `429`/pull-throttling failures in
   those two blocks. Skip silently if the user declines; do not treat a
   skipped login as a setup failure.
5. **Cloudflare Pages tooling + credentials** — ask "set up Cloudflare Pages
   publishing for the dashboards now?" Tell the user this is for **dashboard
   visualization only** (curator, tracer, and evaluator each have a
   `dashboard/run_cloudflare_pages_sync.sh` that publishes their progress
   dashboard to Cloudflare Pages) — declining it just means dashboards stay
   local-only (`dashboard/site/index.html`, served on a local port), nothing
   else in the pipeline depends on it. If the user says yes:
   - Verify a Node.js/npm/npx toolchain exists (`command -v npx`); if not,
     ask before installing one — this is a host-level change, not a
     per-block one.
   - Ask the user for `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` and
     write them to `~/.config/swegen_progress_cloudflare.env` (the path each
     block's `run_cloudflare_pages_sync.sh` reads via `$SWEGEN_HOME`).
     **Never accept these values pasted directly in chat** — have the user
     set the file themselves via a `!`-prefixed shell command in their own
     terminal, then confirm back to you when done. Never echo the token
     back or write it anywhere else (logs, config.yaml, other env files).

## Exit criterion

Setup is done when `python3 scripts/validate_config.py --root .` reports no
findings other than `input:unfilled` for values the user has chosen not to
provide yet — surface those remaining `human` markers as the user's to-do list,
then point them at `/root:check` for the full preflight (live probes included).

Never mutate `runtime_info.input` values the user has already filled in without
explicit confirmation. Never write secrets to `config.yaml` — keep them in env
vars or ignored local files (each block's dryrun checks `$WANDB_API_KEY`,
`$GITHUB_TOKENS`, etc.).
