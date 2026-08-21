---
name: check
description: Run the curator preflight without collecting PRs or creating tasks.
---

# Check Curator

Read `blocks/curator/AGENTS.md`, `CLAUDE.md`, and `config.yaml`. Run `./bin/legoflow check curator`; report GitHub credentials, repository access, source filters, output directories, and any blocked prerequisites. Do not mutate curator artifacts.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:check`. The shared CLI accepts the same command as `./bin/legoflow /curator:check` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
