---
name: setup
description: >
  Bootstrap the trajgen block: clone+pin Harbor and swe_data_process to the
  commits declared in `meta_info.repositories`, build the Harbor uv env
  under `artifacts/env/harbor-uv/`, the LiteLLM venv at the configured
  path, and the swe_data_process uv env at `artifacts/env/swe-data-process-uv/`.
  Then fill in `runtime_info.input.llm_api` / `task_source` / `harbor_job` /
  `agent` — prompting only for values that aren't already set. Idempotent.
  Triggers on phrases like "set up trajgen", "bootstrap trajgen",
  "install harbor for trajgen", "prepare trajgen before running".
---

# /trajgen:setup

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:setup` brings the block from a fresh
clone to "`:check` passes".

## Intent

1. **Repos** — for each entry under `meta_info.repositories`: clone (if
   missing), `git checkout <commit>`. Treat `harbor` and `swe_data_process`
   as read-only mirrors.
2. **Envs** — build the three uv/venv environments under `artifacts/env/`
   per `meta_info.environment`. Skip a build if the env exists and its
   `pip freeze` matches the lockfile.
3. **Config** — walk `runtime_info.input` (`llm_api`, `litellm_proxy`,
   `task_source`, `harbor_job`, `agent`, `sft_conversion`) and prompt for
   unfilled fields. The `task_source.dataset_name` should point at
   `subblock/swegen/artifacts/swe_tasks/<lang>-cc/`.
4. **Consumption ledger** — initialise `artifacts/consumption_ledger.yaml`
   if missing (`runs: []` shape).

## TODO

- [ ] Decide whether `:setup` should also call `/swegen:check` to confirm
      the upstream task source is non-empty.
- [ ] Spec the rebuild-vs-reuse heuristic for stale envs.
