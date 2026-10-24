---
name: dashboard
description: >
  Print the unified project dashboard for the root block tree. Aggregates
  every child's latest archived run into one read-only textual table.
---

# /root:dashboard

Show every registered block, including both `curator` and `terminal-curator`,
even when only one is the active task producer. Label the selected producer
from Tracer's `task_source.local_block` when `provider: local`; `external`
or a non-local provider means neither producer is active. An unused producer with no artifacts
is a normal no-runs state. Reading its dashboard does not require API keys or
activate its setup/run workflow.

Run only from the repository root. Read config.yaml and verify
meta_info.name is legoflow, then execute:

    python3 scripts/root_dashboard.py --root .

The table is the required deliverable. It must contain every block declared in
meta_info.blocks and these columns:

    block | last_run | status | started_at | duration | notes

An absent artifacts/index.yaml is reported as never-run, not as an error.
After the aggregate table, inspect each block's configured static output paths
and report whether they currently exist. Do not start child dashboards or
servers unless the user explicitly requests one. This workflow is read-only.
