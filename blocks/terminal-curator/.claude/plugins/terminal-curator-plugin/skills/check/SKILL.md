---
name: check
description: Preflight LegoFlow Terminal Curator configuration, bounded source inputs, repository pins, generation dependencies, Harbor, and Docker without generating tasks. Use before terminal-curator runs or to diagnose readiness.
---

# /terminal-curator:check

Read `blocks/terminal-curator/CLAUDE.md` and the selected config. Work from
that block directory on the configured execution host. Preserve any
`--config <file>` selection throughout the check and subsequent run.

Run `bash scripts/dryrun.sh` with the overlay option if selected. It runs
the shared block schema/dependency validator and the adapter's readiness
checks. Do not install environments, update pins, generate tasks, launch
containers, or edit config while checking.

Inspect and report all failures together:

- source mode, existing input path, tags/IDs, finite question/page cap,
  positive finite timeouts/concurrency, and the nonnegative extra-retry budget;
- Terminal-Lego checkout and pinned SHA, clean vendored state;
- source-dependent LLM inputs, block environment, Tracer's pinned Harbor
  environment, and Docker daemon reachability;
- output pool/manifest path and the conditional Tracer producer/consumer
  edge. The report may not overwrite input data or lifecycle paths; outputs
  cannot overlap or write into immutable versions, and `eval_registry.path`
  must equal the task pool plus `/registry.json`. Missing output artifacts
  before the first run are normal;
- an already-running terminal-curator worker or a conflicting batch.

For a requested live service check, run
`artifacts/env/terminal-curator/bin/python scripts/terminal_curator.py --config <file> probe` using the block
environment. It may contact the configured Stack Exchange/LLM services and
incur a minimal completion cost; it does not start a curation batch. Report
the actual probe status, including invalid API key/quota/network failures.
Do not expose response text that contains a credential or signed query URL.

Conclude with READY / BLOCKED, the source, limits, host, model (for generation),
workers, validation concurrency, output path, and concrete next actions for
each failure. A mode `tasks` check does not require a generation API key.
Never describe a skipped live probe as passed or a successful `/models`
response as proof that generation completions work.

Before `:run`, present the check summary and confirm the user has authorized
this source/host/budget. Reuse explicit authorization already provided for
the same finite run; clarify unresolved scope before a new or expanded run.
