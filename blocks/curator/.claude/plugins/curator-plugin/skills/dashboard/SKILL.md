---
name: dashboard
description: >
  Regenerate and publish Curator's databoard from the live pipeline under
  artifacts/. Reads the task pools named in config.yaml
  (runtime_info.input.dashboard.datasets), taking language, difficulty and the
  four semantic tags [language, area, topic, bug_class] from each task's own
  task.toml, and reports PR/repo collection, per-batch progress, and
  verification yield. Always presents the resolved paths and discovered batches
  and waits for explicit confirmation before rendering. Triggers on phrases like
  "curator dashboard", "regenerate the databoard", "update swe-databoard",
  "how many tasks have we made", "collection progress".
---

# /curator:dashboard

Render the **curator databoard** from the live pipeline and, only when asked,
deploy it. Three views: **Overview** (global, de-duplicated), **Collection**
(repos and PRs collected, and the PR → task → verified funnel), and **Task List**
(one row per configured batch, click through to its full profile).

This skill never generates tasks and never calls an LLM. Difficulty and tags are
written upstream into each task's `task.toml`; the dashboard only reads them.

## Step 0 - Orient

Run from `blocks/curator/`. Confirm `config.yaml` has
`meta_info.name == "curator"`.

## Step 1 - Resolve the sources

Read `config.yaml -> runtime_info.input.dashboard`:

- `datasets` — `name: path` entries. Each is one **batch** on the Task List.
  Paths may be absolute or relative to the block root.
- `collected_prs_dir` — empty falls back to `pr_collection.output_dir`.

If `datasets` is empty or absent, report that and stop; there is nothing to read.

Pools are allowed to overlap: `merged_swe_tasks` is a manifest-filtered copy of
`swe_tasks`, so the same task id appears in both. Each is still listed as its own
batch, and Overview de-duplicates by task id — never sum the per-batch totals to
get a global figure.

## Step 2 - Show the sources and confirm

Scan read-only and print the report, then **wait for an explicit "yes"**:

```bash
python3 dashboard/progress_monitor_multi.py --report-only
```

It prints the resolved absolute paths, per-batch task/verified/tagged counts and
language mix, any path that does not exist, the overlap between batches, the PR
collection totals, and the de-duplicated global figure:

```text
curator dashboard sources
  <batch name>           <abs path>
                          <N> tasks · <V> verified · <T> tagged
                         languages: <lang>=<n>, ...
  overlap                <K> task ids shared between <a> and <b> (counted once in Overview)
  PR collection          <abs path>
                         <P> PRs · <R> repos · <L> languages
  global (de-duplicated) <U> unique tasks · <V> verified · <T> tagged
```

Present this to the user verbatim and ask whether to proceed. Never render
without an explicit "yes".

### Every configured path must be a list of harbor tasks

A batch directory holds **one harbor task per immediate child**, each with
`task.toml` and `instruction.md`:

```text
<batch>/<task_id>/task.toml
<batch>/<task_id>/instruction.md
```

The report checks this and exits non-zero when any path fails. A wrong path still
renders — it just reports zero — which reads as "we have no tasks" instead of
"you pointed me at the wrong directory", so **never pass a layout problem along
silently**. Show the user the flagged block and what it says:

| Status | Meaning | What to tell the user |
| --- | --- | --- |
| `MISSING` | path does not exist | the path is wrong, or the artifacts live on another host |
| `NESTED` | tasks are one level deeper, e.g. `swe_tasks/<lang>-cc/<task>/` | name the specific subdirectory, or use the flattened pool instead |
| `EMPTY` | directory exists, no `task.toml` at either depth | not a task pool — check what was intended |
| `PARTIAL` | some children are tasks, some are not | say which children are being ignored |

Any path can also be checked on its own before editing the config:

```bash
python3 dashboard/check_task_dir.py <path> [<path> ...]
```

Stop and ask the user to fix `config.yaml` when a batch is `MISSING`, `NESTED` or
`EMPTY`. Only continue past a warning if they say so knowing what it means.

## Optional - add an open-source dataset

Public HF datasets are shown as ordinary batches. Convert, tag, then register:

```bash
python3 dashboard/import_hf_dataset.py <scale_swe|openswe_filtered|swe_rebench_v2> \
  --out artifacts/hf/<name>
python3 repos/swegen/tools/tag_task_metadata.py --tasks-dir artifacts/hf/<name> --jobs 64
```

Then add `<name>: {path: artifacts/hf/<name>, external: true}` under
`dashboard.datasets`. `external` keeps them out of the PR → task funnel, which only
describes tasks the collector sourced. Importing downloads from HuggingFace and
tagging spends LLM tokens — treat both as separate actions the user must ask for.

## Step 3 - Render

```bash
python3 dashboard/progress_monitor_multi.py --output-html dashboard/site/index.html
```

Writes only `dashboard/site/index.html`. Preview locally before any deploy.

## Step 4 - Deploy (only when the user asks)

```bash
# from blocks/curator/dashboard/site/
CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... \
  npx wrangler@3 pages deploy . --project-name=swe-databoard --branch=main
```

Never deploy unless the user explicitly requests it.

## Guardrails

- The only write is `dashboard/site/index.html`. Reading task pools is read-only.
- Never edit `config.yaml`, token files, generated task directories, or submodule
  source.
- Never launch `scripts/start.sh`, `swegen create`, PR collection, or a deploy
  unless the user asks for that separate action.
- A missing value is reported as an em dash, never as `0` — `task.toml` carries no
  patch statistics, and untagged tasks are counted as untagged rather than folded
  into a tag bucket. Do not "fix" these by substituting zeros.
- The Collection funnel is read from the collector's own
  `collected_prs/filtering_report.md`. Where it is absent, those columns report as
  em dashes — do not reconstruct them from the id lists, which only carry survivors.
- `pr_collection.filters` are **configuration**, shown as such. Per-repo stars and
  merged-PR counts are not recorded anywhere; never present a threshold as a
  measurement.
