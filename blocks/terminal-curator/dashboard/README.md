# Terminal Curator dashboard

The board uses Curator's visual system and layout: the 240px sidebar, warm
paper/rust palette, compact metric cards, collapsible sections, per-batch rows,
centered dialogs, and light/dark themes. It adapts those surfaces to the actual
terminal workflow rather than displaying SWE collection or difficulty scores
that this block does not measure.

| View | What it shows |
| --- | --- |
| Overview | Current publication, latest attempt, phase progression, measured validation counts, composition and numeric usage. |
| Task List | Current and retained immutable batches; search and filter by task ID/tag, source, status or batch; select a task for verifier details. |
| Validation | Latest run's Oracle/NOP outcomes, actual rewards and JUnit counts where available, report-only evidence and conflicts. |
| Runs | Newest archived attempts, including failures/interruption, source, duration, candidate and verified counts when recorded. |

## Use

Run from `blocks/terminal-curator/`:

```bash
artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py
artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py --html
artifacts/env/terminal-curator/bin/python dashboard/progress_monitor.py --serve --port 8086
```

The default output is an allowlisted JSON snapshot. `--config <file>` selects
the same complete profile used for the run; otherwise `TERMINAL_CURATOR_CONFIG`
selects it, falling back to `config.yaml`. Relative artifact paths resolve
from the block. `--html [file]` exports one self-contained page, by default
`dashboard/site/index.html`. CSS, JavaScript and projected data are embedded,
so the page needs no external assets or network service.

`--serve` binds to loopback by default and serves `/`, `/index.html` and
`/api/status` only. The browser polls live JSON every 10 seconds while
preserving the selected view and filters. Failed refreshes retain the last
snapshot. A standalone export is explicitly labeled **Snapshot**, never polls
a missing API on static hosting, and needs regeneration for new evidence.
`--host` is available for an explicitly requested alternative binding.

## What it reads

- `artifacts/state.json`: latest run's phase, counters and reported outcomes.
- The configured latest report, **only merged with live state when run IDs
  match**; an older completed report cannot replace a newer failed attempt.
- The configured publication manifest and its immutable version directories:
  IDs, minimal task metadata and provenance identifiers. Candidate task text,
  instructions, reference solutions and source files are never loaded for UI.
- `artifacts/index.yaml` and each matching archived report: run history.
- Trial result JSON and JUnit **only inside this block's run validation
  directories**: numeric reward/test counts. No raw config, errors or paths
  from those files are included in the page or API.

A reported pass remains labeled **report only** when raw verifier files have
been cleaned. A reward/JUnit contradiction is shown as an evidence conflict.
When the report includes `harbor_task_checksums`, the raw trial's
`task_checksum` is compared with that Harbor checksum. The separate
`task_checksums` field is displayed as **Block file digest**, never used as
Harbor's validation identity. Missing hashes remain unavailable.

Latest attempts and published pools have different scopes. A failed attempt
can coexist with a previous successful publication. Missing measurements
appear as `—`, and absent artifacts are a normal fresh-checkout state. No
collection totals, semantic approvals, solved trajectories or difficulty
scores are inferred. Runtime verification still requires the separate agent
semantic audit before downstream hand-off.

Snapshots bound detail to 100 published batches, 100 archived runs and 3,000
task rows, and show a notice if a limit is reached. Files larger than 4 MB,
malformed partial files and paths escaping the block's artifacts are reported
as unavailable. The board does not start generation or verification to fill
missing data.

## Privacy and sharing

The browser receives explicit field projections, not raw artifacts. It omits
prompts, traces, instructions, solutions, full config, endpoints, host paths,
PIDs, process metadata, archive notes, and raw exception/rejection text. Gate
failure reasons are mapped to a fixed vocabulary. Known environment/config
secrets are redacted; HTML and embedded JSON are escaped. Task IDs, batch/run
IDs, question IDs, tags, checksums and numeric usage remain visible: review
those before sharing a snapshot.

The HTTP server is not an artifact file server. Unknown routes return 404;
query strings and arbitrary request paths are not logged. It sends no-store
and nosniff headers. Publishing, long-running Cloudflare synchronization and
public access control are separate operations, not side effects of rendering.

`styles.css` derives its common chrome directly from
`blocks/curator/dashboard/progress_monitor_multi.py`; `template.html` and
`dashboard.js` implement the terminal views. All are embedded by
`progress_monitor.py`, so copying only the generated HTML is sufficient for
an inspected temporary static preview.
