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
  manifest. Run history remains available in the separate run index. An empty checkout should display no runs rather than invent progress.
