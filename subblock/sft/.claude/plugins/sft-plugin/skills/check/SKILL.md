---
name: check
description: >
  Preflight the sft block: validate config.yaml schema; verify
  `nvidia-smi` reports the expected GPU count (typically 8); confirm the
  deepspeed ZeRO-3 config file exists and parses; confirm the SFT dataset
  is registered in LLaMA-Factory and the source rows exist; verify
  WANDB_API_KEY is set (or `wandb_mode: disabled`); confirm the base model
  path exists; run `scripts/dryrun.sh`. Read-only. Triggers on phrases like
  "check sft", "preflight sft", "is sft ready", "diagnose sft",
  "validate sft config".
---

# /sft:check

**STATUS: stub — fill in.**

Per the block plugin guidelines, `:check` is the read-only preflight.
Reports all failures in one pass.

## Intent

1. **Schema** — `config.yaml` parses; `meta_info.name == 'sft'`.
2. **GPUs** — `nvidia-smi` reports `>= infrastructure.gpus_per_node`
   visible devices; none above a sane memory-busy threshold.
3. **Deepspeed** — `training.deepspeed` config file exists and parses.
4. **Dataset** — `dataset.name` is registered in
   `repos/LLaMA-Factory/data/dataset_info.json`; the referenced JSON exists
   and has `> 0` rows.
5. **WandB** — `WANDB_API_KEY` is set OR `experiment.wandb_mode: disabled`.
6. **Base model** — `model.model_path` directory exists and contains a
   `config.json` (or HF model dir contract).
7. **dryrun.sh** — run if present.

## TODO

- [ ] Decide whether to verify training-data row schema (LF format) or
      defer to first-step failure.
