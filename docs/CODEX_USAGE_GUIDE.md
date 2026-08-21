# LegoFlow Codex Usage Guide

## Configuration

```bash
export OPENAI_API_KEY="<operator-provided-key>"
export OPENAI_BASE_URL="https://api.openlux.ai/v1"
```

The validated Codex provider configuration is:

```toml
model_provider = "openlux"
model = "gpt-5.6-sol"

[model_providers.openlux]
name = "OpenLux OpenAI-Compatible API"
base_url = "https://api.openlux.ai/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
supports_websockets = false
```

## Plugin contract

Codex and Claude Code use the same LegoFlow command vocabulary. For example,
`/curator:check` resolves to the Codex wrapper and then follows the canonical
Claude skill at `.claude/plugins/curator-plugin/skills/check/SKILL.md`.

The same pattern applies to every root and block skill. `AGENTS.md` files
directly reference their corresponding `CLAUDE.md` files to avoid duplicated
agent instructions.

## Command groups

- Root: `/root:check`, `/root:setup`, `/root:create`, `/root:dashboard`, `/root:run`.
- Curator: `/curator:check`, `/curator:setup`, `/curator:collect-prs`,
  `/curator:create-tasks`, `/curator:dashboard`, `/curator:run`.
- Tracer, trainer, and evaluator: `check`, `setup`, `dashboard`, and `run`.

Run checks before stateful commands and obtain explicit confirmation before
long-running workflows.

## Troubleshooting

- HTTP 401: refresh the operator-managed key and verify the environment seen by
  the Codex process.
- HTTP 429: retry with appropriate rate limits or a permitted model/provider.
- Metadata warnings: verify that the model is listed by `/v1/models`; a missing
  Codex metadata entry may reduce defaults without proving endpoint failure.
- Plugin discovery: verify `.codex/plugins/` and the wrapper's canonical Claude
  skill path.
