# Terminal Curator progress dashboard

Read live phase/counts and the verified-task manifest without starting
generation or validation:

```bash
artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py
artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py --html
artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py --html --serve --port 8086
```

Pass `--config <path>` when the run uses a local configuration overlay. The
monitor resolves artifact paths from that configuration and reads JSON/YAML
and local task files. It does not call the LLM or Stack Exchange API.

The default terminal view is useful over SSH. `--html` writes a local HTML
snapshot at `dashboard/site/index.html`, and `--serve` reads live state
on every request with a browser refresh every 10 seconds. Regenerate a standalone
snapshot after new results are available. Keep it on the execution node or use an SSH port forward
to inspect it from another host.

The dashboard reports source/generation/validation progress, verified count,
rejection count, and published IDs from actual files. Detailed candidate
outcomes stay in `artifacts/reports/latest.json`; archived run status stays in
`artifacts/index.yaml`. A missing live state is the normal fresh-checkout
state. Only the manifest names downstream eligible tasks.

See [dashboard documentation](../docs/content/docs/dashboard.mdx) and the
[block overview](overview.mdx). Generated HTML and runtime data are ignored by
Git. Publishing a dashboard is a separate user-requested action.
