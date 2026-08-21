---
name: run
description: Run the curator's approved compatibility workflow using its existing scripts.
---

# Run Curator

This is a compatibility entry point. Resolve whether the user requested PR collection or task creation, then dispatch to `./bin/legoflow collect-prs` or `./bin/legoflow create-tasks`. Never silently run both phases.
