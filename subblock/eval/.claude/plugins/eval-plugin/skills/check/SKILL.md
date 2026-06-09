---
name: check
description: >
  Preflight the eval block: validate config.yaml schema; verify
  `repos/harbor/` pinned commit matches `meta_info.repositories.harbor`;
  confirm Harbor uv env and LiteLLM venv exist with expected editable
  installs; probe the configured LLM endpoint with `GET /models`
  (no chat completion calls); confirm the configured
  `(task_source.dataset_name, version)` resolves in
  `repos/harbor/registry.json`; confirm the LiteLLM proxy port is free
  or held by the current job. Read-only. Reports all failures in one
  consolidated message with a run-configuration summary.
  **Mandatory before `:run`.** Triggers on phrases like "check eval",
  "preflight eval", "is eval ready", "diagnose eval", "validate eval
  config".
---

# /eval:check

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:check` is the read-only preflight.
Reports **all** failures in one pass.

## Intent

1. **Schema** — `config.yaml` parses; `meta_info.name == 'eval'`.
2. **Repo pin** — `repos/harbor/` exists and its `git rev-parse HEAD`
   equals `meta_info.repositories.harbor.commit_id`. Drift is FAIL, not
   WARN. Harbor is read-only here — do not auto-checkout; defer to
   `/eval:setup`'s `scripts/update_repos.sh`.
3. **Envs** — Harbor uv env and LiteLLM venv exist at the configured
   paths and have the expected editable installs.
4. **LLM endpoint** — `GET <api_base_url>/models` returns 200; the
   configured `llm_api.model` is present in `data[].id`. (No
   chat-completion call here — eval routes everything through the
   LiteLLM proxy at runtime; the proxy's own startup catches the rest.)
5. **Benchmark registry** — read `repos/harbor/registry.json`; confirm
   an entry exists matching
   `(task_source.dataset_name, task_source.version)`. If the entry
   exists but lies outside the curated table in `CLAUDE.md`, emit a
   WARN with the agent-compatibility caveat verbatim.
6. **LiteLLM port** — `runtime_info.input.litellm_proxy.port` is either
   free, or held by a process belonging to the current eval job. A
   foreign process on the port is FAIL.
7. **Remote reachability** — if `meta_info.resources.ip` is a real
   remote IP, `ssh -o BatchMode=yes -o ConnectTimeout=5 <ip> true`
   succeeds.
8. **`scripts/dryrun.sh`** — run if present; surface its OK / WARN /
   FAIL lines.
9. **`scripts/stop.sh`** — file exists and is executable (per
   BLOCK_DEFINITION.md §2.1). WARN if missing.

## Run-configuration summary

After all checks, print one summary block before exiting:

```
eval run config
  repos/harbor HEAD          : <sha>
  llm provider               : <api_base_url> / <model>
  benchmark                  : <dataset_name>@<version> (<n_tasks_in_registry> tasks)
  agent                      : <agent.name>@<agent.version>, image <agent.runtime_image>
  harbor job dir             : <harbor_job.jobs_dir>
  litellm                    : port <port>, config <litellm_proxy.config_template>
  remote                     : <ip or 'local'>
```

This is the surface the user inspects before approving `:run`.

## TODO

- [ ] Decide whether to verify the agent's runtime image is present on
      the Docker host (Harbor will pull it lazily; an offline node would
      catch this only at first task).
- [ ] Wire `HARBOR_EXCLUDE_TASKS` parsing into the registry-count so the
      printed `n_tasks_in_registry` reflects the post-exclusion size.
