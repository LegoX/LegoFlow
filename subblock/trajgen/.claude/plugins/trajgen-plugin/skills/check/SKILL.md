---
name: check
description: >
  Preflight the trajgen block: validate config.yaml schema; verify
  Harbor / swe_data_process pinned commits match; confirm the three uv/venv
  environments exist and have the expected editable installs; probe the
  configured LLM endpoint with `GET /models` (no chat completion calls);
  check the swegen task source contains a `verifiable_tasks.txt`; confirm
  the LiteLLM proxy port is free or held by the current job; sanity-check
  `artifacts/consumption_ledger.yaml`. Read-only. Triggers on phrases like
  "check trajgen", "preflight trajgen", "is trajgen ready", "diagnose
  trajgen", "validate trajgen config".
---

# /trajgen:check

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:check` is the read-only preflight.
Reports all failures in one pass.

## Intent

1. **Schema** — `config.yaml` parses; `meta_info.name == 'trajgen'`.
2. **Repos / commits** — each pinned commit under `meta_info.repositories`
   matches the working tree HEAD; `harbor` and `swe_data_process` are
   read-only.
3. **Envs** — Harbor uv env, LiteLLM venv, swe_data_process uv env exist
   at the configured paths, with the expected editable installs.
4. **LLM endpoint** — `GET <api_base_url>/models` reachable; the configured
   model id present in `data[].id`.
5. **Upstream task source** —
   `<task_source.dataset_name>/verifiable_tasks.txt` exists; at least one
   task ID is not already in `consumption_ledger.yaml` as `done`.
6. **LiteLLM port** — port from `runtime_info.input.litellm_proxy.port` is
   free, or held by a process belonging to the current job.
7. **Ledger** — `artifacts/consumption_ledger.yaml` parses; status values
   are valid (`pending | running | done | failed | skipped`); every `done`
   / `failed` / `skipped` entry is also in `HARBOR_EXCLUDE_TASKS`.
8. **dryrun.sh** — run if present.

## TODO

- [ ] Decide whether to verify the agent's runtime image is present on the
      configured Docker host.
