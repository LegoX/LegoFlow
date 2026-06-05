---
name: run
description: >
  Launch the swegen pipeline via `scripts/start.sh` after preflight
  passes: PR collection from GitHub → LLM-driven task generation per
  language (`swegen create`) → NOP/Oracle verification → append to
  `verifiable_tasks.txt`. Per-language launches use
  `bash scripts/create_<lang>.sh` (tuned `--timeout`, `--cc-timeout`,
  `--n-concurrent`, `--state-dir`). Long-running (hours per language).
  For a first-time end-to-end smoke before committing to a full run,
  this skill can drive a 10-PR `--max-pr 1` flow that produces a single
  verified task ID and exits. Stamps live state into `artifacts/index.yaml` via
  `scripts/archive_run.sh`. Triggers on phrases like "run swegen",
  "launch swegen", "generate tasks", "kick off PR collection",
  "start the swegen pipeline", "smoke-test swegen".
---

# /swegen:run

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:run` is the preflight-then-execute
entry point. Always run `/swegen:check` first; require explicit user
confirmation before launching. swegen is a **leaf block** — no
subblocks, so this skill runs `scripts/start.sh` directly (per
BLOCK_DEFINITION.md §3.2, no child dispatch applies).

## Mode selection

Args are free-form natural language. Resolve to one of three modes:

| Mode | When chosen | What runs | Wall time |
|---|---|---|---|
| `smoke`  | First run after `:setup`, or args mention "smoke", "quick verify", "10 PRs", "one task". | A single-task smoke: `swegen create --max-pr 1 --n-concurrent 1 --no-require-issue --min-source-files 1`, against `artifacts/collected_prs/python_pr_ids.txt`. Success = one ID written to `artifacts/swe_tasks/py-cc/verifiable_tasks.txt`. | ~10 min |
| `single-language` | Args name a language (e.g. "run swegen for python", "generate go tasks"). | `bash scripts/create_<lang>.sh` for that language only. | hours |
| `full` | Args empty or say "everything" / "all languages". | `bash scripts/start.sh`, which dispatches every language configured under `runtime_info.input.languages.*.enabled`. | many hours |

If args are ambiguous (e.g. "run swegen with 32 tasks"), propose a
specific mode + parameter mutation, confirm, then execute.

## Procedure

1. **Preflight** — invoke `/swegen:check`. For `smoke` mode, the user
   may want the Harbor smoke (step 6 of `:check`) enabled by default.
   Abort the run on any FAIL.
2. **Confirm** — print the run-config summary from `:check` plus the
   chosen mode and the per-language tuned params from
   `scripts/create_<lang>.sh`. Wait for explicit user `yes`.
3. **Launch** — execute the per-mode command (table above). All modes
   should be backgrounded by default (runs are long; LLM API costs
   accumulate on confirmed errors); ask the user once unless they
   already said "foreground" or this is `smoke` mode (smoke is short
   enough to foreground).
4. **Archive** — `start.sh` (and `scripts/create_<lang>.sh`) install the
   EXIT trap that invokes `archive_run.sh`, which appends to
   `artifacts/index.yaml` with `status: completed | failed |
   interrupted`. Do not write to `index.yaml` from this skill.

## Conventions to honour

- **State directory.** Use an **absolute** `--state-dir` under
  `artifacts/` (e.g. `--state-dir "$PWD/artifacts/.swegen-<lang>"`),
  not a relative path under `scripts/`. Relative paths put
  `harbor-jobs/` in the wrong place; the artifacts/ convention keeps
  archive snapshots self-contained.
- **`--docker-prune-batch 0`** during smoke so a tight loop doesn't
  thrash Docker; the tuned `scripts/create_<lang>.sh` may override.
- **`--no-require-issue`** is the default expectation for swegen
  task generation — don't drop it unless the user explicitly wants
  issue-only PRs.

## TODO

- [ ] Spec per-language targeting precisely (which subset of
      `meta_info.languages` becomes `--languages` arg).
- [ ] Decide whether `:run` should split into substeps
      (`/swegen:collect-prs`, `/swegen:generate-tasks`,
      `/swegen:verify`) following the trajgen pattern. Probably
      yes for the `full` mode (each substep is a natural
      checkpoint); definitely no for `smoke`.
- [ ] Spec the resume-on-interrupt behaviour. `swegen create` uses
      `.swegen-create-batch/` for resume; `:run` should detect a
      prior interrupted run and offer to resume vs. start fresh.
