---
name: create
description: Scaffold a new LegoFlow block that follows the block contract and includes Claude and Codex agent instructions.
---

# Create a LegoFlow Block

Use the root block definition and existing examples. A new block must include `config.yaml`, `AGENTS.md`, `CLAUDE.md`, `scripts/`, `artifacts/`, and matching Claude/Codex plugin metadata. Validate its configuration and run the structural tests before presenting it as ready.

## Shared LegoFlow CLI

Use `./bin/legoflow create` to print the shared block-creation contract before the agent-assisted intake and scaffold. Claude Code and Codex use the same contract and finish by running `./bin/legoflow check <block>`.
## LegoFlow Command Convention

The canonical command for this skill is `/root:create`. The shared CLI accepts the same command as `./bin/legoflow /root:create` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
