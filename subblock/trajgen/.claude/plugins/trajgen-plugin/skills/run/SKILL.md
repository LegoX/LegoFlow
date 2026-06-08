---
name: run
description: >
  Launch the trajgen pipeline via `scripts/start.sh` after preflight passes:
  `prepare_tasks.sh` filters swegen's `verifiable_tasks.txt` into
  `artifacts/tasks/<dataset>/`, then Harbor runs trajectories per task with
  the per-job LiteLLM proxy, then (if `sft_conversion.enabled: true`)
  `convert_trajectories.sh` produces LF-format SFT JSON under
  `artifacts/sft_data/<job>/lf.json`. Long-running. Triggers on phrases
  like "run trajgen", "launch trajgen", "generate trajectories",
  "kick off the harbor jobs", "start the trajgen pipeline".
---

# /trajgen:run

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:run` is the preflight-then-execute
entry point. The guidelines explicitly call out trajgen as a case where
`:run` may split into substeps — design here is the orchestrator.

## Intent

1. **Preflight** — invoke `/trajgen:check`; abort on any failure.
2. **Confirm** — print run config summary (task_source dataset, harbor
   concurrency, agent name + image, sft_conversion mode); wait for `yes`.
3. **Launch** — `bash scripts/start.sh` (background by default for long
   runs). Internally this runs:
   - `scripts/prepare_tasks.sh` (manifest-filtered task copy)
   - harbor job (multiple workers, per-task LiteLLM trajectory logging)
   - `scripts/convert_trajectories.sh` (only if `sft_conversion.enabled`)
4. **Archive** — handled by `scripts/archive_run.sh` via EXIT trap.

## TODO

- [ ] Split into `/trajgen:rollout` (harbor only) and `/trajgen:convert-sft`
      (post-process only) per the plugin guidelines; keep `:run` as the
      orchestrator that calls both.
- [ ] Surface partial completion when only some tasks finish.
