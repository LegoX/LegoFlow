# Curator databoard

A single static page rendered from the **live pipeline** under `artifacts/`. It has
three views:

| View | Shows |
| --- | --- |
| Overview | Every batch folded into one set of figures, de-duplicated by task id |
| Collection | Repos and PRs collected, the drop reasons, and the PR → task → verified funnel |
| Task List | One row per configured batch; click a row for that batch's full profile |

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
      datasets:
        merged_swe_tasks: artifacts/merged_swe_tasks
        swe_tasks: artifacts/swe_tasks
      collected_prs_dir: ""
```

Each `name: path` entry is one **batch**. Paths are absolute or relative to
`config.yaml`. Both directory layouts are handled: `<path>/<task_id>/` and
`<path>/<lang>-cc/<task_id>/`.

`collected_prs_dir` empty falls back to `pr_collection.output_dir`.

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
| `site/index.html` | Rendered output |
