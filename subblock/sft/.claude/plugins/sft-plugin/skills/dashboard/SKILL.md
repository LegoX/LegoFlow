---
name: dashboard
description: >
  Read-only progress surface for the sft block. Regenerates
  dashboard/status.mdx via scripts/update_status.py (config summary +
  current step/loss/throughput parsed from the trainer log + final results
  from train_results.json), then prints a textual table: status
  (not-started / training / done), config summary, latest step + loss +
  ETA, final metrics, the WandB run id from runtime_info.output, the
  training_loss.png path, and the newest artifacts/index.yaml run entry.
  Never modifies training state. Triggers on phrases like "sft dashboard",
  "show sft progress", "training loss", "where's the sft run",
  "wandb run id for sft", "how's sft training going".
---

# /sft:dashboard

The per-block "show me what's happening" surface for sft. Read-only — it
regenerates the status doc and prints a summary; it never launches, edits
config, or touches `repos/`.

## Step 0 — Orient

Run only from inside the sft block (`./config.yaml`, `meta_info.name ==
'sft'`); otherwise abort. Resolve `PY = <meta_info.environment.sft_uv>/bin/
python` (default `artifacts/env/lf/bin/python`); fall back to `python3` if
that env isn't built yet (the status generator only needs `pyyaml`).

## Step 1 — Refresh the status doc

`scripts/update_status.py` does the parsing for us — it reads `config.yaml`,
the resolved `output_dir`'s `trainer_log.jsonl` and `train_results.json`,
and writes `dashboard/status.mdx`. Run it **once** (not `--loop`):

```bash
"$PY" scripts/update_status.py --block-dir . 2>&1 || python3 scripts/update_status.py --block-dir .
```

Then read `dashboard/status.mdx` — that file is the authoritative rendered
state (status, config summary, progress table, final results, loss-plot
link). Note: it's written in Chinese (训练状态 / 配置摘要 / 训练进度 /
最终结果); present it faithfully, translating section labels if the user
prefers English.

## Step 2 — Gather the extras `status.mdx` doesn't surface

Read from `config.yaml → runtime_info.output` (populated by `train.sh`
STEP 3 after a run):

- `checkpoint_path.value` — latest checkpoint dir
- `training_curves.value` — **WandB run id** (e.g. `lge1jzzt`). With
  `wandb_mode: offline` there's no public URL; the run lives under
  `artifacts/wandb/`. With `online`, the URL is
  `https://wandb.ai/<entity>/<project>/runs/<run_id>` — only print the full
  URL if you can confirm entity/project; otherwise print the run id.
- `training_metrics.value` — `final_loss`, `train_runtime`, `total_steps`
- `artifacts.{train_results, train_loss_plot, training_log}` — file paths

And the run history from `artifacts/index.yaml` (newest `runs[]` entry):
id, status, started/completed, label/detail.

If a live run is in progress, note it (`pgrep -af 'llamafactory.cli
train'`) and that `status.mdx` auto-refreshes every 30s during training.

## Step 3 — Print the summary

A compact textual table is the default deliverable:

```
## sft dashboard — <CWD>

Status: <not started | training (<step>/<total>, <pct>%) | done>
Model:  <basename of model_name_or_path>   Dataset: <data_name>
Config: gbs=<N> lr=<lr> epochs=<E> template=<t>

Progress (from status.mdx):
  step <cur>/<total>  loss=<loss>  epoch=<e>  elapsed=<t>  eta=<t>

Final (if done):
  final_loss=<v>  runtime=<v>  total_steps=<v>

Artifacts:
  checkpoint:  <checkpoint_path.value>
  loss plot:   <train_loss_plot>      (training_loss.png)
  train log:   <training_log>
  wandb:       run_id=<id>  mode=<wandb_mode>

Latest run (artifacts/index.yaml): <id> — <status> — <label>
```

Point the user at `dashboard/status.mdx` for the rendered version (it
embeds the loss curve). On a headless host, just print paths — don't try to
open a browser or image viewer.

There is no bundled web UI for this block today (unlike rl's `webui/`);
the `dashboard/*.mdx` files are the surface. If the user asks for a live
web dashboard, say it's not implemented here and offer the textual view
plus the `status.mdx` auto-refresh.

## Guardrails

- Read-only: never edit `config.yaml`, launch training, or write anything
  except via `update_status.py` (which only writes `dashboard/status.mdx`).
- Run `update_status.py` **once** here — never with `--loop` (that's the
  background updater `train.sh` owns during a run).
- Never modify `repos/`. Local block — no SSH.
