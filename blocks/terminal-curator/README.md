# Terminal Curator

An agent-operated LegoFlow block that turns bounded Stack Overflow batches,
Terminal-Lego questions JSON, or local candidates into Harbor terminal tasks.
It runs alongside the SWE `curator` block and shares the downstream Tracer,
Trainer, and Evaluator pipeline.

Fill `config.yaml`, supply credentials through the environment, and use the
block plugin:

| Workflow | Claude Code | Codex |
| --- | --- | --- |
| Setup | `/terminal-curator:setup` | `$terminal-curator-setup` |
| Check | `/terminal-curator:check` | `$terminal-curator-check` |
| Run | `/terminal-curator:run` | `$terminal-curator-run` |
| Progress | `/terminal-curator:dashboard` | `$terminal-curator-dashboard` |

The equivalent scripts are `scripts/{setup,dryrun,start,stop,clean,archive_run}.sh`.
Checks run before generation. Publishable tasks must pass Oracle=1 and NOP=0 in
separate fresh Harbor containers. The authoritative output is
`artifacts/tasks/verifiable_tasks.txt`; candidate directories are not a dataset.

`artifacts/tasks` points to the newest immutable batch under
`artifacts/tasks-versions/`. Tracer stages links to the resolved version,
keeping existing rollout inputs stable when another batch is published.

Tracer reads this pool with `task_source.provider: local`,
`task_source.local_block: terminal-curator`, and
`task_source.terminal_tasks_dir: ../terminal-curator/artifacts/tasks`.

Evaluator reads the generated `artifacts/tasks/registry.json` through its
existing `harbor_registry` provider as `terminal-curator@1.0`. See the
[Evaluator configuration](docs/content/docs/evaluator-handoff.mdx) for the
registry path and required `no_hack: false` setting.

- [Getting started](docs/content/docs/getting-started.mdx)
- [Configuration](docs/content/docs/configuration.mdx)
- [Workflow and Tracer hand-off](docs/content/docs/workflow.mdx)
- [Dashboard](dashboard/README.md)
- [Agent contract](CLAUDE.md)

The upstream Terminal-Lego submodule remains pinned and read-only. All
generation, validation jobs, environments, reports, and archives stay under
this block's ignored `artifacts/` directory. No training or rollout starts as a
side effect of curation.
