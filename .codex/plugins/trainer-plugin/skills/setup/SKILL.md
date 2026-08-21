---
name: setup
description: Prepare LLaMA-Factory and data-processing environments after an approved preflight.
---

# Set Up Trainer

After explicit confirmation, run `./bin/legoflow setup trainer`. Preserve the pinned submodules and report the Python/uv environment, installed package revisions, and available GPU topology.
## LegoFlow Command Convention

The canonical command for this skill is `/trainer:setup`. The shared CLI accepts the same command as `./bin/legoflow /trainer:setup` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
