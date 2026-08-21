---
name: dashboard
description: >
  Regenerate and publish Curator's databoard from the live pipeline under
  artifacts/. Sources are fixed, not configured: artifacts/collected_prs/ for PR
  collection and every immediate subdirectory of artifacts/swe_tasks/ as one
  batch. Language, difficulty and the four semantic tags
  [language, area, topic, bug_class] come from each task's own task.toml.
  Always presents the resolved paths and discovered batches, confirms each batch
  is a directory of harbor tasks, and waits for explicit confirmation before
  rendering. Triggers on phrases like
  "curator dashboard", "regenerate the databoard", "update the databoard",
  "how many tasks have we made", "collection progress".
---

# /curator:dashboard

Render the **curator databoard** from the live pipeline and, only when asked,
deploy it. Three views: **Overview** (global, de-duplicated), **Collection**
(repos and PRs collected, and the PR → task → verified funnel), and **Task List**
(one row per batch, click through to its full profile).

This skill never generates tasks and never calls an LLM. Difficulty and tags are
written upstream into each task's `task.toml`; the dashboard only reads them.

## Step 0 - Orient

Run from `blocks/curator/`. Confirm `config.yaml` has
`meta_info.name == "curator"`.

## Step 1 - The sources are fixed

There is nothing to configure. The board always reads:

| What | Where |
| --- | --- |
| PR collection | `artifacts/collected_prs/` — one directory; every `{lang}_pr_ids.txt` and `filtering_report.md` inside is found automatically |
| Task batches | `artifacts/swe_tasks/*` — **each immediate subdirectory is one batch** |

**A batch's name on the board is its directory name** — `py-cc`, `go-cc`, and so
on. There is no place to rename it.

If `artifacts/swe_tasks/` holds no subdirectory, report that and stop; there is
nothing to read.

Batches may overlap (the same task id in two pools). Overview de-duplicates by
task id — never sum the per-batch totals to get a global figure.

## Step 2 - Show the sources and confirm

Scan read-only and print the report, then **wait for an explicit "yes"**:

```bash
python3 dashboard/progress_monitor_multi.py --report-only
```

It prints the resolved absolute paths, the batches discovered under
`artifacts/swe_tasks/`, per-batch task/verified/tagged counts and language mix,
which batches are symlinked-in datasets, any layout that is not a directory of
harbor tasks, the overlap between batches, the PR collection totals, and the
de-duplicated global figure:

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

**Confirm the format, do not assume it.** Before asking for that yes, state for
each batch whether it is a directory of harbor tasks — the report's layout column
is what tells you. A batch that is not `OK` (or `PARTIAL`, with the ignored
children named) must be raised with the user explicitly; do not fold it silently
into the totals. This matters most for symlinked-in third-party datasets, which
have not been through this pipeline and are the likeliest to be shaped
differently.

### Every batch must be a list of harbor tasks

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

Any directory can also be checked on its own before linking it in:

```bash
python3 dashboard/check_task_dir.py <path> [<path> ...]
```

Stop and ask the user how to proceed when a batch is `MISSING`, `NESTED` or
`EMPTY` — the fix is to correct or remove that entry under
`artifacts/swe_tasks/`, not to edit any config. Only continue past a warning if
they say so knowing what it means.

## Optional - add a third-party dataset

A dataset the collector did not produce joins the board by being **symlinked into
`artifacts/swe_tasks/`**. There is no registration step and nothing to edit:

```bash
python3 dashboard/import_hf_dataset.py <scale_swe|openswe_filtered|swe_rebench_v2> \
  --out artifacts/hf/<name>
python3 repos/legoflow-curator/tools/tag_task_metadata.py --tasks-dir artifacts/hf/<name> --jobs 64
python3 dashboard/check_task_dir.py artifacts/hf/<name>      # confirm the shape first
ln -s "$(pwd)/artifacts/hf/<name>" artifacts/swe_tasks/<name>
```

The link's own name becomes the batch name. Symlinked batches are reported as
`[symlinked dataset]` and kept out of the PR → task funnel, which only describes
tasks the collector sourced. An imported dataset is the likeliest thing on this
board to be shaped differently, so check it *before* linking it in.

Importing downloads from HuggingFace and tagging spends LLM tokens — treat both
as separate actions the user must ask for.

## Step 3 - Render

```bash
python3 dashboard/progress_monitor_multi.py --output-html dashboard/site/index.html
```

Writes only `dashboard/site/index.html` (generated output; not tracked in git).
Preview locally before any deploy.

## Step 4 - Deploy (only when the user asks)

```bash
# from blocks/curator/
source ../../scripts/publish_dashboard.sh
publish_dashboard curator dashboard/site
```

With Cloudflare credentials this deploys to the Pages project `legoflow-curator`
and prints the URL **read back from the API**; without them it opens a temporary
`*.trycloudflare.com` tunnel instead. Never construct the address from the
project name — a taken `*.pages.dev` subdomain is silently given a suffix.

Never deploy unless the user explicitly requests it.

## Guardrails

- The only write is `dashboard/site/index.html`. Reading task pools is read-only.
- Never edit `config.yaml`, token files, generated task directories, or submodule
  source. The board has no configuration to edit: to change what it shows, change
  what is under `artifacts/swe_tasks/`.
- Never launch `scripts/start.sh`, `legoflow-curator create`, PR collection, or a deploy
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
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow dashboard curator`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:dashboard`. The shared CLI accepts the same command as `./bin/legoflow /curator:dashboard` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
