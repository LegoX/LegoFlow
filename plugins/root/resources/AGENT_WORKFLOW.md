# LegoFlow Agent Workflow

Codex is an orchestration surface for LegoFlow, not a replacement for the block runtimes. Read the root `AGENTS.md`, the relevant block `AGENTS.md`, and that block's `config.yaml` before acting.

## Operating contract

1. Inspect the repository, submodules, configuration, dependencies, and current artifacts.
2. Run the applicable `scripts/dryrun.sh` or block `check` skill.
3. Report the configuration, resource requirements, credentials required, and expected outputs.
4. Wait for explicit confirmation before setup, rollout, training, evaluation, or destructive cleanup.
5. Run the existing block script in a named tmux session when the operation is long-running.
6. Inspect artifacts and archive metadata after completion; do not infer success from a process exit code alone.
7. For iterative experiments, preserve prior artifacts and record the hypothesis, change, result, and next action.

## Configuration

The optional root `runtime_info.input.agent` section selects the orchestration agent. `runtime: codex` means the user is driving the workflow with Codex. It does not change Harbor's rollout `agent.name` fields, which remain block-specific and must be supported by the checked-out submodule.

Credentials are read from environment variables. Never print, commit, or place tokens in YAML, logs, prompts, or artifacts.
