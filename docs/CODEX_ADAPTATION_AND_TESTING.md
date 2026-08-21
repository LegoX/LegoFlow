# Codex Adaptation and Validation

This document summarizes LegoFlow's Codex integration and the validation
performed for pull request #105.

## Design principles

- Claude Code remains the canonical source for workflow semantics.
- Codex skills are intentionally thin wrappers rather than a second workflow
  implementation.
- Claude Code and Codex use the same LegoFlow slash-command vocabulary.
- Agent instructions are maintained once, in `CLAUDE.md`.
- `AGENTS.md` files directly reference the corresponding `CLAUDE.md` so that
  Codex receives the same project contract without duplicated instructions.

## Codex plugin layout

The root plugin is located at `.codex/plugins/root-plugin/`. Block plugins are
located at `.codex/plugins/<block>-plugin/` for `curator`, `tracer`, `trainer`,
and `evaluator`.

Each plugin contains a `.codex-plugin/plugin.json` manifest and skill
directories. Every Codex `SKILL.md` identifies and loads the matching canonical
Claude skill under `.claude/plugins/` and requires the agent to follow it in
full, including safety gates, configuration checks, execution steps, and
reporting requirements.

The synchronization helper is
`scripts/sync_codex_claude_contracts.py`. It can regenerate the thin wrappers
after canonical Claude skills change.

## Unified invocation

The same command names are used by Claude Code and Codex:

```text
/root:check
/root:setup
/root:create
/root:dashboard
/root:run

/curator:check
/curator:setup
/curator:collect-prs
/curator:create-tasks
/curator:dashboard
/curator:run

/tracer:check       /tracer:setup       /tracer:dashboard       /tracer:run
/trainer:check      /trainer:setup      /trainer:dashboard      /trainer:run
/evaluator:check    /evaluator:setup    /evaluator:dashboard    /evaluator:run
```

The supported operational sequence remains `check -> confirm -> run`. Codex
must not bypass the canonical confirmation gate before expensive or stateful
workflows.

## Validation method

The live test used Codex CLI with the OpenLux OpenAI-compatible endpoint:

```text
Base URL: https://api.openlux.ai/v1
Model: gpt-5.6-sol
Wire API: responses
Credential: supplied through OPENAI_API_KEY, never committed
```

Each plugin was invoked in an ephemeral Codex process. The test prompt
required the agent to read the wrapper, follow the referenced Claude skill,
execute safe checks, and avoid modifying tracked files. Long-running `run`
workflows were exercised through their safety-gate and dry-run behavior rather
than launched as production jobs.

## Live results

All 24 Codex skills passed:

| Plugin | Skills | Result |
| --- | --- | --- |
| Root | `check`, `setup`, `create`, `dashboard`, `run` | 5/5 passed |
| Curator | `check`, `setup`, `collect-prs`, `create-tasks`, `dashboard`, `run` | 6/6 passed |
| Tracer | `check`, `setup`, `dashboard`, `run` | 4/4 passed |
| Trainer | `check`, `setup`, `dashboard`, `run` | 4/4 passed |
| Evaluator | `check`, `setup`, `dashboard`, `run` | 4/4 passed |

Additional validation passed:

- `tests/run.sh`: 15/15 test cases passed.
- `tests/cases/13_codex_compatibility.sh`: passed.
- `tests/cases/14_legoflow_cli.sh`: passed.
- `git diff --check`: passed.
- Repository credential scan: no committed API keys or GitHub tokens.

## Maintenance contract

When a Claude skill changes, update the canonical `.claude` skill first, then
run `scripts/sync_codex_claude_contracts.py` and the Codex compatibility tests.
Do not fork the workflow logic into Codex-only instructions. This keeps Claude
Code and Codex behavior aligned while allowing both agents to invoke the same
LegoFlow commands.
