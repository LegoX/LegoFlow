---
name: dashboard
description: >
  Training progress surface for sft: parse the latest training log under
  `artifacts/` for current step, loss, throughput; print the WandB run URL
  if configured; show the loss-curve plot path (`loss.png`) when produced.
  Textual table by default; optional embedded webui later. Read-only.
  Triggers on phrases like "sft dashboard", "show sft progress",
  "training loss", "where's the sft run", "wandb url for sft".
---

# /sft:dashboard

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:dashboard` is the per-block
"show me what's happening" surface.

## Intent

1. **Textual summary** (always): for the latest run in
   `artifacts/index.yaml`, parse the training log for current step / loss /
   throughput; print WandB run URL from `runtime_info.output.training_curves`.
2. **Artifacts links** — point at `train_results.json`, `loss.png`, the
   final checkpoint dir.
3. **Optional webui** (later): embed a small loss-curve renderer or link
   to the root dashboard.

## TODO

- [ ] Decide what "current" means when multiple runs are in
      `artifacts/index.yaml`.
