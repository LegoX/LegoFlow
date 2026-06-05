---
name: setup
description: >
  Bootstrap the eval block: clone `repos/harbor/` at the commit declared
  in `meta_info.repositories.harbor` (via `scripts/update_repos.sh`),
  build the Harbor uv env at the path under `meta_info.environment.harbor_uv`,
  build the LiteLLM venv at the configured path, then fill in
  `runtime_info.input.llm_api` / `litellm_proxy` / `task_source` /
  `harbor_job` / `agent` — prompting only for values that aren't already
  set. Idempotent. Triggers on phrases like "set up eval", "bootstrap
  eval", "install harbor for eval", "prepare eval before running".
---

# /eval:setup

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:setup` brings the block from a fresh
clone to "`:check` passes".

## Intent

1. **Repos** — for `meta_info.repositories.harbor`: clone via
   `scripts/update_repos.sh` (which refuses to update a dirty Harbor
   worktree per the block's repository policy). Confirm
   `git rev-parse HEAD` matches the configured commit.
2. **Envs** — build the Harbor uv env (path from
   `meta_info.environment.harbor_uv`) and the LiteLLM venv (path from
   `meta_info.environment.litellm_venv`). Skip a build if the env exists
   and the editable installs resolve.
3. **Config** — walk `runtime_info.input` (`llm_api`, `litellm_proxy`,
   `task_source`, `harbor_job`, `agent`) and prompt for unfilled fields.
   `task_source.dataset_name` + `version` together must resolve to an
   entry in `repos/harbor/registry.json`; the prompt should suggest
   curated benchmarks from the CLAUDE.md table.
4. **Artifacts** — initialise `artifacts/index.yaml` if missing
   (`runs: []` shape).

## TODO

- [ ] Decide whether `:setup` should also surface the agent-compatibility
      caveat for non-code benchmarks (CLAUDE.md §"Other registry
      entries") when the user picks a benchmark outside the curated set.
- [ ] Spec the rebuild-vs-reuse heuristic for stale envs.
