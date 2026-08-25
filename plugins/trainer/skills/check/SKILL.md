---
name: trainer-check
description: Run the canonical trainer check workflow.
---

# Canonical LegoFlow Skill

Read and follow `blocks/trainer/.claude/plugins/trainer-plugin/skills/check/SKILL.md` in full. It is the canonical workflow shared by Claude Code and Codex; do not duplicate or reinterpret its safety gates, reporting requirements, or runtime procedure here.

## Unified Invocation

Invoke this Codex skill as `$trainer-check`. The shared CLI fallback is `./bin/legoflow /trainer:check` when the agent does not expose slash commands directly.
