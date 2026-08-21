# OpenLux and Codex Compatibility Test Report

## Environment

- Codex CLI: `0.140.0`.
- Base URL: `https://api.openlux.ai/v1`.
- Model: `gpt-5.6-sol`.
- Credentials: supplied through the environment and omitted from this report.

## API results

- `GET /v1/models`: passed.
- `POST /v1/responses`: passed.
- `POST /v1/chat/completions`: passed for the Claude-compatible path.

## LegoFlow plugin results

All 24 root and block Codex skills passed live invocation tests:

- Root: `check`, `setup`, `create`, `dashboard`, `run`.
- Curator: `check`, `setup`, `collect-prs`, `create-tasks`, `dashboard`, `run`.
- Tracer: `check`, `setup`, `dashboard`, `run`.
- Trainer: `check`, `setup`, `dashboard`, `run`.
- Evaluator: `check`, `setup`, `dashboard`, `run`.

The test harness prevented long-running production workflows and required each
skill to read and follow its canonical Claude Code skill. No tracked files were
modified by the live skill tests.
