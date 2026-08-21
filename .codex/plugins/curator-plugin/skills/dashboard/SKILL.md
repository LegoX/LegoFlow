---
name: dashboard
description: Inspect curator collection and task-verification artifacts.
---

# Inspect Curator

Use `./bin/legoflow dashboard curator` in read-only mode where possible. Summarize candidate PRs, generated tasks, verification pass rates, language/tag slices, and the latest archive entry.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:dashboard`. The shared CLI accepts the same command as `./bin/legoflow /curator:dashboard` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
