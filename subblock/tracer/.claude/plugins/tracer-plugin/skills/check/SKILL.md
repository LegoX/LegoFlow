---
name: check
description: >
  Preflight the tracer block. Validates config.yaml schema; verifies
  Harbor / swe_data_process pinned commits match (and accepts tracked
  submodule status, not just gitignore); confirms the three uv/venv
  environments exist with the expected editable installs; live-probes
  the configured LLM endpoint with `GET /models` and (when
  `task_source.provider: huggingface`) probes HF dataset reachability;
  confirms LiteLLM proxy port is free or held by current user; verifies
  the agent runtime_image is present on the local Docker daemon; sanity-
  checks `artifacts/consumption_ledger.yaml` and cross-checks every
  done/failed/skipped entry against `HARBOR_EXCLUDE_TASKS`. Read-only.
  Reports all failures in one pass. Triggers on phrases like "check
  tracer", "preflight tracer", "is tracer ready", "diagnose tracer",
  "validate tracer config".
---

# /tracer:check

Read-only preflight. Wraps `scripts/dryrun.sh` and adds the few
contextual checks that need conversational nuance (e.g. interpreting CF-
gating 401s, flagging stale memories, deciding whether a WARN is worth
escalating).

## How to run

1. Execute `bash scripts/dryrun.sh` from the block root. The script is
   the single source of truth for what "tracer is ready" means — it
   covers schema, repos/commits, all three envs, LLM endpoint live
   probe, HF dataset auth probe, port-4001 ownership, Docker image
   presence, ledger validity, and the ledger↔`HARBOR_EXCLUDE_TASKS`
   cross-check.
2. Report PASS / WARN / FAIL counts and a structured summary back to
   the user. Use the dryrun's section headings.

## Interpreting results

- **LLM endpoint 401 from this shell**: when the configured base URL is
  `llm10.jierungogogo.com` (or similar CF-gated production endpoint),
  `dummy-key` is the real production key and the 401 is a network
  artifact specific to Claude Code's sandboxed shell — see memory
  `project-swegen-llm-endpoint`. Dryrun downgrades 401/403 to WARN for
  this reason. If the user is targeting that endpoint, suggest probing
  from a non-sandboxed shell on the same host (`ssh haoli@<host>` then
  `curl …`) to confirm.

- **HF dataset 401/403**: real auth failure. Either the user needs to
  paste an `hf_…` token (write to `~/.cache/huggingface/token`, mode
  600), or the dataset id is wrong, or their token doesn't grant
  access to that gated repo. Resolve before `prepare_tasks.sh` runs.

- **Port 4001 occupied by another user**: real FAIL — `start.sh` will
  fail to bind. There is a long-running root-owned LiteLLM on
  `:4002` on shared hosts; that's normal. Only `:4001` matters for
  tracer.

- **Agent runtime_image not pulled**: WARN, not FAIL. Harbor will
  `docker pull` it at first task. Offer to pre-pull
  (`docker pull <image>`) so launch latency is predictable and any
  registry-auth surprises surface now.

- **ledger leak**: every entry with `status: done|failed|skipped` MUST
  also appear in `environment.extra.HARBOR_EXCLUDE_TASKS`. If dryrun
  reports leaks, Harbor will re-run them — fix the exclude list
  before running.

## When to escalate to FAIL vs WARN

Dryrun's classification is intentional. Do not "promote" warnings to
failures unless the user asks. The four current real-world warnings:

| WARN | Meaning |
|---|---|
| `llm_api.input_cost_per_token / output_cost_per_token empty` | Billing accounting only. |
| `agent runtime_image not pulled locally` | First task pays the pull cost. |
| `HF dataset network error` | Likely transient; retry once. |
| LLM endpoint 401/403 from this shell | Likely CF-gating artifact (see memory). |

## Mandatory before `/tracer:run`

Per the root `CLAUDE.md` "check → confirm → run" workflow, this skill
must run AND the user must explicitly confirm before any
`/tracer:run` invocation. Never auto-launch.

## Out of scope

- Long-running side effects: no `docker pull`, no `prepare_tasks.sh`,
  no `git clone`. All checks are < 30 s in aggregate.
- Fixing failures: this skill only diagnoses. Setup fixes belong in
  `/tracer:setup`.
