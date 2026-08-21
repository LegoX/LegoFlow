---
name: dashboard
description: Inspect benchmark accuracy, task slices, and evaluation artifacts.
---

# Inspect Evaluator

Use `./bin/legoflow dashboard evaluator` and analysis scripts read-only. Summarize overall accuracy, resolved/unresolved tasks, tags, repositories, failure modes, model endpoint, and comparison to prior runs. Ask for confirmation before publishing results.
## LegoFlow Command Convention

The canonical command for this skill is `/evaluator:dashboard`. The shared CLI accepts the same command as `./bin/legoflow /evaluator:dashboard` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
