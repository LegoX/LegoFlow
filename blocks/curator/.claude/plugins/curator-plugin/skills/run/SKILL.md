---
name: run
description: >
  Uniform-interface compatibility adapter retained so every block plugin has a
  `/<block>:run` skill. Forward the user's complete request to
  `/curator:create-tasks` without changing arguments or duplicating its
  workflow.
---

# /curator:run (compatibility)

This uniform-interface compatibility adapter exists only to preserve the
repository's common `/<block>:run` plugin layout. `/root:run curator` does not
invoke this adapter; root targeting directly executes Curator's all-language
`scripts/start.sh`.

Immediately invoke `/curator:create-tasks` with the user's complete request and
arguments. Do not repeat preflight, confirmation, mode selection, launch, or
reporting logic here; the canonical skill owns all behavior.

For direct Curator operation, tell users to invoke `/curator:create-tasks`.
