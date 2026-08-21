---
name: dashboard
description: >
  Read-only progress surface for the trainer block. Backed by the web dashboard
  under dashboard/ (React + server.py) that parses artifacts/model/<run>/
  (trainer_log.jsonl, trainer_state.json, *_results.json) and console logs in
  artifacts/logs/. This skill prints a quick textual summary (status, config,
  latest step/loss/ETA, final metrics, WandB run id, artifacts, newest
  artifacts/index.yaml entry) and, on request, launches the web UI on :8091.
  Never modifies training state. Triggers on phrases like "trainer dashboard",
  "show trainer progress", "training loss", "where's the trainer run", "wandb run id
  for trainer", "how's trainer training going".
---

# /trainer:dashboard

The per-block "show me what's happening" surface for trainer. Read-only — it
summarizes state and can launch the monitoring web UI; it never launches or
edits training, config, or `repos/`.

## Step 0 — Orient

Run only from inside the trainer block (`./config.yaml`, `meta_info.name == 'trainer'`);
otherwise abort. Resolve the run output root from `config.yaml`
(`training.output_dir`): a relative value lives at `artifacts/model/<basename>`,
an absolute value is used as-is. Runs land under `artifacts/model/<run>/`.

## Step 1 — Quick textual status (default deliverable)

Agents can't open a browser, so the default output is a text summary parsed
directly from the latest run's artifacts — no daemon, no side effects.

1. Find the active run dir (the resolved `output_dir`, or the most recently
   modified immediate subdir of `artifacts/model/` containing
   `trainer_log.jsonl` or `trainer_state.json`).
2. Read the **last** line of `<run>/trainer_log.jsonl` for live step / loss /
   learning_rate / epoch / percentage / elapsed / remaining. If absent, fall
   back to the last `log_history` entry in `<run>/trainer_state.json`.
3. If `<run>/train_results.json` (or `all_results.json`) exists, the run is
   finished — read `final_loss` / `train_runtime` / `total_steps` from there.

State = `not started` (no run dir) / `training` (jsonl modified < 3 min ago and
percentage < 100) / `done` (results json present or percentage ≈ 100).

## Step 2 — Gather the extras

Read from `config.yaml → runtime_info.output` (populated by `train.sh` STEP 3):

- `checkpoint_path.value` — latest checkpoint dir
- `training_curves.value` — **WandB run id** (e.g. `lge1jzzt`). With
  `wandb_mode: offline` there's no public URL (the run lives under
  `artifacts/wandb/`); with `online` it's
  `https://wandb.ai/<entity>/<project>/runs/<run_id>` — only print the full URL
  if you can confirm entity/project, otherwise print the run id.
- `training_metrics.value` — `final_loss`, `train_runtime`, `total_steps`
- `artifacts.{train_results, train_loss_plot, training_log}` — file paths

And the newest `runs[]` entry from `artifacts/index.yaml` (written by
`scripts/archive_run.sh`): `id`, `started_at`, `completed_at`, `status`,
`archive`, `notes`.

## Step 3 — Offer the live web UI

For a rich live view (loss curves, multi-run compare, eval/perf panels, wandb),
the human can launch the dashboard webui:

```bash
cd dashboard && ./start_dashboard.sh          # builds frontend (first run), serves :8091
# public link (no DNS/login needed) — prints an https://<random>.trycloudflare.com URL:
cd dashboard && TUNNEL=true ./start_dashboard.sh
```

It reads `../artifacts/model` (runs) and `../artifacts/logs` (logs) by default.
Only launch it when the user asks — it's a long-running server. On a headless
host, just give the command and the textual summary; don't try to open a browser.

## Step 4 — Print the summary

```
## trainer dashboard — <CWD>

Status: <not started | training (<step>/<total>, <pct>%) | done>
Model:  <basename of model_name_or_path>   Dataset: <data_name>
Config: gbs=<N> lr=<lr> epochs=<E> template=<t>

Progress (latest trainer_log.jsonl):
  step <cur>/<total>  loss=<loss>  epoch=<e>  elapsed=<t>  eta=<t>

Final (if done):
  final_loss=<v>  runtime=<v>  total_steps=<v>

Artifacts:
  checkpoint:  <checkpoint_path.value>
  loss plot:   <train_loss_plot>      (training_loss.png)
  train log:   <training_log>
  wandb:       run_id=<id>  mode=<wandb_mode>

Latest run (artifacts/index.yaml): <id> — <status> — <notes>

Live web UI:  cd dashboard && ./start_dashboard.sh   (:8091)
```

## Guardrails

- Read-only: never edit `config.yaml`, launch training, or write artifacts.
- Only start the web server when the user explicitly asks; it's long-running.
- Never modify `repos/`. Local block — no SSH.
## Shared LegoFlow CLI

The canonical execution command for this skill is `./bin/legoflow dashboard trainer`. Claude Code and Codex use this same command; this skill supplies the agent-specific confirmation, reporting, and artifact-analysis workflow around it.
## LegoFlow Command Convention

The canonical command for this skill is `/trainer:dashboard`. The shared CLI accepts the same command as `./bin/legoflow /trainer:dashboard` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
