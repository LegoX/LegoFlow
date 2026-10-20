---
name: run
description: Generate or import one finite terminal-task batch, validate fresh Oracle=1 and NOP=0 trials, audit instruction/test/reference consistency, and hand off the corrected verified manifest. Use for terminal-curator runs, not downstream rollout or training.
---

# /terminal-curator:run

Read `blocks/terminal-curator/CLAUDE.md` and the selected config. Execute
from this block's directory on its configured host, in a named tmux session
for a long run. Carry `--config <file>` through check, launch, and dashboard.

1. Run `/terminal-curator:check`. Resolve blocking failures before launching.
2. Show the resolved source/path/tags/IDs, finite caps, host, model, generation
   workers, extra-retry budget (`max_retries + 1` API attempts), validation
   concurrency/timeouts, output pool, and existing run
   state. Run only the scope the user authorized. If the source, host, or
   budget is unclear, clarify it first; obtain confirmation for a new or
   expanded large run. Existing explicit authorization for this finite run
   is sufficient and must not be asked for again.
3. Execute `bash scripts/start.sh`, adding the selected `--config <file>`.
   Do not run Terminal-Lego's open-ended `run_pipeline.sh` or its old
   validator as a substitute. Do not modify either pinned repository.
4. Monitor `artifacts/state.json`, logs, and validation jobs; inspect the
   completed `artifacts/reports/latest.json` afterward. Require
   both separate fresh-container trials: Oracle reward 1, NOP reward 0,
   completed Harbor results, no trial exception, and the exact Harbor task
   checksum recorded before/after both jobs and in the copied publication.
   Do not compare trial `task_checksum` to the separate block file digest.
   A timeout/missing reward
   is a rejected candidate, not a valid NOP baseline.
5. Audit every runtime-passing task before any downstream hand-off. Map each
   test expectation to the written instruction, and identify required behavior
   without a test. Check exact output text, log prefixes, filenames, flags,
   errors, and final filesystem state against the stated contract. Execute
   the requested program or command with representative inputs and check its
   outputs, exit status, and state changes. Source-text searches or the
   presence of an implementation token do not establish that behavior.
   Inspect the reference solution too: it must perform the requested work
   when Harbor runs `/solution/solve.sh` without arguments, including creating
   any executable the task asks the agent to provide.
6. Correct unstated test requirements, missing behavior checks, and an
   inaccurate reference solution in a mutable candidate copy under
   `artifacts/`. Preserve source attribution and record the requirement/test
   mapping, findings, changed files/reasons, behavioral checks, and reviewer
   in candidate `provenance.json` and local run notes. Do not edit an immutable
   published batch or either pinned repository. Re-import the corrected copy
   with `source.type: tasks` and run **both fresh Oracle and NOP trials again**.
   A correction is eligible for hand-off only after the semantic audit and
   those new trials pass; retain their actual run IDs and evidence. Keep the
   existing authorized batch scope and resource limits.
7. Inspect the final manifest, semantic-review record, and each task's fresh
   validation evidence. Structured test approval requires actual boolean
   `pass: true` and `issues: []`; a nonempty, missing or malformed issues list
   does not approve a candidate. The `/solution` reference-path guard allows
   legitimate task artifacts named solve.sh and is only a heuristic alongside
   behavioral review. A batch with no verified tasks must fail. Preserve
   rejected candidates/reasons; do not remove contract-required assertions,
   add failed IDs to the manifest, or silently change the source/model after
   observing failure.
8. Inspect the EXIT archive and newest `artifacts/index.yaml` entry. Check
   that its actual runtime `run_id` matches the per-run report, that its
   redacted config was frozen at run start, and that archival succeeded;
   `id: run_NNN` is only the sequential archive ID. Do not adopt a report from
   another attempt or summarize a stale `latest.json`. Report
   source count, generated/imported count, validation count, verified IDs,
   failures, semantic findings/corrections, report path, manifest path,
   archive path, and actual exit state.
   Describe incomplete results as incomplete.

Keep credentials out of commands, reports, dashboards, and commits. The archive
must contain the effective **redacted** config and repository SHAs. Source and
output data live under artifacts, never inside `repos/`.

The hand-off is `artifacts/tasks/verifiable_tasks.txt`. Explain how to select
`task_source.local_block: terminal-curator` and the terminal output path in
Tracer. The legacy implicit root shell chain refuses terminal-to-Tracer
execution to preserve the audit between these stages. Do not launch
Tracer, conversion, training, or evaluation unless the
user separately authorized those operations. To stop this block's run use
`bash scripts/stop.sh`; avoid broad process/container termination.

The same audited publication includes `registry.json` for Evaluator's
`terminal-curator@1.0` entry. Use the corrected batch's manifest and registry
together when describing either downstream hand-off.
