---
name: run
description: >
  Launch the sft training pipeline via `scripts/start.sh` after preflight
  passes: optional trajectory→LF conversion, LLaMA-Factory + DeepSpeed
  ZeRO-3 training on 8× GPU, WandB tracking, checkpoint save per
  `save_freq`. Long-running. Stamps live state into `artifacts/index.yaml`.
  Triggers on phrases like "run sft", "launch sft training",
  "start the sft block", "kick off the supervised fine-tuning",
  "fire off sft".
---

# /sft:run

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:run` is the preflight-then-execute
entry point.

## Intent

1. **Preflight** — invoke `/sft:check`; abort on any failure.
2. **Confirm** — print run config summary (model path, dataset, batch
   size, lr, epochs, GPU count, WandB project); wait for `yes`.
3. **Launch** — `bash scripts/start.sh` (background by default; ask once).
4. **Archive** — handled by `scripts/archive_run.sh` via EXIT trap; writes
   `runtime_info.output.{checkpoint_path, training_metrics, training_curves}`.

## TODO

- [ ] Decide whether `:run` should also call `/trajgen:run` upstream if
      `lf.json` is missing, or refuse and ask the user.
- [ ] Spec partial-checkpoint resume behaviour.
