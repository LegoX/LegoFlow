# Tracer Agent Instructions

This block is maintained by LegoX as part of LegoFlow.

Read the repository `AGENTS.md`, this block's `CLAUDE.md`, `config.yaml`, and the root block definition before acting. Use the tracer check/setup/run/dashboard contracts and keep Harbor, LiteLLM, and trajectory artifacts under the configured artifact directories.

The tracer's `runtime_info.input.agent` selects the Harbor rollout scaffold. Do not replace supported Harbor agents with `codex` unless the checked-out Harbor submodule explicitly provides that agent. Codex is the orchestration agent by default.
