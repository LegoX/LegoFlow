---
name: trainer-setup
description: Run the canonical trainer setup workflow.
---

# Canonical LegoFlow Skill

Read and follow `blocks/trainer/.claude/plugins/trainer-plugin/skills/setup/SKILL.md` in full. It is the canonical workflow shared by Claude Code and Codex; do not duplicate or reinterpret its safety gates, reporting requirements, or runtime procedure here.

## Unified Invocation

Invoke this Codex skill as `$trainer-setup`. The shared CLI fallback is `./bin/legoflow /trainer:setup` when the agent does not expose slash commands directly.
