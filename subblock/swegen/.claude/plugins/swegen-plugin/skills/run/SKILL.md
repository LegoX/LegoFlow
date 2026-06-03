---
name: run
description: >
  Launch the swegen pipeline via `scripts/start.sh` after preflight passes:
  PR collection from GitHub → LLM-driven task generation per language →
  NOP/Oracle verification → write `verifiable_tasks.txt`. Long-running
  (hours per language); supports per-language targeting and concurrent
  language workers. Stamps live state into `artifacts/index.yaml` via
  `scripts/archive_run.sh`. Triggers on phrases like "run swegen",
  "launch swegen", "generate tasks", "kick off PR collection",
  "start the swegen pipeline".
---

# /swegen:run

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:run` is the preflight-then-execute
entry point. Always run `/swegen:check` first and require explicit user
confirmation before launching.

## Intent

1. **Preflight** — invoke `/swegen:check`; abort on any failure.
2. **Confirm** — present run configuration summary (languages, pr_limit,
   target_count, parallelism); wait for explicit user `yes`.
3. **Launch** — `bash scripts/start.sh` locally. Background is preferred
   since runs are long; ask the user once.
4. **Archive** — `scripts/start.sh`'s EXIT trap calls `archive_run.sh`,
   which appends to `artifacts/index.yaml`.

## TODO

- [ ] Spec per-language targeting (run only python, run only go, etc.).
- [ ] Decide whether to split `:run` into substeps (`:collect-prs`,
      `:generate-tasks`, `:verify`) following the trajgen pattern from the
      plugin guidelines.
