---
name: collect-prs
description: Collect candidate GitHub pull requests for LegoFlow after approval.
---

# Collect Pull Requests

Run `./bin/legoflow collect-prs` only after check and confirmation. Use `GITHUB_TOKEN` from the environment, never echo it, and record filters, pagination, rate-limit status, and the artifact path. Do not create tasks in this step.
## LegoFlow Command Convention

The canonical command for this skill is `/curator:collect-prs`. The shared CLI accepts the same command as `./bin/legoflow /curator:collect-prs` and dispatches it to the module runtime. Claude Code and Codex use the same command convention.
