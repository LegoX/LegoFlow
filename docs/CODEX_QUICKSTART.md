# LegoFlow Codex Quickstart

Read this file before starting a Codex session.

## Configure the API

Use operator-managed secrets. Do not commit credentials:

```bash
export OPENAI_API_KEY="<operator-provided-key>"
export OPENAI_BASE_URL="https://api.openlux.ai/v1"
```

The validated configuration uses model `gpt-5.6-sol`, provider `openlux`, and
the `responses` wire API. The API key must be available to the Codex process.

## Project layout

```text
blocks/curator/    GitHub PR to SWE task curation
blocks/tracer/     Agent trajectory generation
blocks/trainer/    Model training
blocks/evaluator/  Evaluation
```

Each block has a canonical `CLAUDE.md`, a forwarding `AGENTS.md`, and a Codex
plugin whose skills reference the canonical Claude Code skills.

## Unified commands

Use the same command names in Claude Code and Codex:

```text
/root:check
/curator:check
/curator:create-tasks
/tracer:run
/trainer:run
/evaluator:run
```

Codex skills are thin wrappers. They must read and follow the corresponding
`.claude/plugins/<plugin>/skills/<skill>/SKILL.md` in full.

## Safety workflow

Use `check -> confirm -> run`. Run a check first, report its result, and wait
for explicit user confirmation before launching expensive or stateful work.

## API compatibility requirements

Codex requires all of the following with the same credentials:

1. `GET /v1/models` returns HTTP 200.
2. `POST /v1/responses` accepts Codex input and returns usable output text.
3. The selected model supports continuation and tool calls sufficiently for
   agent execution.

`/v1/chat/completions` compatibility alone is not sufficient for Codex.

## Safe smoke command

```bash
codex exec --ephemeral --dangerously-bypass-approvals-and-sandbox \
  -m gpt-5.6-sol \
  'Reply exactly OK. Do not modify files.'
```
