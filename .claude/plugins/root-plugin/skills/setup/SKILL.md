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

## Exit criterion

Setup is done when `python3 scripts/validate_config.py --root .` reports no
findings other than `input:unfilled` for values the user has chosen not to
provide yet — surface those remaining `human` markers as the user's to-do list,
then point them at `/root:check` for the full preflight (live probes included).

Never mutate `runtime_info.input` values the user has already filled in without
explicit confirmation. Never write secrets to `config.yaml` — keep them in env
vars or ignored local files (each block's dryrun checks `$WANDB_API_KEY`,
`$GITHUB_TOKENS`, etc.).
