---
name: dashboard
description: >
  Regenerate and publish Curator's dataset analytics databoard: per-task
  difficulty scores and the four semantic tags [language, area, topic,
  bug_class], compared across the curated datasets and published at
  swe-databoard.pages.dev. Runs the canonical tagger
  (repos/swegen/tools/tag_task_metadata.py) and the HTML generator
  (dashboard/progress_monitor_multi.py). This is dataset analytics, NOT live
  run-progress monitoring — for run status read the batch logs,
  verifiable_tasks.txt, and artifacts/index.yaml. Triggers on phrases like
  "curator dashboard", "regenerate the databoard", "difficulty and tags",
  "update swe-databoard".
---

# /curator:dashboard

Regenerate the **dataset analytics databoard** and, optionally, deploy it to
Cloudflare Pages. The databoard scores each task's difficulty and assigns the
four semantic tags `[language, area, topic, bug_class]`, then renders a
single-page comparison across the curated datasets, published at
`swe-databoard.pages.dev`.

This skill analyzes finished datasets. It does **not** report live run
progress. For run status, read the batch logs under
`artifacts/logs/swegen-create/`, the per-language `verifiable_tasks.txt`
manifests, and `artifacts/index.yaml`; `/curator:check` diagnoses the
environment.

## Step 0 - Orient

Run from `blocks/curator/`. Validate that `config.yaml` has
`meta_info.name == "curator"`. The databoard sources live under `dashboard/`;
read `dashboard/README.md` for the dataset roster, export scripts, and endpoint
configuration.

## Step 1 - Confirm the datasets

Each dataset lives under `dashboard/datasets/<id>/`:

| id | Display | Source |
| --- | --- | --- |
| `self_made` | LegoFlow-Instances | Curator `swegen-selfmade` export |
| `swe_rebench` | SWE-rebench | `nebius/SWE-rebench` |
| `openswe_filtered` | OpenSWE-filtered | `SWE-Lego/openswe_filtered_for_rl` |
| `scale_swe` | Scale-SWE | `AweAI-Team/Scale-SWE` |

Each `<id>/` needs `tasks.jsonl` (unified records) before tagging. If a
dataset's `tasks.jsonl` is missing, produce it with the matching exporter
(`export_self_made.py`, `export_openswe_filtered.py`, …) or drop in the
externally-prepared JSONL, per `dashboard/README.md`. Both files are
git-ignored and large.

## Step 2 - Difficulty + tag generation

Run the **canonical** tagger from the swegen submodule so the databoard and the
SWE-gen pipeline share one implementation. It scores difficulty (a 5-dimension
weighted, log-scaled `1.0–10.0` value bucketed easy/medium/hard) and assigns
the 4-tuple `[language, area, topic, bug_class]`, writing
`dashboard/datasets/<id>/tags.jsonl`. Runs are resumable and idempotent.

```bash
# from blocks/curator/dashboard/
python3 ../repos/swegen/tools/tag_task_metadata.py \
  --datasets-dir datasets --dataset all --jobs 64 --retries 3
```

## Step 3 - Render the HTML

Build the single-page multi-dataset databoard from the tagged datasets:

```bash
# from blocks/curator/dashboard/
python3 progress_monitor_multi.py --output-html site/index.html
```

This reads each `datasets/<id>/tags.jsonl` and writes only
`dashboard/site/index.html`. Preview it locally before any deploy.

## Step 4 - Deploy (only when the user asks)

Publishing is a separate, explicit action. Deploy the rendered site to the
`swe-databoard` Cloudflare Pages project:

```bash
# from blocks/curator/dashboard/site/
CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... \
  npx wrangler@latest pages deploy . --project-name=swe-databoard --branch=main
```

Never deploy unless the user explicitly requests it.

## Guardrails

- The only writes this skill performs are the tagger's `tags.jsonl` outputs and
  the rendered `dashboard/site/index.html`.
- Never edit `config.yaml`, token files, generated task directories, or
  submodule source.
- Never launch `scripts/start.sh`, `swegen create`, or a Cloudflare deploy
  unless the user explicitly asks for that separate action.
- This skill does not report live run progress; redirect such requests to the
  batch logs, `verifiable_tasks.txt`, and `artifacts/index.yaml`.
