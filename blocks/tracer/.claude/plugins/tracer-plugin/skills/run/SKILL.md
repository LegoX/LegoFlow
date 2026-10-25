---
name: run
description: >
  Launch the tracer pipeline via `scripts/start.sh` after preflight passes,
  and do the tracer-specific post-run accounting: prepare/filter tasks, start
  the per-job LiteLLM proxy, run Harbor trajectories, clean up the proxy,
  inspect the job artifacts, update processed_tasks.yaml (the single
  exclusion source), and optionally produce LF-format SFT data under
  the per-job artifacts/sft_data folder. Long-running. Triggers on phrases like
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

Read `TRAJGEN_CONFIG` when set (relative paths are resolved from this block),
otherwise `config.yaml`. Keep that same effective profile throughout preflight,
preparation, launch, and archive; do not reset a parent-selected profile.
For `local_block: terminal-curator`, require the producer skill's completed
instruction/tests/reference-solution semantic audit and fresh Oracle/NOP
revalidation after corrections before starting downstream rollout. A manifest
or Oracle/NOP result by itself does not prove that audit occurred.

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
- sample count (`runtime_info.input.harbor_job.samples_per_task`; `>= 2` multiplies cost linearly and triggers best-of-n campaign mode)
- agent name, version, runtime image, and model — or, when
  `runtime_info.input.agent.scaffolds` is set, the full scaffold list and the
  expected per-scaffold job names printed by the multi-scaffold campaign banner
- SFT conversion mode (`runtime_info.input.sft_conversion.enabled`)

Do not auto-launch a long Harbor job just because preflight passed.

## Step 3 — Launch

```bash
scripts/start.sh                 # full run
scripts/start.sh --update-repos  # refresh Harbor first (or TRAJGEN_UPDATE_REPOS=1)
```

`start.sh` performs, in order:

1. `scripts/prepare_tasks.sh`, which publishes an owned version of the selected
   task pool and returns the fixed dataset path for this invocation. Source,
   manifest, settings, and metadata determine reuse. It refuses an unowned
   existing real directory or symlink even with `--overwrite`; choose a fresh
   `harbor_job.dataset_path` or move old staging after checking active jobs;
2. dryrun preflight. `TRAJGEN_PREPARE_TASKS=0` skips preparation and resolves
   existing staging once; do not use it to bypass source/manifest validation;
3. generate the per-job LiteLLM config from `runtime_info.input.llm_api` +
   `litellm_proxy` and **start the proxy** on `runtime_info.input.litellm_proxy.port`;
4. build and run the Harbor command from `config.yaml`, adding one
   `--exclude-task-name <id>` per task id that
   `scripts/resolve_exclude_tasks.py` resolves from
   `runtime_info.input.env_extra.HARBOR_EXCLUDE_TASKS`;
5. if `runtime_info.input.sft_conversion.enabled: true`, run
   `scripts/convert_trajectories.sh --job "$JOB_NAME"` after Harbor exits.

With `runtime_info.input.harbor_job.samples_per_task >= 2` (best-of-n
campaign), steps 3-5 repeat as k sequential sample jobs named
`<job>-s1` … `<job>-s<k>`, each with its own proxy and conversion, and with
processed-task (ledger) exclusion suppressed so every sample re-runs the staged
set — `excluded_tasks.txt` ids are still honored. Samples never run
concurrently: the readiness probe accepts any listener on the proxy port.

Job output lands under `runtime_info.input.harbor_job.jobs_dir` =
`artifacts/jobs/<job>/`, with per-task logs at
`artifacts/jobs/<job>/<task>/agent/litellm-trajectory.jsonl`.

## Step 3b — Multi-scaffold campaign mode

When `runtime_info.input.agent.scaffolds` is set (default `null` = single-agent
mode, everything above unchanged), `start.sh` validates every entry up front —
supported names only, no silent claude-code fall-through — then runs one
**sequential** Harbor job per scaffold over the same staged batch, each with
its own LiteLLM proxy lifecycle (shared port, so never concurrent), its own
`--scaffold` for conversion, and `HARBOR_LEDGER_EXCLUDE=0` so every scaffold
re-runs the whole batch (`excluded_tasks.txt` still applies). Job names are
distinct per scaffold via name+version. `command_override` and
`harbor_job.job_name_prefix` are refused in this mode.

The campaign summary prints the merge command at the end; run it to produce the
campaign dataset:

```bash
python3 scripts/merge_sft_data.py --jobs <job1>,<job2>,... [--per-task-cap N]
```

Trainer consumes the merged file by pointing its config at it
(`trainer.source.type: local_lf`, `source.lf_path` →
`artifacts/sft_data/merged/lf.json`) — no trainer-side change is needed.

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

Terminal Curator requires `verifiable_tasks.txt`; other local sources filter by
it when present and preserve the valid-directory fallback when absent. Local
staging holds task links; terminal publication paths are immutable, while
ordinary SWE/external contents remain mutable links. HF staging copies files.

The launcher excludes ledger entries but does not write outcomes itself. After
every job, maintain the ledger to avoid accidental repeated work:

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

**Best-of-n campaign bookkeeping** (`samples_per_task >= 2`): bookkeep per
sample job, not once per task. Append one ledger entry per sample job per task
— same flat `runs:` shape, with `trajectory_path` pointing at that sample job's
trajectory (`artifacts/jobs/<job>-s<i>/<task>/agent/litellm-trajectory.jsonl`)
and `note` recording the sample index (e.g. `sample 2/3`). Because every sample
re-runs the same tasks, the ledger ends up with k entries per task; that is
expected. After all sample jobs are converted, produce the campaign's final
dataset by selecting the best records per task:

```bash
python3 scripts/select_trajectories.py --jobs <job>-s1,<job>-s2,<job>-s3 --per-task-cap 2
```

If `sft_conversion.out_dir` is not the default `artifacts/sft_data`, the sample
jobs live elsewhere — pass `--sft-root <out_dir>` so they resolve.

The merged dataset lands in `artifacts/sft_data/selected/` (`lf.json` plus
`lf.stats.json` and `selection_manifest.json`); that directory — not any single
sample's — is the campaign's handoff to Trainer.

**Multi-scaffold campaign bookkeeping** (`agent.scaffolds` set): bookkeep per
scaffold job. Append one flat ledger entry per task per scaffold job, each with
the `trajectory_path` under that job's `artifacts/jobs/<job>/…` directory (the
ledger is task-keyed, so same task across scaffold jobs = multiple entries with
different `trajectory_path` values — that is expected, and it is why campaign
jobs launch with `HARBOR_LEDGER_EXCLUDE=0`). After the last job, merge the
per-scaffold SFT datasets to produce the campaign dataset:

```bash
python3 scripts/merge_sft_data.py --jobs <job1>,<job2>,<job3>
```

Then record in the run notes which jobs were merged and the
`per_scaffold_counts` from `artifacts/sft_data/merged/merge_manifest.json`.

## Step 7 — Optional dashboard/SFT refresh

- SFT data and stats: `/tracer:dashboard` covers
  `scripts/convert_trajectories.sh` for one-off conversion or
  `--skip-unchanged` refresh.
- Progress board: `/tracer:dashboard` covers local preview and Cloudflare
  Pages sync.
