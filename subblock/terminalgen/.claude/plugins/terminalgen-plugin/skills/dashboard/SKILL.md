---
name: dashboard
description: >
  Summarize terminalgen's progress across the 13 task domains: questions
  scraped, tasks generated, verifiable_tasks.txt count, Docker pass rate,
  last run duration. Reads `artifacts/terminal_tasks/<domain>-tl/` and the
  per-domain manifests; no live server needed for the textual view, but if
  a webui exists, launch it on a configured port. Read-only. Triggers on
  phrases like "terminalgen dashboard", "show terminalgen progress",
  "how many tasks does terminalgen have", "verifiable rate per domain".
---

# /terminalgen:dashboard

Read-only progress view for terminalgen. It reports what has been scraped,
generated, verified, and archived without launching generation or editing
configuration.

## Step 0 - Orient

Run from `subblock/terminalgen/`. Validate that `config.yaml` has
`meta_info.name == "terminalgen"`. Read `runtime_info.input.domains` to get the
domain keys and tuned parameters.

## Step 1 - Gather progress inputs

For each domain `<domain>`:

| Input | Path |
| --- | --- |
| Scraped questions | `artifacts/collected_questions/<domain>_so_data.json` (count `questions[]`) |
| Candidate tasks | `artifacts/terminal_tasks/<domain>-tl/_candidates/task_*/` |
| Verified count | `artifacts/terminal_tasks/<domain>-tl/verifiable_tasks.txt` |
| Validation report | `artifacts/terminal_tasks/<domain>-tl/validation_report.json` |

Read, without modifying:

- Question count from each bucket JSON.
- Generated candidate count from `_candidates/` directories containing `tests/test.sh`.
- Verified count from `<domain>-tl/verifiable_tasks.txt`.
- Pass/fail breakdown from `validation_report.json` when present.
- Latest create logs under `artifacts/logs/terminalgen-create/`.
- Latest run entry from `artifacts/index.yaml`.
- Block status from `config.yaml -> status`.

Also check for live processes scoped to this block, not every job on the host:

```bash
BLOCK_DIR="$(pwd -P)"
pgrep -af 'task_generator.py|validate_tasks.py|scripts/create_domain' | grep -F "$BLOCK_DIR" || true
```

Include matching PIDs if present. Ignore unrelated jobs from other checkouts
such as `$HOME/terminal-lego`.

## Step 2 - Optional HTML dashboard refresh

If `dashboard/progress_monitor_all.py` exists and the user asks for the web
dashboard, run it once to regenerate the local HTML:

```bash
python3 dashboard/progress_monitor_all.py \
  --output-html dashboard/site/index.html \
  --state-file dashboard/memory/.progress_monitor_all_state.jsonl \
  --cache-file dashboard/memory/.progress_monitor_all_cache.json
```

This writes only dashboard runtime files. Do not start any deploy/sync loop
unless the user explicitly asks.

## Step 3 - Print the textual summary

Default output:

```text
terminalgen dashboard - <CWD>
Status: <config.status.phase> - <config.status.progress>
Live run: <none|pid list>

Domain                  Scraped  Generated  Verified  Rate   gen_w  val_w  val_to  Last log
core-terminal-os        <n>      <n>        <n>       <pct>  <n>    <n>    <n>     <path>
...

Latest run: <id/status/started/completed from artifacts/index.yaml or none>
Outputs:
  tasks:  artifacts/terminal_tasks/<domain>-tl/
  merged: artifacts/merged_terminal_tasks/   (harbor 1.1, downstream input)
  logs:   artifacts/logs/terminalgen-create/
  html:   dashboard/site/index.html
```

When task directories are missing, show zero counts rather than treating it as
an error. When a log exists, include the path and last meaningful status line.

## Step 4 - Interpret health

Flag these conditions:

- Scraped count is zero for an enabled domain.
- Verified count is zero while generated count is nonzero (tasks failing Docker
  validation — common when the generator picks internet/credential-dependent
  topics; suggest narrowing the domain's `tag_filter` toward self-contained tasks).
- Success rate is unusually low compared with `config.yaml` domain status.
- A live process exists but the newest log has not changed recently.
- `verifiable_tasks.txt` lists a task id whose task directory is missing.

Do not change parameters from dashboard. Suggest `/terminalgen:check` for
environment failures and `/terminalgen:run` for new generation.

## Guardrails

- Read-only by default. The only allowed write is the optional one-shot HTML
  dashboard refresh requested by the user.
- Never edit `config.yaml`, generated task directories, or submodule source.
- **Never modify `repos/terminal-lego/`.**
- Never launch `scripts/start.sh` or the generator from this skill unless the
  user explicitly asks for that separate action.
