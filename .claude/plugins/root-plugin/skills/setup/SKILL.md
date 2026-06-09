---
name: setup
description: >
  One-shot bootstrap for the root block tree — set up shared tooling, fill in
  shared config (API keys, public model paths, dashboard wiring, common
  credentials), and (optionally, on user confirmation) recurse into each
  subblock listed under `meta_info.subblocks` and invoke its own `:setup`
  skill in order. Idempotent: safe to re-run; only touches fields the user
  has not already filled in. Triggers on phrases like "set up the project",
  "bootstrap the pipeline", "fill in the root config", "initial setup",
  "wire up API keys", "prepare everything before /root:run".
---

# /root:setup

**STATUS: stub — fill in.**

Per the block plugin guidelines (`resources/BLOCK_DEFINITION.md` → § Plugin
skills), every block exposes a `:setup` skill that bootstraps everything the
block needs before `:check` can pass. At the root level this covers:

1. **Shared tooling** — verify (or install) the global venv / uv / docker /
   kubectl prerequisites the pipeline assumes; abort with actionable next
   steps if missing.
2. **Shared config** — walk `runtime_info.input` keys that are commonly
   shared across subblocks (LLM API base + key, public model paths, wandb
   credentials, GitHub tokens) and prompt the user once; write the values
   into the relevant subblock `config.yaml`s.
3. **Dashboard wiring** — make sure the root dashboard is reachable (calls
   `/root:dashboard` if not already up).
4. **Recurse (optional)** — for each `name` in `meta_info.subblocks`, ask
   the user "set up `<name>` now?" and on yes hand off to `/<name>:setup`.

Never mutate `runtime_info.input` values the user has already filled in
without explicit confirmation. Never write secrets to `config.yaml` — keep
them in env vars (the dryrun checks for `$WANDB_API_KEY` etc.).

## TODO

- [ ] Define the exact list of "shared" input keys vs subblock-private ones.
- [ ] Decide whether recursion is opt-in (default no) or opt-out.
- [ ] Spec the failure model — bail at first missing prerequisite, or
      collect-and-report like `/root:check`.
