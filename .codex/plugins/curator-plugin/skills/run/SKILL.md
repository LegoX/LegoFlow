---
name: run
description: Run the curator's approved compatibility workflow using its existing scripts.
---

# Run Curator

This is a compatibility entry point. Resolve whether the user requested PR collection or task creation, then dispatch to `./bin/legoflow collect-prs` or `./bin/legoflow create-tasks`. Never silently run both phases.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:run`. The shared CLI accepts the same command as `./bin/legoflow /curator:run` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
