# Curator Agent Instructions

This block is maintained by LegoX as part of LegoFlow.

Read the repository `AGENTS.md`, this block's `CLAUDE.md`, `config.yaml`, and the root block definition before acting. Use `scripts/dryrun.sh` for validation and the documented curator scripts for collection and task creation.

The curator uses `GITHUB_TOKEN` for GitHub access. Keep credentials in the environment, preserve collected data, and wait for confirmation before PR collection or task creation. Use `scripts/clean.sh` only for explicitly approved cleanup.

Use the shared commands `/curator:check`, `/curator:setup`, `/curator:collect-prs`, `/curator:create-tasks`, `/curator:dashboard`, and `/curator:run`. Claude Code and Codex use the same command names; the executable fallback is `./bin/legoflow /curator:check`.
