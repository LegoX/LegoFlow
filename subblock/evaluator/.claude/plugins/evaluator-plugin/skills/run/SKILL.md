---
name: run
description: >
  Launch the evaluator pipeline via `scripts/start.sh` after preflight
  passes: generate the per-job LiteLLM config from
  `runtime_info.input.litellm_proxy`, start the LiteLLM proxy on the
  configured port, then run the Harbor job with
  `--dataset <name> --registry-path repos/harbor/registry.json` and any
  `--exclude-task-name` flags from `HARBOR_EXCLUDE_TASKS`. Long-running.
  Stamps live state into `artifacts/index.yaml` via
  `scripts/archive_run.sh`. Triggers on phrases like "run evaluator",
  "launch evaluator", "run swebench", "evaluate the model", "kick off the
  evaluator benchmark".
---

# /evaluator:run

Preflight-then-execute entry point. Always invoke `/evaluator:check` first;
require explicit user confirmation before launching. evaluator is a **leaf
block** — no subblocks — so this skill runs `scripts/start.sh` directly
(per BLOCK_DEFINITION.md §3.2, no child dispatch applies).

## Procedure

1. **Preflight** — invoke `/evaluator:check`; abort on any FAIL. Never launch
   on a stale/unvalidated config.
2. **Confirm** — present the run-configuration summary from `:check`
   plus the benchmark task count (post-`HARBOR_EXCLUDE_TASKS`), the
   `agent.name@version` + `runtime_image`, and a rough wall-time
   estimate. **Wait for an explicit `yes`** (root `CLAUDE.md`
   "check → confirm → run"). Never auto-launch — a full benchmark is
   many container-hours.
3. **Launch** — `bash scripts/start.sh`, background by default (evaluator runs
   are long; ask the user once if they want foreground). Per
   `meta_info.resources.ip`:
   - `local` / null / absent → run on the current host inside a tmux
     session named `evaluator`.
   - real remote IP (currently `192.168.35.240`) → open a local tmux
     window, SSH to the remote, attach to (or create) a remote tmux
     session named `evaluator`, and run `start.sh` inside it from
     `meta_info.resources.directory` (per BLOCK_DEFINITION.md §2.3).
     Confirm with the user whether the code is already in sync at that
     path or needs rsync first. A tmux session keeps the run alive across
     shell disconnects.
4. **Archive** — `start.sh`'s EXIT trap runs `cleanup_litellm` then
   `scripts/archive_run.sh "$rc" "$RUN_STARTED_AT"`, which appends to
   `artifacts/index.yaml` and snapshots config + scripts under
   `artifacts/archives/run_NNN/`. Do not write `index.yaml` from this
   skill.

## What `scripts/start.sh` does internally

1. Optional `update_repos.sh` (only with `--update-repos` /
   `EVAL_UPDATE_REPOS=1`).
2. Re-runs `scripts/dryrun.sh` as its own preflight (exits on FAIL).
3. Generates a per-job LiteLLM config from
   `runtime_info.input.{llm_api, litellm_proxy}` + the Harbor template
   into `artifacts/litellm/<job>/`, copying Harbor's
   `trajectory_logger.py` alongside it.
4. Starts the LiteLLM proxy (`serve_litellm.sh`) on
   `litellm_proxy.port`, waits up to 30 s for it to accept connections,
   and aborts if it exits early.
5. Builds the default Harbor command (`uv run harbor run --dataset …
   --registry-path … --agent-import-path … --mounts-json … --model …`)
   with per-agent flags, `--retry-exclude AgentTimeoutError`, and one
   `--exclude-task-name` per entry in `HARBOR_EXCLUDE_TASKS`, then runs
   it inside `repos/harbor`, teeing to `artifacts/logs/evaluator_<ts>.log`.

Use `bash scripts/start.sh --dry-run-command` to print the generated
LiteLLM config path and Harbor command **without** launching — useful in
the confirm step.

## Modes

| Mode | Trigger | What runs |
|---|---|---|
| `smoke` | Args mention "smoke"/"quick"; or `harbor_job.n_tasks` is a small int; or a `-100` benchmark subset is selected. | Standard `start.sh` flow; Harbor honours the `n_tasks` cap. A `-100` subset is the cleanest smoke. |
| `full`  | Args empty or say "everything"/"all tasks". | `start.sh` against the full benchmark. |

## Conventions to honour

- **Benchmark selection lives in `config.yaml`, not flags.** To change
  benchmark, edit `runtime_info.input.task_source.{dataset_name,version}`
  and re-run `/evaluator:check`. Refuse to override these via free-form args —
  launching a different benchmark than the one `:check` validated is
  never correct.
- **Agent selection lives in `config.yaml`, not flags.** Switch agents by
  editing `agent.{name, version, runtime_image, runtime_host_path}`
  together and re-running `/evaluator:check` (so the runtime-extraction check
  re-runs). Don't inject agent overrides via args.
- **Excluded tasks come from `HARBOR_EXCLUDE_TASKS`.** Record them in
  `config.yaml`'s `environment.extra.HARBOR_EXCLUDE_TASKS` so they
  survive across runs and appear in archives — don't pass exclusions via
  skill args.
- **One LiteLLM proxy per run.** If `:check` flagged the port as held by
  a foreign process, do not start a second proxy on a different port
  without the user explicitly OK-ing the config change.
- **Timed-out tasks are not retried.** `start.sh` always passes
  `--retry-exclude AgentTimeoutError`; a task that times out will time
  out again and only burns budget. If a task repeatedly times out, add it
  to `HARBOR_EXCLUDE_TASKS`.

## Resume on interrupt

Harbor's per-job state under `artifacts/jobs/<job>/` lets an interrupted
run be re-driven. If a prior `evaluator` run was interrupted, surface it and
ask whether to resume that job dir vs. start a fresh one, rather than
silently launching a duplicate.

## Out of scope

- Editing `config.yaml`, building envs, or extracting agent runtimes —
  those belong in `/evaluator:setup`.
- Writing `artifacts/index.yaml` — owned by `archive_run.sh`.
