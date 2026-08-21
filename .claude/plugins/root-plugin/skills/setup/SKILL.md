---
name: setup
description: >
  One-shot bootstrap for the root block tree — verify shared tooling, ensure the
  root config.yaml exists and matches the contract, and (optionally, on user
  confirmation) recurse into each block listed under `meta_info.blocks`
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
   (orchestration identity: `meta_info.blocks` roster with role one-liners,
   explicit `dependencies: {from: {}, to: {}}`, empty `runtime_info.output`).
   If missing, scaffold it from `resources/config.template.yaml`.
   **The root owns no shared *pipeline* inputs**: the blocks intentionally use
   different LLM endpoints/keys, so every external pipeline value is filled
   per-block by that block's own `:setup` — do not invent a shared `llm_api` or
   task-source at the root.

   The one exception is `runtime_info.input.cloudflare` and
   `runtime_info.input.docker`: tree-wide, **optional**, account-level
   infrastructure credentials that every block reads through
   `scripts/shared_credentials.sh`. Both default to all-empty (feature off) and
   are filled in step 4/5 below, never automatically.
3. **Recurse (optional)** — for each `name` in `meta_info.blocks`, ask the
   user "set up `<name>` now?" and on yes hand off to `/<name>:setup`. Each
   block's setup fills its own `runtime_info.input`, replacing `human`
   markers with real values (never writing secrets into config — env/file
   channels only, as each field's inline comment directs).

## Optional extras

These two are **off by default** — always ask before doing either, even if
the user has run `/root:setup` before. Neither is required for `/root:check`
or `/root:run` to pass; they only unlock specific quality-of-life gains.

Both write to the same place: root `config.yaml` →
`runtime_info.input.{docker,cloudflare}`, read by every block through
`scripts/shared_credentials.sh` (resolution order: env > root config.yaml >
the block's own legacy env file). **Non-secret fields go in `config.yaml`;
secrets do not** — `config.yaml` is git-tracked, so `api_token` and `password`
stay `""` there and come from the environment instead.

4. **Docker registry credentials** — ask "configure a container-registry login
   to raise the anonymous pull rate limit?" Tell the user plainly **why**
   before asking: `curator`, `tracer` and `evaluator` all pull large numbers of
   Docker images per run (one per task environment), and anonymous Docker Hub
   pulls are capped at 100 per 6h per IP — hitting it mid-run surfaces as
   manifest errors that look like agent or verifier failures. If the user says
   yes:
   - Write the non-secret fields into root `config.yaml` →
     `runtime_info.input.docker`: `username`, and `registry`/`mirror` if they
     use something other than Docker Hub.
   - Have the user supply the password/token as `$DOCKER_PASSWORD` in their own
     environment. **Never accept a password pasted in chat**, and never write
     one into `config.yaml`.
   - Alternatively, a plain interactive `docker login` in their own terminal
     works too and needs no config at all — `scripts/docker_login.sh --status`
     and the root dryrun both detect an existing login.
   Skip silently if the user declines; a skipped login is not a setup failure.
5. **Cloudflare Pages tooling + credentials** — ask "set up Cloudflare Pages
   publishing for the dashboards now?" Tell the user this is for **dashboard
   visualization only** (tracer and evaluator each have a
   `dashboard/run_cloudflare_pages_sync.sh` sync loop; curator publishes its
   databoard with a manual `wrangler pages deploy` from `dashboard/site/`;
   trainer instead uses an anonymous `cloudflared` quick tunnel and needs no
   account at all) — declining it just means dashboards stay local-only
   (`dashboard/site/index.html`, served on a local port), nothing else in the
   pipeline depends on it. If the user says yes:
   - Verify a Node.js/npm/npx toolchain exists (`command -v npx`); if not,
     ask before installing one — this is a host-level change, not a
     per-block one. A common gotcha: node installed via `nvm` is absent from
     non-interactive shells, so `npx` resolves for the user but not for the
     sync script. Check for `~/.nvm/versions/node/*/bin/npx` before concluding
     node is missing, and if that is the situation, put that bin dir on `PATH`
     in the env file rather than installing a second node.
   - Write `account_id` (not a secret) into root `config.yaml` →
     `runtime_info.input.cloudflare.account_id`. Project names are not
     configurable: each block's dashboard publishes to `legoflow-<block>`.
   - Have the user supply `CLOUDFLARE_API_TOKEN` in their own environment, or
     in whichever per-block env file they already use (curator:
     `swegen_progress_cloudflare.env`, tracer:
     `trajgen_progress_cloudflare.env`, evaluator:
     `harbor_webui_cloudflare.env`, all under `$SWEGEN_HOME/.config/`) — those
     remain supported as a fallback. **Never accept the token pasted directly
     in chat** — have the user write it themselves via a `!`-prefixed shell
     command or their own editor, then confirm back to you when done. Never
     echo the token back or write it into `config.yaml`.
   - The token needs **Cloudflare Pages: Edit** permission. Confirm it works
     with `bash scripts/dryrun.sh` (its step 3 live-probes the token against
     `/user/tokens/verify`), not by guessing.

## Exit criterion

Setup is done when `python3 scripts/validate_config.py --root .` reports no
findings other than `input:unfilled` for values the user has chosen not to
provide yet — surface those remaining `human` markers as the user's to-do list,
then point them at `/root:check` for the full preflight (live probes included).

Never mutate `runtime_info.input` values the user has already filled in without
explicit confirmation. Never write secrets to `config.yaml` — keep them in env
vars or ignored local files (each block's dryrun checks `$WANDB_API_KEY`,
`$GITHUB_TOKENS`, etc.).
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow setup <block>`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
## LegoFlow Command Convention

The canonical command for this skill is `/root:setup`. The shared CLI accepts the same command as `./bin/legoflow /root:setup` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
