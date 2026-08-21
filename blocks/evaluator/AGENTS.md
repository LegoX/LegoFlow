# Evaluator Agent Instructions

This block is maintained by LegoX as part of LegoFlow.

Read the repository `AGENTS.md`, this block's `CLAUDE.md`, `config.yaml`, and the root block definition before acting. Use the evaluator check/setup/run/dashboard contracts and keep benchmark outputs under `artifacts/`.

Confirm the benchmark, task count, model endpoint, credentials, and resource requirements before launching an evaluation. Never treat a partial Harbor job as a successful evaluation; inspect its result summary and per-task verifier outputs.

Use the shared commands `/evaluator:check`, `/evaluator:setup`, `/evaluator:run`, and `/evaluator:dashboard`. Claude Code and Codex use the same command names; the executable fallback is `./bin/legoflow /evaluator:check`.
