# OpenLux Codex API Diagnosis

## Purpose

This document records the API requirements and the diagnostic procedure used
to verify OpenLux compatibility with Codex CLI and LegoFlow.

## Required configuration

```text
Base URL: https://api.openlux.ai/v1
Model: gpt-5.6-sol
Credential variable: OPENAI_API_KEY
Wire API: responses
```

Set the key only in the environment or a secret manager. Never store it in a
tracked file, shell transcript, or issue comment.

## Codex requirements

The selected key and base URL must support:

1. `GET /v1/models` with HTTP 200.
2. `POST /v1/responses` with Codex-style input and completed output text.
3. Continuation and tool-call behavior sufficient for agent execution.

`POST /v1/chat/completions` is useful for the Claude Code path but is not by
itself evidence that Codex is compatible.

## Reproduction

```bash
export OPENAI_API_KEY="<operator-provided-key>"
export OPENAI_BASE_URL="https://api.openlux.ai/v1"

curl -i "$OPENAI_BASE_URL/models" \
  -H "Authorization: Bearer $OPENAI_API_KEY"

curl -i "$OPENAI_BASE_URL/responses" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-sol","input":"Reply exactly OK.","max_output_tokens":16,"store":false}'

codex exec --ephemeral --dangerously-bypass-approvals-and-sandbox \
  -m gpt-5.6-sol 'Reply exactly OK. Do not modify files.'
```

## Diagnosis

The latest valid configuration passed the endpoint checks and Codex CLI
request. Earlier failures were caused by invalid or rate-limited credentials,
not by the Codex request shape. The repository therefore documents the
protocol and secret-handling requirements without embedding a credential.
