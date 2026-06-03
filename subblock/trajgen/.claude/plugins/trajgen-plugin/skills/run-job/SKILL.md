---
name: run-job
description: >
  Run one trajgen Harbor trajectory job end to end and do the trajgen-specific
  bookkeeping the generic /block:run does not: dryrun preflight, generate the
  per-job LiteLLM proxy config and start the proxy, launch Harbor with
  --exclude-task-name flags built from HARBOR_EXCLUDE_TASKS, then stop the proxy,
  inspect artifacts/jobs/<job>/, and update consumption_ledger.yaml,
  HARBOR_EXCLUDE_TASKS, and config.yaml's status. Use when asked to "run a
  trajgen job", "generate trajectories", "launch harbor for trajgen", or after
  /trajgen:setup passes dryrun. Triggers on "/trajgen:run-job".
---

# /trajgen:run-job

Execute and account for a single Harbor trajectory job. Run from the block root
`subblock/trajgen/`, inside a named tmux session on the host named by
`meta_info.resources.ip` (currently `local` → this host) so the job survives
disconnects.

> Relationship to `/block:run`: `/block:run trajgen` runs the generic preflight,
> executes `scripts/start.sh`, and archives the run via the EXIT trap. This skill
> describes what `start.sh` orchestrates and the **manual post-run steps** that
> keep the task-consumption contract correct. Prefer `/block:run` to launch;
> follow Steps 4–5 here afterward.

## Step 1 — Preflight

```bash
scripts/dryrun.sh
```

Must pass: config, both managed repos, all three envs, task dirs, and the
`sft_conversion` block. If tasks are missing, run `/trajgen:setup` first (or
`TRAJGEN_PREPARE_TASKS=1 scripts/start.sh` to re-prepare inline).

Optionally inspect the exact Harbor command without launching:

```bash
scripts/start.sh --dry-run-command
```

## Step 2 — Launch

```bash
scripts/start.sh                 # full run
scripts/start.sh --update-repos  # refresh Harbor first (or TRAJGEN_UPDATE_REPOS=1)
```

`start.sh` performs, in order:
1. dryrun preflight;
2. generate the per-job LiteLLM config from `runtime_info.input.llm_api` +
   `litellm_proxy` and **start the proxy** on `runtime_info.input.litellm_proxy.port`;
3. build and run the Harbor command from `config.yaml`, adding one
   `--exclude-task-name <id>` per token in `environment.extra.HARBOR_EXCLUDE_TASKS`;
4. if `runtime_info.input.sft_conversion.enabled: true`, run
   `scripts/convert_trajectories.sh --job "$JOB_NAME"` after Harbor exits (see `/trajgen:convert-sft`).

Job output lands under `runtime_info.input.harbor_job.jobs_dir` =
`artifacts/jobs/<job>/`, with per-task logs at
`artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`.

## Step 3 — Stop the proxy

When Harbor inference finishes the per-job LiteLLM proxy may still be running.
`start.sh` cleans it up via its EXIT trap, but if the job was interrupted, find
and stop only the LiteLLM process started for **this** job (matched by the
configured port) — never kill unrelated LiteLLM processes.

## Step 4 — Inspect results

Inspect the newest dir under `artifacts/jobs/`. Confirm `result.json` exists and
count trajectory files (`…/agent/litellm-trajectory.jsonl`). Summarize per-task
outcome and reward.

## Step 5 — Bookkeeping (the trajgen-specific contract)

Trajgen **only** runs tasks listed in swegen's `verifiable_tasks.txt`, and must
never re-run a task it already processed. After every job:

1. Update `artifacts/consumption_ledger.yaml` — one entry per task with
   `status` (`pending | running | done | failed | skipped`), `submitted_at`,
   `completed_at`, `trajectory_path`, `reward`, `note`.
2. Add every task now `done`, `failed` (excluded), or `skipped` to
   `environment.extra.HARBOR_EXCLUDE_TASKS` in `config.yaml`, so the next
   `start.sh` skips it.
3. Update `config.yaml`'s `status` block (`phase`, `progress`, `next_steps`,
   `blockers`, `last_updated`). Remember `config.yaml` is one-shot per run — the
   authoritative timeline is `artifacts/index.yaml` (written by `archive_run.sh`).

## Step 6 — (optional) Convert + publish

- SFT data: `/trajgen:convert-sft` (or it runs inline when `sft_conversion.enabled: true`).
- Progress board: `/trajgen:dashboard`.
