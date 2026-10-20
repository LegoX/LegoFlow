---
name: dashboard
description: Inspect Terminal Curator's real generation progress, immutable task batches, Oracle/NOP evidence and archived runs in the Curator-style dashboard; optionally export or serve it without starting curation or rollout.
---

# /terminal-curator:dashboard

Read `blocks/terminal-curator/dashboard/README.md` and the selected config.
Run on the host holding this block's artifacts, preserving the same full
`--config <file>` profile. The board shares Curator's sidebar/cards/batch rows,
filters, dialogs and light/dark theme; it has Overview, Task List, Validation
and Runs views.

1. Execute
   `artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py`
   with the selected profile. Present actual latest-run status and published
   count as different scopes: a failed attempt can retain an older verified
   publication. Missing state/history is normal before the first run.
2. If asked for a page, use `--html` (or `--html <path>`). This produces one
   self-contained static snapshot. It does not poll an API on static hosting;
   regenerate it for new evidence.
3. If a local live preview is requested, use `--serve --port 8086` in a
   separate session. The default binding is loopback. Its JSON polling keeps
   the selected view and filters, and a failed refresh retains the previous
   snapshot. For remote resources, use the already authorized host and port
   forwarding; do not publish host details in a public artifact.

The projection may read live state, a run-ID-matching report, the verified
manifest and immutable versions, the run index and exact archived reports,
and contained trial result/JUnit files. It never serves raw files. Distinguish
actual reward/JUnit, partial evidence, report-only verdicts after cleanup,
and conflicting evidence. Harbor checksum linkage uses
`harbor_task_checksums`, not the distinct `task_checksums` block file digest.
Missing measurements stay unavailable; never invent source scan totals,
difficulty scores, semantic approvals or solved trajectories.

Generation completion includes failed attempts. `generation_failed` is that
failed subset, and `collected_questions: null` for local imports is not zero
question collection. Candidate/phase rows are progress, not verified output.
Show only safe identifiers, tags and numeric metadata; prompts, task text,
solutions, full config, endpoints, host paths, raw errors, archive notes and
private PID/start metadata do not belong in the page or API.

Rendering does not call services, generate tasks, launch Harbor/Tracer,
convert trajectories, train or deploy. Public publishing is a separate
requested action with artifact review. The docs demo is an explicitly
synthetic fixture; never overwrite it with real run data or cite it as live
validation evidence. Use the actual run snapshot separately when requested.
