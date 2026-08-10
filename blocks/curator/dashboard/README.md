# Curator databoard

A single static page rendered from the **live pipeline** under `artifacts/`. It has
three views:

| View | Shows |
| --- | --- |
| Overview | Headline totals and rates, the repos → PRs → tasks funnel, and task-creation success by language |
| PR Collection | Repos and PRs collected, per language, with the reasons rows were dropped |
| Task List | One row per configured batch; click a row for its full profile and 10 sample tasks |

## Where the numbers come from

Nothing here calls an LLM and nothing is exported into this directory first. Each
task is read from its own `task.toml`:

```toml
[metadata]
difficulty = "medium"
tags = ["python", "backend", "mcp-server", "default-configuration"]
```

`tags` is the harbor 4-tuple `[language, area, topic, bug_class]`, written upstream
by the tagger during task generation. **Language comes from this file, never from the
directory name** — a task sitting in `py-cc/` that declares Go counts as Go.

Numeric difficulty (`[scoring] difficulty_score`) is optional; where it is absent the
label still counts, and pooled order statistics report as `—` rather than 0.

Collection statistics come from `artifacts/collected_prs/`:

- `filtering_report.md` — the collector's own funnel (repos searched → candidates →
  kept; PRs scanned → merged → qualifying) plus drop reasons per language
- `{language}_pr_ids.txt` — `owner/repo:pr-NUMBER`, the survivors

The large `{language}_prs.jsonl` payloads are deliberately not read.

## Configuration

`config.yaml → runtime_info.input.dashboard`:

```yaml
    dashboard:
      prs:
        collected_prs: artifacts/collected_prs
      tasks:
        swe_tasks_py: artifacts/swe_tasks/py-cc
        swe_tasks_go: artifacts/swe_tasks/go-cc
```

`tasks` and `prs` are both `name: path`. A **task** path is any directory holding a
list of harbor tasks — there is no requirement that it be `merged_swe_tasks`. A
**PR** path is a whole collection directory; every language inside is discovered
automatically, so one entry normally covers all of them.

Each `name: path` entry is one **batch**, and must hold one harbor task per
immediate child (`task.toml` + `instruction.md`). Paths are absolute or relative
to `config.yaml`.

Check a path before configuring it:

```bash
python3 dashboard/check_task_dir.py <path>
```

It reports `OK`, `NESTED` (tasks one level deeper, e.g. `swe_tasks/<lang>-cc/`),
`EMPTY` or `MISSING`. The generator runs the same check and exits non-zero if any
configured batch fails, so a mistyped path is reported rather than showing up as
zero tasks.

An empty `prs` falls back to `pr_collection.output_dir`.

## Open-source datasets

Public HF datasets go on the board the same way: convert them to task directories,
then list them as a batch. `import_hf_dataset.py` knows `scale_swe`,
`openswe_filtered` and `swe_rebench_v2`.

```bash
python3 dashboard/import_hf_dataset.py scale_swe --out artifacts/hf/scale_swe
python3 ../repos/swegen/tools/tag_task_metadata.py --tasks-dir artifacts/hf/scale_swe --jobs 64
```

The import writes `tags = [language]` only; the tagger fills area, topic, bug_class,
category and scoring. Until it runs, those tasks show as untagged rather than
guessed. Then add the batch with `external: true`:

```yaml
      datasets:
      tasks:
        scale_swe: {path: artifacts/hf/scale_swe, external: true}
```

`external` keeps them out of the PR → task funnel on Collection — they have no PR
provenance, so counting them there would understate the pipeline's conversion rate.
They still appear on the Task List and in the Overview totals.

### Overlapping batches are expected

`merged_swe_tasks` is produced by copying verified tasks out of `swe_tasks`, so most
ids appear in both — and because that copy never deletes, it also retains tasks that
have since disappeared from `swe_tasks`. Neither pool is a subset of the other in
practice. Each is reported as its own batch, and **Overview de-duplicates by task id**,
so never add per-batch totals together to get a global figure.

## Running

```bash
# from blocks/curator/
python3 dashboard/progress_monitor_multi.py --report-only          # sources, no writes
python3 dashboard/progress_monitor_multi.py --output-html dashboard/site/index.html
```

`--report-only` prints the resolved paths, per-batch counts, batch overlap and the
collection totals without writing anything; `/curator:dashboard` uses it as its
confirmation gate.

Deploy is a separate, explicit step:

```bash
# from blocks/curator/dashboard/site/
CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... \
  npx wrangler@3 pages deploy . --project-name=swe-databoard --branch=main
```

## Files

| File | Role |
| --- | --- |
| `progress_monitor_multi.py` | Config loading, aggregation, HTML rendering |
| `task_toml.py` | Reads `task.toml` across both layouts (ported from tracer's dashboard) |
| `collection_stats.py` | Parses `filtering_report.md` and the PR id lists |
| `check_task_dir.py` | Verifies a directory is a list of harbor tasks |
| `sample_tasks.py` | Caches 10 whole tasks per batch for the sample viewer |
| `import_hf_dataset.py` | Converts a public HF dataset into task directories |
| `site/index.html` | Rendered output |

## Sample tasks

Counts never show what a task actually *is*. Each batch caches its first ten tasks
to `site/data/samples-<batch>.json` — problem statement, verifier, reference fix,
bug patch and Dockerfile, each capped at 24 KB — and the Task List fetches one
only when a reader opens it. The JSON files are the cache; they are rewritten on
every render.
