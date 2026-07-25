---
name: dashboard
description: >
  Summarize curator's progress across languages: PRs collected, tasks
  generated, verifiable_tasks.txt count, NOP/Oracle pass rate, last run
  duration. Reads `artifacts/swe_tasks/<lang>-cc/` and the per-language
  manifests; no live server needed for the textual view, but if a webui
  exists, launch it on a configured port. Read-only. Triggers on phrases
  like "curator dashboard", "show curator progress", "how many tasks does
  curator have", "verifiable rate per language".
---

# /curator:dashboard

Read-only progress view for Curator. It reports what has been collected,
generated, verified, and archived without launching generation or editing
configuration.

## Step 0 - Orient

Run from `subblock/curator/`. Validate that `config.yaml` has
`meta_info.name == "curator"`. Read `runtime_info.input.languages` to get
the language keys and tuned parameters.

## Step 1 - Gather progress inputs

For each language mapping:

| Key | PR file | Task directory |
| --- | --- | --- |
| `py` | `artifacts/collected_prs/python_pr_ids.txt` | `artifacts/swe_tasks/py-cc/` |
| `js` | `artifacts/collected_prs/javascript_pr_ids.txt` | `artifacts/swe_tasks/js-cc/` |
| `ts` | `artifacts/collected_prs/typescript_pr_ids.txt` | `artifacts/swe_tasks/ts-cc/` |
| `go` | `artifacts/collected_prs/go_pr_ids.txt` | `artifacts/swe_tasks/go-cc/` |
| `c` | `artifacts/collected_prs/c_pr_ids.txt` | `artifacts/swe_tasks/c-cc/` |
| `cpp` | `artifacts/collected_prs/cpp_pr_ids.txt` | `artifacts/swe_tasks/cpp-cc/` |
| `java` | `artifacts/collected_prs/java_pr_ids.txt` | `artifacts/swe_tasks/java-cc/` |
| `rust` | `artifacts/collected_prs/rust_pr_ids.txt` | `artifacts/swe_tasks/rust-cc/` |

Read, without modifying:

- PR count from each PR file.
- Generated task count from task directories that contain `tests/test.sh`.
- Verified count from `<task-dir>/verifiable_tasks.txt`.
- Batch state from hidden SWEgen state directories when present.
- Difficulty from each task's `task.toml` (scored inline during `swegen
  create`); dataset-level difficulty + tags live in
  `dashboard/datasets/<id>/tags.jsonl` when present.
- Latest create logs under `artifacts/logs/swegen-create/`.
- Latest run entry from `artifacts/index.yaml`.
- Block status from `config.yaml -> status`.

Also check for live processes scoped to this block, not every Curator job on
the host:

```bash
BLOCK_DIR="$(pwd -P)"
pgrep -af 'swegen create|scripts/create_' | grep -F "$BLOCK_DIR" || true
```

Include matching PIDs if present. Ignore unrelated jobs from other checkouts
such as `$HOME/SWE-gen`.

## Step 2 - Optional HTML dashboard refresh

`dashboard/` hosts the dataset **analytics** dashboard (difficulty + tag
comparison across datasets), published to `swe-databoard.pages.dev`. It is a
separate concern from this textual progress view. If the user asks for the web
dashboard, regenerate the local HTML from the tagged datasets:

```bash
python3 dashboard/progress_monitor_multi.py --output-html dashboard/site/index.html
```

This reads `dashboard/datasets/<id>/tags.jsonl` and writes only the HTML. Do
not start any deploy unless the user explicitly asks. See
`dashboard/README.md` for the tagging pipeline.

## Step 3 - Print the textual summary

Default output:

```text
curator dashboard - <CWD>
Status: <config.status.phase> - <config.status.progress>
Live run: <none|pid list>

Language  PRs  Generated  Verified  Rate   timeout  cc_timeout  concurrency  Last log
py        <n>  <n>        <n>       <pct>  <n>      <n>         <n>          <path>
...

Latest run: <id/status/started/completed from artifacts/index.yaml or none>
Outputs:
  tasks:  artifacts/swe_tasks/<lang>-cc/
  merged: artifacts/merged_swe_tasks/
  logs:   artifacts/logs/swegen-create/
  html:   dashboard/site/index.html
```

When task directories are missing, show zero counts rather than treating it
as an error. When a log exists, include the path and last meaningful status
line if available.

## Step 4 - Interpret health

Flag these conditions:

- PR count is zero for an enabled language.
- Verified count is zero while generated count is nonzero.
- Success rate is unusually low compared with `config.yaml` language
  status.
- A live process exists but the newest log has not changed recently.
- `verifiable_tasks.txt` lists a task id whose task directory is missing.

Do not change parameters from dashboard. Suggest `/curator:check` for
environment failures and `/curator:create-tasks` for new generation.

## Guardrails

- Read-only by default. The only allowed write is the optional one-shot
  HTML dashboard refresh requested by the user.
- Never edit `config.yaml`, token files, generated task directories, or
  submodule source.
- Never launch `scripts/start.sh`, `swegen create`, or Cloudflare deploy
  from this skill unless the user explicitly asks for that separate action.
