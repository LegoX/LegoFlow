---
name: dashboard
description: Inspect tracer jobs, trajectory quality, and converted training data.
---

# Inspect Tracer

Use `./bin/legoflow dashboard tracer` and read-only analysis scripts. Summarize job completion, task pass rates, trajectory quality, reasoning checks, scaffold/model slices, conversion counts, and failed tasks. Ask for confirmation before publishing a dashboard.
## LegoFlow Command Convention

The canonical command for this skill is `/tracer:dashboard`. The shared CLI accepts the same command as `./bin/legoflow /tracer:dashboard` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
