---
name: setup
description: Prepare the curator environment after an approved preflight.
---

# Set Up Curator

After explicit confirmation, run `./bin/legoflow setup curator`. Preserve the pinned `legoflow-curator` submodule and report the resulting environment and CLI version.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:setup`. The shared CLI accepts the same command as `./bin/legoflow /curator:setup` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
