---
name: setup
description: Prepare Harbor, LiteLLM, and tracer environments after an approved preflight.
---

# Set Up Tracer

After explicit confirmation, run `./bin/legoflow setup tracer`. Preserve pinned Harbor and data-processing submodules, report environment paths, and do not start a trajectory job as part of setup.
## LegoFlow Command Convention

The canonical command for this skill is `/tracer:setup`. The shared CLI accepts the same command as `./bin/legoflow /tracer:setup` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
