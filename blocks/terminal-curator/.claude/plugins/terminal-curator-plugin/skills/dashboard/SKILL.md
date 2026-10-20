---
name: dashboard
description: Inspect Terminal Curator's actual source/generation/validation progress, verified manifest, and run history; optionally render or serve a local HTML snapshot. Use for terminal-curator status without starting curation or rollout.
---

# /terminal-curator:dashboard

Read `blocks/terminal-curator/dashboard/README.md` and the selected config.
Run from the block directory on the host holding its artifacts. Preserve
the `--config <file>` selection from the run.

1. Execute `artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py` with the block environment
   and overlay option when selected. Summarize live phase/counts and the manifest. Inspect the local latest
   report for rejection reasons and the index for archived status if needed;
   those raw files are not served to the browser. A fresh checkout with no
   state/index is a normal no-runs state.
2. If the user requests a page, run
   `artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py --html` to write a local snapshot.
   Use `--html <path>` only when another destination is requested.
3. If local preview is requested, use
   `artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py --html --serve --port 8086` in a
   separate session. For a remote host, show an SSH port-forward command
   using the already configured host without publishing internal addresses
   or credentials in a public artifact.

Exported HTML is a snapshot; regenerate it for new evidence. The local server
reads current state on every request and refreshes the browser every 10 seconds. Rendering may write
ignored dashboard files but never mutates task verification or launches API
calls, generation, Harbor jobs, Tracer, or training. Public publishing is a
separate user-requested operation. No secret config fields or raw signed
URLs may appear in the browser payload.
