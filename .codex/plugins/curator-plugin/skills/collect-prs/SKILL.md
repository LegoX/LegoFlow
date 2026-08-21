---
name: legoflow-curator-collect-prs
description: Collect candidate GitHub pull requests for LegoFlow after approval.
---

# Collect Pull Requests

Run the existing curator PR collection command only after check and confirmation. Use `GITHUB_TOKEN` from the environment, never echo it, and record filters, pagination, rate-limit status, and the artifact path. Do not create tasks in this step.
