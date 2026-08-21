---
name: dashboard
description: Inspect training logs, checkpoints, metrics, and experiment comparisons.
---

# Inspect Trainer

Use `./bin/legoflow dashboard trainer` in read-only mode. Summarize dataset size, loss curves, throughput, checkpoint status, validation signals, and comparison against the configured baseline. Do not delete or overwrite checkpoints.
## LegoFlow Command Convention

The canonical command for this skill is `/trainer:dashboard`. The shared CLI accepts the same command as `./bin/legoflow /trainer:dashboard` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
