---
name: setup
description: >
  One-shot bootstrap for the rl block: 1) build the venv at
  `repos/harbor-verl-train/.venv` via `setup_env.sh` (or verify an existing
  one passes the editable-install check); 2) sync the git submodules under
  `repos/` (harbor-verl-train, harbor, verl) to their pinned commits and
  apply the verl patch; 3) fill in `runtime_info.input` in `config.yaml` —
  model path, kubeconfig / docker_host, LLM API endpoint, wandb mode,
  experiment name — prompting the user only for values that aren't already
  set or are obvious placeholders. Idempotent. Triggers on phrases like
  "set up the rl block", "bootstrap rl", "install the rl venv", "wire up
  rl config", "prepare rl before training".
---

# /rl:setup

**STATUS: stub — fill in.**

Per the block plugin guidelines, the `:setup` skill is the one-shot
bootstrap that brings the rl block from a fresh clone to "`:check` passes".

## Intent

1. **venv** — if `repos/harbor-verl-train/.venv` is missing OR the editable
   installs are wrong, run `bash repos/harbor-verl-train/scripts/setup_env.sh`
   (or, if `runtime_info.input.environment.venv_path` is set, verify that
   venv has the correct editable installs of harbor / verl / harbor-verl-train).
2. **submodules** — for each repo under `meta_info.repos`, ensure the
   submodule is checked out at the pinned commit; apply the patch under
   `harbor-verl-train/patches/` if present.
3. **config** — walk the `runtime_info.input` keys (model, infrastructure,
   k8s OR harbor_agent docker_host, experiment, credentials) and prompt for
   anything unfilled or placeholder. Never write secrets into `config.yaml`
   — keep `credentials.wandb_api_key: ""` and tell the user to `export
   WANDB_API_KEY=...` in their shell.
4. **dashboard** — (optional) offer to run `/rl:dashboard` afterwards.

## TODO

- [ ] Decide what to do if the venv exists but is stale (rebuild vs warn).
- [ ] Spec exactly which inputs are interactive vs auto-derived.
- [ ] Hook into `/root:setup` so a root-level bootstrap can delegate here.
