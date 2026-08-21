---
name: setup
description: Prepare Harbor and LiteLLM evaluation environments after an approved preflight.
---

# Set Up Evaluator

After explicit confirmation, run `./bin/legoflow setup evaluator`. Preserve the pinned Harbor submodule, verify the registry, and report environment paths without starting an evaluation.
## LegoFlow Command Convention

The canonical command for this skill is `/evaluator:setup`. The shared CLI accepts the same command as `./bin/legoflow /evaluator:setup` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
