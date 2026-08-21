---
name: run
description: >
  Launch the tracer pipeline via `scripts/start.sh` after preflight passes,
  and do the tracer-specific post-run accounting: prepare/filter tasks, start
  the per-job LiteLLM proxy, run Harbor trajectories, clean up the proxy,
  inspect artifacts/jobs/<job>/, update processed_tasks.yaml (the single
  exclusion source), and optionally produce LF-format SFT data under
  artifacts/sft_data/<job>/lf.json. Long-running. Triggers on phrases like
  "run tracer", "run a tracer job", "launch tracer", "generate
  trajectories", "kick off the harbor jobs", "start the tracer pipeline",
  or "launch harbor for tracer".
---

# /tracer:run

Execute and account for a tracer Harbor trajectory job. Run from the block
root `blocks/tracer/`, inside a named tmux session on the host named by
`meta_info.resources.ip` (currently `local` → this host) so the job survives
disconnects.

`/root:run tracer` runs the generic preflight, executes `scripts/start.sh`, and
archives the run via the EXIT trap. This block-local skill documents the
tracer-specific layer that `start.sh` orchestrates, plus the manual post-run
steps that keep the task-consumption contract correct.

## Step 1 — Preflight

```bash
scripts/dryrun.sh
```

Must pass: config, both managed repos, all three envs, and the
`sft_conversion` block. Preflight does **not** require staged tasks — an
unstaged block is just one that has not run yet, and `start.sh` stages before it
launches. It does check that the task *source* exists, which staging cannot
conjure.

Optionally inspect the exact Harbor command without launching:

```bash
scripts/start.sh --dry-run-command
```

## Step 2 — Confirm

Before launching, print the run config summary and wait for explicit user
confirmation:

- task source dataset and prepared task count
- Harbor concurrency, retry count, and timeout multiplier
- agent name, version, runtime image, and model
- SFT conversion mode (`runtime_info.input.sft_conversion.enabled`)

Do not auto-launch a long Harbor job just because preflight passed.

## Step 3 — Launch

```bash
scripts/start.sh                 # full run
scripts/start.sh --update-repos  # refresh Harbor first (or TRAJGEN_UPDATE_REPOS=1)
```

`start.sh` performs, in order:

1. dryrun preflight;
2. `scripts/prepare_tasks.sh`, which stages the task pool (set
   `TRAJGEN_PREPARE_TASKS=0` to skip when it is already staged);
3. generate the per-job LiteLLM config from `runtime_info.input.llm_api` +
   `litellm_proxy` and **start the proxy** on `runtime_info.input.litellm_proxy.port`;
4. build and run the Harbor command from `config.yaml`, adding one
   `--exclude-task-name <id>` per task id that
   `scripts/resolve_exclude_tasks.py` resolves from
   `runtime_info.input.env_extra.HARBOR_EXCLUDE_TASKS`;
5. if `runtime_info.input.sft_conversion.enabled: true`, run
   `scripts/convert_trajectories.sh --job "$JOB_NAME"` after Harbor exits.

Job output lands under `runtime_info.input.harbor_job.jobs_dir` =
`artifacts/jobs/<job>/`, with per-task logs at
`artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`.

## Step 4 — Stop the proxy

When Harbor inference finishes the per-job LiteLLM proxy may still be running.
`start.sh` cleans it up via its EXIT trap, but if the job was interrupted, find
and stop only the LiteLLM process started for **this** job (matched by the
configured port) — never kill unrelated LiteLLM processes.

## Step 5 — Inspect results

Inspect the newest dir under `artifacts/jobs/`. Confirm `result.json` exists and
count trajectory files (`…/agent/litellm-trajectory.jsonl`). Summarize per-task
outcome and reward.

## Step 6 — Bookkeeping

Tracer **only** runs tasks listed in curator's `verifiable_tasks.txt`, and must
never re-run a task it already processed. After every job:

1. Append to `artifacts/processed_tasks.yaml` — **one flat entry per task**
   under `runs:`, with `task_id`, `status` (`pending | running | done | failed |
   skipped`), `submitted_at`, `completed_at`, `trajectory_path`, `reward`,
   `note`. Keep it flat: `start.sh` derives the exclude list by reading
   `runs[*].{task_id,status}`, so wrapping the entries in a per-job layer makes
   it silently derive **nothing**.
2. Do **not** copy those task ids into
   `runtime_info.input.env_extra.HARBOR_EXCLUDE_TASKS`. That field names
   `excluded_tasks.txt` + `artifacts/processed_tasks.yaml`, and
   `scripts/resolve_exclude_tasks.py` expands both at launch — appending ids to
   config grows it by one line per task forever and re-creates the drift this
   design removed. To retire a task permanently, append its id to
   **`excluded_tasks.txt`** (git-tracked, unlike the ledger).
3. Do not add a `status:` block to `config.yaml` — that section is retired (see
   the root `CLAUDE.md`). `config.yaml` is one-shot per run; the authoritative
   timeline is `artifacts/index.yaml`, written by `archive_run.sh`.

## Step 7 — Optional dashboard/SFT refresh

- SFT data and stats: `/tracer:dashboard` covers
  `scripts/convert_trajectories.sh` for one-off conversion or
  `--skip-unchanged` refresh.
- Progress board: `/tracer:dashboard` covers local preview and Cloudflare
  Pages sync.
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow run tracer`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
