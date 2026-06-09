---
name: run
description: >
  Launch the eval pipeline via `scripts/start.sh` after preflight
  passes: generate the per-job LiteLLM config from
  `runtime_info.input.litellm_proxy`, start the LiteLLM proxy on the
  configured port, then run the Harbor job with
  `--dataset <name> --registry-path repos/harbor/registry.json` and any
  `--exclude-task-name` flags from `HARBOR_EXCLUDE_TASKS`. Long-running.
  Stamps live state into `artifacts/index.yaml` via
  `scripts/archive_run.sh`. Triggers on phrases like "run eval",
  "launch eval", "run swebench", "evaluate the model", "kick off the
  eval benchmark".
---

# /eval:run

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:run` is the preflight-then-execute
entry point. Always invoke `/eval:check` first; require explicit user
confirmation before launching. eval is a **leaf block** — no subblocks,
so this skill runs `scripts/start.sh` directly (per BLOCK_DEFINITION.md
§3.2, no child dispatch applies).

## Intent

1. **Preflight** — invoke `/eval:check`; abort on any FAIL.
2. **Confirm** — present the run-configuration summary from `:check`
   plus the benchmark `n_tasks` (post-`HARBOR_EXCLUDE_TASKS`), the
   `agent.runtime_image`, and the estimated wall time. Wait for
   explicit user `yes`.
3. **Launch** — `bash scripts/start.sh` (background by default for long
   runs; ask the user once). Per `meta_info.resources.ip`:
   - `local` / null / absent → execute on the current host;
   - real remote IP → open a local tmux window named `eval`, SSH to the
     remote, attach to (or create) a remote tmux session named `eval`,
     and run `start.sh` inside that remote session per
     BLOCK_DEFINITION.md §2.3. Confirm with the user whether the code is
     already in sync at `meta_info.resources.directory` or needs rsync.
4. **Archive** — `start.sh`'s EXIT trap calls
   `scripts/archive_run.sh`, which appends to `artifacts/index.yaml` with
   `status: completed | failed | interrupted` and snapshots config +
   scripts under `artifacts/archives/run_NNN/`. Do not write to
   `index.yaml` from this skill.

## Modes

| Mode | Trigger | What runs |
|---|---|---|
| `smoke` | Args mention "smoke" / "quick" / "n_tasks=N" for small N; or `runtime_info.input.harbor_job.n_tasks` is set to a small integer. | Standard `start.sh` flow, but Harbor honours the `n_tasks` cap. Use a `-100` benchmark subset for the cleanest smoke. |
| `full`  | Args empty or say "everything" / "all tasks". | `start.sh` against the full benchmark. |

## Conventions to honour

- **Benchmark selection lives in `config.yaml`, not flags.** To change
  benchmark, edit `runtime_info.input.task_source.{dataset_name,version}`
  and re-run `/eval:check`. `:run` should refuse to override these
  via free-form args — there is no scenario where launching a different
  benchmark than the one `:check` validated is correct.
- **Excluded tasks come from `HARBOR_EXCLUDE_TASKS`.** Do not pass
  exclusions via skill args; record them in `config.yaml`'s
  `environment.extra.HARBOR_EXCLUDE_TASKS` so they survive across runs
  and show up in archives.
- **One LiteLLM proxy per run.** If `:check` flagged the port as held
  by a foreign process, do not start a second proxy on a different
  port without the user explicitly OK-ing the config change.

## TODO

- [ ] Decide whether `:run` should split into substeps
      (`/eval:proxy`, `/eval:harbor`, `/eval:score`) following the
      trajgen pattern. Probably yes once Harbor's score aggregation
      stabilises; not yet.
- [ ] Spec resume-on-interrupt — Harbor's job state under
      `artifacts/jobs/<job>/` already supports it; `:run` should detect
      an interrupted prior run and offer to resume vs. start fresh.
