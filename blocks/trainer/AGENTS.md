# Trainer Agent Instructions

This block is maintained by LegoX as part of LegoFlow.

Read the repository `AGENTS.md`, this block's `CLAUDE.md`, `config.yaml`, and the root block definition before acting. Run the trainer dry run before setup or training and verify GPU, dataset, model, and DeepSpeed requirements.

Training is expensive and irreversible at scale. Present the resolved run configuration and wait for explicit confirmation before starting. Preserve checkpoints, logs, and WandB metadata.

Use the shared commands `/trainer:check`, `/trainer:setup`, `/trainer:run`, and `/trainer:dashboard`. Claude Code and Codex use the same command names; the executable fallback is `./bin/legoflow /trainer:check`.
