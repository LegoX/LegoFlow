---
name: create-tasks
description: Convert curated pull requests into verified SWE tasks after approval.
---

# Create Verified Tasks

Read the curator task-generation contract and existing artifacts first. After explicit confirmation, run `./bin/legoflow create-tasks`, verify NOP/Oracle results, and report the manifest and rejected-task counts. Preserve prior task pools.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:create-tasks`. The shared CLI accepts the same command as `./bin/legoflow /curator:create-tasks` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
