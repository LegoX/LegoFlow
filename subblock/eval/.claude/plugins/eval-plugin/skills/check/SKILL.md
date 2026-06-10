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

Read-only preflight. Wraps `scripts/dryrun.sh` — the single source of
truth for what "eval is ready" means — and adds the few live, contextual
checks a static script cannot make: an LLM-endpoint reachability probe,
whether a Harbor job is already running, whether the LiteLLM port is held
by a foreign process, and whether this run would overwrite an existing
job dir. Reports **all** failures in one pass; never edits `config.yaml`,
never launches.

## How to run

1. **Be on the right host first.** `meta_info.resources.ip` is currently
   `192.168.35.240` — a real remote IP, not `local`. If the current
   shell is on a different host, SSH to `192.168.35.240` and run the
   checks from the block dir there. Never preflight on a host the run
   won't use; env paths, port ownership, and Docker state are all
   host-specific.
2. Execute `bash scripts/dryrun.sh` from the block root. It is the
   authoritative readiness check (sections below).
3. Run the live contextual checks the dryrun can't (see "Contextual
   checks").
4. Report PASS / WARN / FAIL counts using the dryrun's section headings,
   then print the run-configuration summary.

## What `scripts/dryrun.sh` covers

| § | Checks |
|---|---|
| 1. Block files | `CLAUDE.md`, `config.yaml`, `dashboard/overview.mdx`, **`artifacts/index.yaml`** all exist. |
| 2. YAML syntax | `config.yaml` and `index.yaml` parse under PyYAML. |
| 3. Harbor repo config | `url` / `commit` (or `ref`/`branch`) / `path` / `readonly` set; path is gitignored. |
| 4. Local Harbor checkout | `repos/harbor/.git` present, origin URL matches, **HEAD == `commit` pin** (drift is FAIL), worktree clean. |
| 5. Harbor env | `harbor_uv` env exists and is outside `repos/harbor`; Python ≥ 3.12; `import harbor / litellm / datasets`; `harbor` resolves to `repos/harbor` (editable); `harbor --help` works. |
| 6. LiteLLM env | `litellm_uv` venv exists; Python == 3.13; `litellm` CLI present; installed `litellm` version == `1.83.14`. |
| 7. Model API | `llm_api.{api_base_url, model, api_key}` set (costs empty → WARN). **No live probe.** |
| 8. Harbor run config | proxy/task_source/harbor_job/agent fields set; `provider == harbor_registry`; **`(dataset_name, version)` resolves in `registry.json` and reports task count**; LiteLLM template exists; agent `model_name` derivable; `jobs_dir`/`job_dir` under `artifacts/jobs`; `runtime_image` set; **`runtime_host_path` populated with the per-agent marker file**. |
| 9. Run command | `command_override` empty (default Harbor command will be built) or present. |

## Contextual checks the skill adds

`dryrun.sh` is static. Add these live checks and fold them into the
report:

- **LLM endpoint probe** (the description's `GET /models`): dryrun
  deliberately skips it — §7 only confirms the fields are set, because
  reachability is exercised by each job *after* LiteLLM proxy startup.
  The skill is where the probe belongs:
  `curl -fsS -H "Authorization: Bearer <api_key>" <api_base_url>/models`
  — expect 200 with the configured model (basename of `llm_api.model`)
  in `data[].id`. See "Interpreting results" for the CF-gating caveat.
- **A job already running**: a live `harbor run` (or a stray LiteLLM on
  the configured port) means launching now would double-book the host.
  Check for an existing `eval` tmux session and a `litellm`/`harbor`
  process before declaring SAFE-TO-RUN.
- **LiteLLM port owner**: `runtime_info.input.litellm_proxy.port`
  (currently `4101`) must be free or held by *this* eval job. A foreign
  process on the port is FAIL — `start.sh` will fail to bind.
- **Overwrite guard**: `start.sh` timestamps the job dir, so collisions
  are unlikely, but if `job_dir` is pinned in config, warn when it
  already contains results.
- **`artifacts/index.yaml` presence**: it was removed in a recent merge,
  so dryrun §1 currently FAILs. If absent, flag it and point the user at
  `/eval:setup` to recreate it (`runs: []`); `scripts/archive_run.sh`
  also writes it on first run.

## Interpreting results

- **Harbor commit drift / dirty worktree** (§4): FAIL, not WARN. Harbor
  is a read-only managed dependency. Do not auto-checkout — defer to
  `/eval:setup`'s `scripts/update_repos.sh`, which refuses to update a
  dirty worktree.
- **`agent.runtime_host_path` empty / missing marker** (§8): real FAIL.
  When set, `start.sh` bind-mounts that host dir into every task
  container; if it's empty the agent silently falls back to an
  in-container install (`curl …/install.sh` for claude-code, `pip` for
  openhands-sdk) that 403s/times out on isolated networks. dryrun prints
  the exact `docker create … && docker cp …` extraction hint — fix in
  `/eval:setup` before running.
- **`registry.json` `not_found`** (§8): the `(dataset_name, version)`
  pair isn't in the registry. Re-check against the curated table in
  `CLAUDE.md`; list everything with
  `python3 -c 'import json;[print(e["name"]+"@"+e["version"]) for e in json.load(open("repos/harbor/registry.json"))]'`.
  If the entry exists but lies **outside** the curated table, emit a WARN
  with the agent-compatibility caveat from `CLAUDE.md` ("Other registry
  entries") — math/MCQ/QA/own-runtime benchmarks generally don't work
  with the configured custom agent.
- **LiteLLM version mismatch** (§6): FAIL. The pin is `1.83.14`; other
  versions change proxy config semantics. Rebuild the venv in setup.
- **LLM endpoint 401/403 from this shell** (contextual probe): when the
  base URL is `qwen.jierungogogo.com` (or another CF-gated production
  endpoint), `dummy-key` is the real production key and the 401 is a
  network artifact specific to Claude Code's sandboxed shell — see memory
  `project-swegen-llm-endpoint`. Treat as WARN, not FAIL, and suggest
  re-probing from a non-sandboxed shell on `192.168.35.240`.
- **`scripts/stop.sh` missing**: WARN. The eval block currently ships no
  `stop.sh`; teardown is handled by `start.sh`'s EXIT trap
  (`cleanup_litellm` + `archive_run.sh`). Note it but do not block.
- **Token costs empty** (§7): WARN — billing accounting only.

## When to escalate FAIL vs WARN

dryrun's classification is intentional. Do not promote warnings to
failures unless the user asks. The expected real-world WARNs:

| WARN | Meaning |
|---|---|
| `input_cost_per_token / output_cost_per_token empty` | Billing accounting only. |
| benchmark outside the curated `CLAUDE.md` table | Agent compatibility is the user's call. |
| LLM endpoint 401/403 from this shell | Likely CF-gating artifact (see memory). |
| `scripts/stop.sh` missing | Teardown is handled by `start.sh`'s EXIT trap. |

## Run-configuration summary

After all checks, print one summary block before exiting:

```
eval run config
  host                       : <ip> (remote → SSH) or 'local'
  repos/harbor HEAD          : <sha> (pin <commit>)
  llm provider               : <api_base_url> / <model>
  benchmark                  : <dataset_name>@<version> (<n_tasks_in_registry> tasks)
  agent                      : <agent.name>@<agent.version>, image <agent.runtime_image>
  runtime_host_path          : <path> (populated? yes/no)
  harbor job dir             : <harbor_job.jobs_dir>  (n_concurrent <N>, n_tasks <null|N>)
  litellm                    : port <port>, config <litellm_proxy.config_template>
  excluded tasks             : <HARBOR_EXCLUDE_TASKS or none>
```

This is the surface the user inspects before approving `:run`.

## Mandatory before `/eval:run`

Per the root `CLAUDE.md` "check → confirm → run" workflow, this skill
must run AND the user must explicitly confirm before any `/eval:run`
invocation. Never auto-launch.

## Out of scope

- No side effects: no `docker pull`/`docker cp`, no `update_repos.sh`,
  no env builds, no proxy start. All checks are read-only and fast.
- Fixing failures: this skill only diagnoses. Repo/env/runtime fixes
  belong in `/eval:setup`.
