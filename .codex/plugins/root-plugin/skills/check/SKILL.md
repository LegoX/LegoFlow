---
name: check
description: Validate LegoFlow's root configuration, block dependencies, credentials, repositories, and runtime prerequisites without starting jobs.
---

# Check LegoFlow

Read `AGENTS.md`, `config.yaml`, and the relevant block instructions. Run `./bin/legoflow check --full` for the root pipeline or `./bin/legoflow check <block>` for a single block. Summarize warnings and failures, including missing environment variables, submodule state, GPU/Docker requirements, and output paths. Do not start a job.
## LegoFlow Command Convention

The canonical command for this skill is `/root:check`. The shared CLI accepts the same command as `./bin/legoflow /root:check` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
