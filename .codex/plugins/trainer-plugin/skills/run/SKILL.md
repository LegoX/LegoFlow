---
name: run
description: Launch an approved supervised fine-tuning run and inspect its outputs.
---

# Run Trainer

Never start training without a successful check and explicit confirmation. Run `./bin/legoflow run trainer` in a named tmux session, monitor logs without exposing secrets, and inspect checkpoints, metrics, curves, and archive metadata. Preserve previous runs.
## LegoFlow Command Convention

The canonical command for this skill is `/trainer:run`. The shared CLI accepts the same command as `./bin/legoflow /trainer:run` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
