---
name: run
description: >
  Compatibility entry point for the root block's uniform `/<block>:run`
  protocol. Forward the user's complete request to `/curator:create-tasks`
  without changing arguments or duplicating its workflow.
---

# /curator:run (compatibility)

This command exists only so `/root:run` can dispatch Curator through the
repository-wide `/<block>:run` interface.

Immediately invoke `/curator:create-tasks` with the user's complete request and
arguments. Do not repeat preflight, confirmation, mode selection, launch, or
reporting logic here; the canonical skill owns all behavior.

For direct Curator operation, tell users to invoke `/curator:create-tasks`.
