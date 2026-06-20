---
name: dashboard
description: >
  Per-job status surface for evaluator: read `artifacts/jobs/<job>/` to count
  tasks pending / running / resolved / unresolved / failed; compute the
  aggregate resolution rate; show trajectories produced
  (`litellm-trajectory.jsonl` count) and the configured benchmark
  metadata (`dataset_name@version`, n_tasks_in_registry,
  agent.name@version). Textual table by default; optional webui later.
  Read-only. Triggers on phrases like "evaluator dashboard", "show evaluator
  progress", "swebench accuracy", "how many tasks resolved", "what evaluator
  jobs are running".
---

# /evaluator:dashboard

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:dashboard` is the per-block
"show me what's happening" surface.

## Intent

1. **Textual summary** (always): for each `artifacts/jobs/<job>/`, print
   a row with:
   - benchmark (`dataset_name@version`),
   - agent (`name@version`, runtime image),
   - total tasks, in-flight, resolved, unresolved, failed,
   - resolution rate (resolved / (resolved + unresolved)),
   - trajectories produced (count of
     `<task>/agent/litellm-trajectory.jsonl` files).
2. **Aggregate across runs** — for the same `(dataset_name, version,
   agent)` triple, print a roll-up across the matching entries in
   `artifacts/index.yaml`.
3. **Optional webui** (later): reuse Harbor's job-view UI if available,
   or build a lightweight aggregator scoped to evaluator.

## TODO

- [ ] Decide the resolved/unresolved derivation — does it come from
      `evaluation/result.json` per task, or from a Harbor-side roll-up?
- [ ] Spec how to handle in-flight runs in the resolution-rate
      denominator (currently excluded; should they be?).
