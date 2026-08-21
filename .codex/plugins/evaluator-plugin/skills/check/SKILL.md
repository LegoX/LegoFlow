---
name: check
description: Validate benchmark registry, model endpoint, Harbor environment, and evaluation resources without running tasks.
---

# Check Evaluator

Read `blocks/evaluator/AGENTS.md`, `CLAUDE.md`, and `config.yaml`. Run `./bin/legoflow check evaluator`; report benchmark, version, task count, agent scaffold, endpoint, credentials, Docker, and resource requirements. Do not launch Harbor.
## LegoFlow Command Convention

The canonical command for this skill is `/evaluator:check`. The shared CLI accepts the same command as `./bin/legoflow /evaluator:check` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
