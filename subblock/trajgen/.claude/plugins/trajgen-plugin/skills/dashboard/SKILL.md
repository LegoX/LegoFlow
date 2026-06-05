---
name: dashboard
description: >
  Per-job status surface for trajgen: read `artifacts/jobs/<job>/` to count
  tasks pending / running / done / failed; show trajectories produced
  (`litellm-trajectory.jsonl` count); show SFT conversion progress
  (`artifacts/sft_data/<job>/lf.json` rows). Textual table by default;
  optional webui later. Read-only. Triggers on phrases like "trajgen
  dashboard", "show trajgen progress", "how many trajectories", "what
  jobs are running for trajgen".
---

# /trajgen:dashboard

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:dashboard` is the per-block
"show me what's happening" surface.

## Intent

1. **Textual summary** (always): for each `artifacts/jobs/<job>/`, print a
   row with: total tasks, in-flight, done, failed, trajectories produced,
   SFT rows converted (if `sft_data/<job>/lf.json` exists).
2. **Consumption ledger** — show the global pending / done / failed
   counts from `artifacts/consumption_ledger.yaml`.
3. **Optional webui** (later): reuse Harbor's job-view UI or build a
   lightweight aggregator.

## TODO

- [ ] Decide the JSONL counting strategy (read-all vs cached).
