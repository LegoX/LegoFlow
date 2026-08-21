---
name: check
description: Validate the trainer dataset, model, GPU, environment, and DeepSpeed configuration without training.
---

# Check Trainer

Read `blocks/trainer/AGENTS.md`, `CLAUDE.md`, and `config.yaml`. Run `./bin/legoflow check trainer`; report resolved dataset, model, output directory, GPU count, DeepSpeed profile, and credential requirements. Do not allocate a training run.
## LegoFlow Command Convention

The canonical command for this skill is `/trainer:check`. The shared CLI accepts the same command as `./bin/legoflow /trainer:check` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
