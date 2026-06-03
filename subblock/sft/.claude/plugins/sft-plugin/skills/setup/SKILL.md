---
name: setup
description: >
  Bootstrap the sft block: install `repos/LLaMA-Factory/` (editable),
  ensure `repos/swe_data_process/src` is on PYTHONPATH (or installed),
  register the SFT dataset in `data/dataset_info.json` (sourcing from
  trajgen's `lf.json` under
  `subblock/trajgen/artifacts/sft_data/<job>/lf.json`), then fill in
  `runtime_info.input` — model path, training hyperparameters, WandB
  credentials. Prompts only for unfilled fields. Idempotent. Triggers on
  phrases like "set up sft", "bootstrap sft", "install LLaMA-Factory",
  "prepare sft before training".
---

# /sft:setup

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:setup` brings the block from a fresh
clone to "`:check` passes".

## Intent

1. **Repos** — clone `repos/LLaMA-Factory/` and `repos/swe_data_process/`
   if missing; pin to commits declared in `meta_info.repos`.
2. **Env** — create / verify the venv; `pip install -e repos/LLaMA-Factory/`;
   ensure `swe_data_process` is importable.
3. **Dataset** — read `source.job_dir` (trajgen's job), convert
   trajectories → LF JSON (if `enabled`), register the dataset in
   `repos/LLaMA-Factory/data/dataset_info.json` under `dataset.name`.
4. **Config** — prompt for `runtime_info.input.model.model_path`,
   `training.*`, `experiment.wandb_*`, `credentials.wandb_api_key` (kept
   in env, not config).

## TODO

- [ ] Decide whether `:setup` should also call `/trajgen:check` to confirm
      the upstream trajectory source is ready.
- [ ] Spec how to handle multiple candidate `lf.json`s in trajgen output.
