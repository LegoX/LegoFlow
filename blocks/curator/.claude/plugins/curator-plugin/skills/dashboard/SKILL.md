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
without an explicit "yes". If a batch reports `[PATH NOT FOUND]` or zero tasks,
say so plainly and ask whether to continue or fix the config first — do not
silently render an empty board.

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
- Collection filter thresholds (`pr_collection.filters`) are **configuration**, not
  measured repo attributes; the collector persists only the flat PR id list, so
  repo stars and merged-PR counts are not available per repo.
