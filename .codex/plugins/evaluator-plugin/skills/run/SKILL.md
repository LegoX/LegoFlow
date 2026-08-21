---
name: run
description: Run an approved benchmark evaluation and inspect verifier results.
---

# Run Evaluator

Complete the evaluator check and wait for confirmation. Run `./bin/legoflow run evaluator` in a named tmux session. Inspect the result summary, per-task verifier outputs, trajectory logs, and archive metadata; report partial jobs as partial rather than successful.
## LegoFlow Command Convention

The canonical command for this skill is `/evaluator:run`. The shared CLI accepts the same command as `./bin/legoflow /evaluator:run` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
