# Terminal Curator plugin

Operate a bounded terminal-task curation batch from `config.yaml`. This plugin
is the canonical workflow shared by Claude Code and the Codex wrappers in
`plugins/terminal-curator/`.

| Skill | Purpose |
| --- | --- |
| `/terminal-curator:setup` | Initialize pinned dependencies and environments. |
| `/terminal-curator:check` | Read-only source, config, pin, Docker, and runtime checks. |
| `/terminal-curator:run` | Check, resolve authorization, execute a finite batch, and inspect evidence. |
| `/terminal-curator:dashboard` | Read progress and optionally render a local HTML snapshot. |

Use environment variables for credentials. The upstream submodule is read-only.
Tasks are publishable only after independent fresh Oracle=1 / NOP=0 Harbor
trials; downstream rollout is a separate Tracer operation.

See `blocks/terminal-curator/CLAUDE.md` and the block's `docs/content/docs/`
for configuration and artifact contracts.
