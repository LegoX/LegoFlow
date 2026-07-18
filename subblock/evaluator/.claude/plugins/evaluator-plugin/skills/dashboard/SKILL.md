---
name: dashboard
description: >
  Inspect and browse eval results. Two surfaces: (1) a quick textual
  per-job summary read straight from `artifacts/jobs/<job>/` — tasks
  resolved / unresolved / in-flight, resolution rate, trajectory counts,
  benchmark + agent metadata; (2) the web dashboard under `dashboard/`
  (`bash dashboard/start.sh`, served on :8092) which renders the
  attribution/scoring artifacts produced by `scripts/analyze_job.sh` into
  `<job_dir>/analysis/`. Also drives the analysis pipeline
  (`analyze_job.sh`, `prepare_dataset.sh`) when a job has not been
  analyzed yet. Read-only over results. Triggers on phrases like "eval
  dashboard", "show eval progress", "swebench accuracy", "how many tasks
  resolved", "what eval jobs are running", "open the eval webui",
  "analyze this eval job".
---

# /evaluator:dashboard

The per-block "show me what's happening" surface. eval has both a
lightweight CLI summary and a full web dashboard; pick by what the user
asked for.

## Where the data lives

- **Raw job output**: `artifacts/jobs/<job>/<task>/{agent,verifier}/`,
  with each trial's status in `<task>/result.json`, trajectories at
  `<task>/agent/litellm-trajectory.jsonl`, and the job roll-up at
  `artifacts/jobs/<job>/result.json`.
- **Analysis artifacts**: `artifacts/jobs/<job>/analysis/` — written by
  `scripts/analyze_job.sh` (auto-run by `start.sh` after each eval). This
  is exactly what the web dashboard reads:
  `report_failed.json` / `report_resolved.json`,
  `report_task_analysis.json`,
  `traj_analysis/score_comparison.json`,
  `instance_analysis/{summary,correlations}.json`, and `instances.jsonl`.
  If a job has no `analysis/` dir, it was never analyzed — run the
  pipeline (below) before expecting the webui to show breakdowns.

## 1. Quick textual summary (default)

For each `artifacts/jobs/<job>/`, print a row with:
- benchmark (`dataset_name@version`) and agent (`name@version`, runtime image),
- total tasks, in-flight, resolved, unresolved, failed,
- resolution rate = resolved / (resolved + unresolved) — exclude in-flight
  from the denominator and say so,
- trajectories produced (count of `<task>/agent/litellm-trajectory.jsonl`).

Derive resolved/unresolved from the job-level `result.json`
(`stats.evals` / `reward_stats`) when present; otherwise scan per-task
`result.json` (`verifier_result.rewards.reward` / `exception_info`) and
`verifier/report.json`. For the same `(dataset_name, version, agent)`
triple, optionally roll up across matching entries in
`artifacts/index.yaml`.

## 2. Analysis pipeline (when a job lacks `analysis/`)

- **`scripts/analyze_job.sh [<job_dir>]`** — runs Harbor's `job_analysis`
  pipeline and writes `<job_dir>/analysis/`. No arg → newest job under
  `harbor_job.jobs_dir`. Idempotent; safe on old jobs. Uses
  `artifacts/env/harbor-uv` (CPU-only; LLM judge off by default → zero
  token cost, enable with `JOB_ANALYSIS_JUDGE=1` + `ANTHROPIC_API_KEY`).
- **Gold dependency**: the pipeline needs a gold dataset at
  `artifacts/datasets/<gold_base>/`. When absent, `analyze_job.sh`
  auto-runs **`scripts/prepare_dataset.sh [<dataset_name>]`** (adapter →
  tagger) to build it, then proceeds; if it still can't be produced it
  **skips cleanly** instead of crashing. Generation needs network
  (HuggingFace). The tagger needs a JSON-clean endpoint — see the
  `job_analysis.tag_llm` caveat in `/evaluator:setup` and `CLAUDE.md`.

## 3. Web dashboard

A stdlib-only HTTP server + vanilla-JS SPA under `dashboard/` (no build
step). It auto-discovers jobs under `artifacts/jobs/` and renders the
`analysis/` artifacts.

```bash
bash dashboard/start.sh                 # serves on :8092, defaults to artifacts/jobs
# or: python3 dashboard/server.py --jobs-dir artifacts/jobs --port 8092
```

Panels: **Overview** (aggregate stats across jobs), **All jobs** (sortable
table), **Single job** (analysis reports, failure distributions, task
breakdowns, trial details), **Compare** (shift-click jobs), and a
step-by-step **Trajectory viewer**. API: `GET /api/{jobs,overview}`,
`/api/jobs/<name>`, `/api/jobs/<name>/trials/<trial>[/trajectory]`,
`/api/compare?name=…&name=…`.

**Sharing (optional, only on request):**
- **Pinggy** (temporary tunnel for the live server): `bash
  dashboard/share_pinggy.sh` after `dashboard/start.sh` is up.
- **Cloudflare Pages** (durable public static export): `python3
  dashboard/export_static.py --output-dir site …` then `wrangler pages
  deploy`, or the long-running `bash dashboard/run_cloudflare_pages_sync.sh`
  loop. Needs `~/.config/harbor_webui_cloudflare.env`. See
  `dashboard/README.md` for the full chunked / R2 trajectory modes.

## Conventions

- **Read-only over results**: this skill summarizes, analyzes, and serves;
  it never launches an eval (`/evaluator:run`) or edits `config.yaml`.
- **Host**: results live on the node in `meta_info.resources.ip`
  (the checked-in default is `local`). For a configured remote host,
  connect there to read job dirs / serve the webui; a dashboard served
  on a host that holds no jobs shows nothing.
- The analysis pipeline is also run automatically by `start.sh`; invoking
  it here is for old/interrupted jobs or a forced re-analysis.
