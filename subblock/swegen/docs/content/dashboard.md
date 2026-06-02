---
title: Dashboard
description: Monitor SWE-gen progress online
---

# Dashboard

Monitor SWE-gen progress online

SWE-gen has a separate live databoard for operational progress:

```
https://swe-databoard.pages.dev/
```

Open it here:

[Open SWE-gen Databoard](https://swe-databoard.pages.dev/)

## What it monitors

The databoard summarizes the live SWE-gen construction state:

| Source | Files | Shown as |
| --- | --- | --- |
| PR pools | `collected_prs/*_pr_ids.txt` or block artifact copies | Candidate PR counts |
| Batch state | `.swegen-create-batch/*.json` | processed, failed, pending, and success counts |
| Verified manifests | `verifiable_tasks.txt` | validated task totals |
| Task metadata | `task.toml` and solution/test files | difficulty and task-shape summaries |

The dashboard is intentionally separate from this documentation site. This site
explains the workflow; the databoard shows the current run.

## Local dashboard generator

The Live repository also contains a SWE-gen dashboard generator at:

```
dashboard/swegen/progress_monitor_all.py
```

It can generate a local HTML view:

```
python3 dashboard/swegen/progress_monitor_all.py --serve
```

and the accompanying Cloudflare sync script deploys it to the `swe-databoard`
Pages project.

## Docs versus databoard

There are two Cloudflare Pages projects:

| Project | URL | Purpose |
| --- | --- | --- |
| `swe-swegen-docs` | `https://swe-swegen-docs.pages.dev` | This documentation site |
| `swe-databoard` | `https://swe-databoard.pages.dev/` | Live SWE-gen progress dashboard |

Use the docs when onboarding or reviewing the pipeline. Use the databoard when
operating a live data construction run.
