---
name: legoflow-check
description: Validate LegoFlow's root configuration, block dependencies, credentials, repositories, and runtime prerequisites without starting jobs.
---

# Check LegoFlow

Read `AGENTS.md`, `config.yaml`, and the relevant block instructions. Run `bash scripts/dryrun.sh` for the root pipeline or the target block's `scripts/dryrun.sh` for a single block. Summarize warnings and failures, including missing environment variables, submodule state, GPU/Docker requirements, and output paths. Do not start a job.
