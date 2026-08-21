---
name: run
description: Generate approved coding-agent trajectories from verified LegoFlow tasks.
---

# Run Tracer

Complete the tracer check and wait for confirmation. Run `./bin/legoflow run tracer` in a named tmux session. The configured `runtime_info.input.agent.name` selects Harbor's rollout scaffold; Codex remains the orchestrator unless Harbor explicitly provides a Codex scaffold. Inspect raw trajectories, conversion output, and archive metadata after the job.
## LegoFlow Command Convention

The canonical command for this skill is `/tracer:run`. The shared CLI accepts the same command as `./bin/legoflow /tracer:run` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
