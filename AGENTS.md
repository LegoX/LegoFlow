# LegoFlow Agent Instructions

You are operating in LegoFlow, an agent-native code-data engineering pipeline maintained by LegoX. During the repository rename, all new user-facing text, URLs, configuration, and documentation must use `LegoFlow` and `LegoX`.

## Before any action

- Read `config.yaml`, the relevant block `config.yaml`, and `.codex/plugins/root-plugin/resources/BLOCK_DEFINITION.md`.
- Read the relevant block's `AGENTS.md` before editing or running anything below that block.
- Inspect submodule status and do not modify submodule contents unless the task explicitly requires it.
- Treat `config.yaml` as a one-shot run specification; live state belongs in `artifacts/index.yaml`.

## Safety and execution

- Follow `check -> confirm -> run`. A check is read-only; setup, rollout, training, evaluation, and cleanup require explicit confirmation.
- Prefer the existing scripts and their documented contracts over ad-hoc commands.
- Use local execution by default and a named `tmux` session for long-running work.
- Keep credentials in environment variables such as `GITHUB_TOKEN`; never print or commit them.
- Preserve artifacts and previous experiment results. Do not use `--all` cleanup without explicit confirmation.

## Development

- Keep all repository content in English.
- Use `LegoX` for organization references and `LegoFlow` for the project name.
- Run the narrowest relevant tests first, then the root test suite.
- Do not change generated dashboards, submodule contents, or unrelated experiment files.
