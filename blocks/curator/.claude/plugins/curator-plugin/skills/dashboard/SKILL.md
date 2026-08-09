---
name: dashboard
description: >
  Regenerate and publish Curator's dataset analytics databoard: per-task
  difficulty scores and the four semantic tags [language, area, topic,
  bug_class], compared across the curated datasets and published at
  legoflow-databoard.pages.dev. Self-made metadata comes from task.toml; only
  external datasets run the canonical tagger
  (repos/legoflow-curator/tools/tag_task_metadata.py). The read-only HTML
  generator is dashboard/progress_monitor_multi.py. This is dataset analytics,
  NOT live run-progress monitoring — for run status read the batch logs,
  verifiable_tasks.txt, and artifacts/index.yaml.
---

# /curator:dashboard

Regenerate the **dataset analytics databoard** and, optionally, deploy it to
Cloudflare Pages. The databoard renders prepared difficulty metadata and the
four semantic tags `[language, area, topic, bug_class]` in a single-page
comparison across the curated datasets, published at
`legoflow-databoard.pages.dev`.

This skill analyzes finished datasets. It does **not** report live run
progress. For run status, read the batch logs under
`artifacts/logs/legoflow-curator-create/`, the per-language `verifiable_tasks.txt`
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
| `self_made` | LegoFlow Curator Instances | LegoFlow-SWE-Curator task directories or task tarballs |
| `swe_rebench` | SWE-rebench | `nebius/SWE-rebench` |
| `swe_rebench_v2` | SWE-rebench-V2 | `nebius/SWE-rebench-V2` |
| `openswe_filtered` | OpenSWE-filtered | `SWE-Lego/openswe_filtered_for_rl` |
| `scale_swe` | Scale-SWE | `AweAI-Team/Scale-SWE` |

`dashboard/dataset_registry.py` is the code-level source of truth for this
roster. Each `<id>/` needs prepared `tasks.jsonl` and `tags.jsonl`; both are
git-ignored and large.

## Step 2 - Prepare metadata

For `self_made`, export complete Curator task directories or tarballs. This
copies existing metadata from `task.toml` and performs no network or LLM call:

```bash
# from blocks/curator/dashboard/
python3 export_self_made.py --source <task-dir-or-tarball>
```

Every task must include `instruction.md`, `solution/fix.patch`, and valid
`task.toml` values for `metadata.difficulty`, four `metadata.tags`,
`scoring.difficulty_score`, and `scoring.difficulty_label`. A missing or invalid
task fails the export with the complete affected-task list; never fall back to
LLM tagging.

For external datasets only, run the **canonical** tagger from the
legoflow-curator submodule during preparation:

```bash
# from blocks/curator/dashboard/
python3 ../repos/legoflow-curator/tools/tag_task_metadata.py \
  --datasets-dir datasets --dataset <external-id> --jobs 64 --retries 3
python3 metadata_records.py \
  --dataset <external-id> --tags-file datasets/<external-id>/tags.jsonl
```

## Step 3 - Render the HTML

Build the single-page multi-dataset databoard from the tagged datasets:

```bash
# from blocks/curator/dashboard/
python3 progress_monitor_multi.py --output-html site/index.html
```

This reads and validates each `datasets/<id>/tags.jsonl` and writes only
`dashboard/site/index.html`. It never invokes a scorer or tagger. Preview it
locally before any deploy.

For a one-task or self-made-only E2E validation, render only the prepared
Curator dataset without requiring external dataset files:

```bash
python3 progress_monitor_multi.py \
  --dataset self_made \
  --output-html <experiment-dir>/dashboard/index.html
```

## Step 4 - Deploy (only when the user asks)

Publishing is a separate, explicit action. Deploy the rendered site to the
`legoflow-databoard` Cloudflare Pages project:

```bash
# from blocks/curator/dashboard/site/
CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... \
  npx wrangler@latest pages deploy . --project-name=legoflow-databoard --branch=main
```

Never deploy unless the user explicitly requests it.

## Guardrails

- Preparation writes only dashboard `tasks.jsonl` / `tags.jsonl`; rendering
  writes only `dashboard/site/index.html`.
- Preserve `metadata_source`, `metadata_schema_version`, `scorer_provenance`,
  and `tagger_provenance` in metadata records.
- Never edit `config.yaml`, token files, generated task directories, or
  submodule source.
- Never launch `scripts/start.sh`, `legoflow-curator create`, or a Cloudflare deploy
  unless the user explicitly asks for that separate action.
- This skill does not report live run progress; redirect such requests to the
  batch logs, `verifiable_tasks.txt`, and `artifacts/index.yaml`.
