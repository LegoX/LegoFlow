# Terminal Curator implementation notes

This file records durable design decisions. Live progress and per-run results
are generated in `artifacts/state.json`, `artifacts/reports/latest.json`, and
`artifacts/index.yaml`.

- Terminal-Lego supplies the terminal-task generation logic as a pinned,
  read-only submodule. The LegoFlow adapter owns finite source selection,
  environment-based credentials, output isolation, Harbor normalization, and
  publication. The upstream infinite `run_pipeline.sh` is not the block entry.
- Oracle=1 and NOP=0 must come from independent fresh Harbor environments.
  The upstream validator's reward alone does not establish this contract.
- Only the published `artifacts/tasks/verifiable_tasks.txt` is handed to
  Tracer. Rejected/incomplete candidates retain their evidence outside that
  pool, and downstream rollout must not infer verification from directories.
- Published batches are immutable under `artifacts/tasks-versions/<id>/`;
  `artifacts/tasks` is an atomically updated symlink. Tracer resolves the
  version when staging, so republishing cannot change an old staged task.
  Default cleanup retains these batches, environments, reports/state, and
  archives; temporary run trees contain the raw source/candidate/job evidence.
- Harbor comes from Tracer's existing pinned checkout/environment. Keeping
  the same runtime avoids one validator accepting tasks a downstream rollout
  cannot load. Do not introduce a floating Harbor install for convenience.
- Root orchestration selects one local task producer from Tracer's config.
  Registering this block must not require terminal-generation credentials or
  launch terminal curation during an existing SWE workflow.
- Dashboard HTML is generated from the allowlisted live state and verified
  manifest. The board also projects safe archived-run summaries and contained
  verifier reward/JUnit evidence. Raw reports, notes and config are never
  embedded. An empty checkout displays no runs rather than inventing progress.

- Validation identity is the pinned Harbor `Task.checksum`, checked before and
  after each job and in the copied batch before publication. Keep it distinct
  from the block's whole-file `task_checksums` digest.
- Freeze the loaded redacted config at run start and archive by the exact
  runtime ID. Sequential archive IDs and runtime IDs are different namespaces;
  never choose a report by whichever `latest.json` happens to exist at EXIT.
- Generation failure is per candidate: record completion/failure counters and
  stage/status without discarding successful siblings. Retry counts mean extra
  retries, and structured approval requires boolean true plus an empty issues
  list; neither is a substitute for the agent's semantic audit.
- The verifier guard targets absolute Harbor `/solution` reference access,
  allowing legitimate task artifacts named solve.sh. It is a heuristic, not
  a proof against arbitrary obfuscation.
- Stop uses private PID/start identity and captured run ownership; cleanup
  holds both run and archive locks. None of that process metadata is public UI.
- The docs dashboard demo is deterministically rendered from a declared,
  explicitly synthetic fixture. Actual reviewed run snapshots are separate
  deliverables and must never overwrite that tracked demonstration.
- `validation.job_timeout_sec` bounds the entire Oracle or NOP job, including
  all queued candidates, environment setup and cleanup. It is not a per-task
  timeout. Normal runs retain completed per-task evidence even when a job times
  out or exits nonzero, record the job outcome in `job_status`, and automatically
  run fresh NOP only for the unchanged tasks satisfying the complete Oracle
  gate. Each phase keeps the configured timeout and concurrency. Partial NOP
  results can qualify only with complete paired evidence; unfinished or invalid
  results never count as zero reward. Interrupted jobs clean only their recorded
  Compose projects before continuation; cleanup/integrity failures still stop
  the run. The adapter publication does not replace the semantic handoff audit.
