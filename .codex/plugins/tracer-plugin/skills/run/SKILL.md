---
name: legoflow-tracer-run
description: Generate approved coding-agent trajectories from verified LegoFlow tasks.
---

# Run Tracer

Complete the tracer check and wait for confirmation. Run `blocks/tracer/scripts/start.sh` in a named tmux session. The configured `runtime_info.input.agent.name` selects Harbor's rollout scaffold; Codex remains the orchestrator unless Harbor explicitly provides a Codex scaffold. Inspect raw trajectories, conversion output, and archive metadata after the job.
