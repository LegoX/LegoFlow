---
name: check
description: Run the tracer preflight without launching Harbor trajectory jobs.
---

# Check Tracer

Read `blocks/tracer/AGENTS.md`, `CLAUDE.md`, and `config.yaml`. Run `./bin/legoflow check tracer`; report task source, selected Harbor scaffold, model endpoint, LiteLLM, Docker, submodule, and artifact requirements. Do not launch Harbor.
## LegoFlow Command Convention

The canonical command for this skill is `/tracer:check`. The shared CLI accepts the same command as `./bin/legoflow /tracer:check` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
