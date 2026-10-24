---
name: setup
description: Bootstrap LegoFlow Terminal Curator's pinned repositories and isolated generation/Harbor environments for a configured finite batch. Use for terminal-curator setup, not SWE Curator setup or task generation.
---

# /terminal-curator:setup

Read `blocks/terminal-curator/CLAUDE.md`, the selected `config.yaml`, and
`blocks/terminal-curator/docs/content/docs/usage/configuration.mdx`. Resolve paths
from the repository root; execute commands from `blocks/terminal-curator/`.
Honor an explicit `--config` overlay and use it for every following command.

1. Identify source mode, finite question/page limits, model, output pool, and
   resource host. For a remote resource, operate through SSH and tmux there;
   resolve synchronization first using existing user authorization. Do not
   install dependencies on the orchestration node when execution is remote.
2. Preserve inputs the user already supplied. Ask only for information needed
   by the chosen source: `json` needs a questions JSON path, `tasks` needs a
   candidate pool, and generation modes need an endpoint/model and an
   environment-supplied LLM key. Keep all outputs inside artifacts, keep the
   registry at `<task-pool>/registry.json`, and separate reports from source,
   lifecycle state and immutable publications. Never write a secret into
   tracked config.
3. Execute `bash scripts/setup.sh` (plus `--config <file>` when selected).
   This initializes the pinned Terminal-Lego submodule and creates the
   block environment outside `repos/`. It also prepares Harbor through
   Tracer's pinned checkout. Do not run the upstream `run_pipeline.sh`, edit
   vendored code, or substitute a floating system Harbor installation.
4. Run `/terminal-curator:check` on the execution host. Report ready/missing
   prerequisites, repository SHAs, source/limits, resolved paths, and the next
   command. An absent run index is normal before the first run.

Setup does not collect a batch or launch generation, validation containers,
Tracer, training, evaluation, or a public dashboard. A failed setup is not
permission to patch `repos/`; follow the block definition's vendored-code
warning/confirmation procedure if such a change is truly required.
