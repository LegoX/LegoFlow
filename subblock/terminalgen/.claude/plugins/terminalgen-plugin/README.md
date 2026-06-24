# terminalgen — block-local skills for the terminal task-generation block

Slash commands tailored to `subblock/terminalgen/`. They wrap the terminal-lego
pipeline (StackOverflow scrape → LLM task generation → Docker verification) and
the optional flat-merge helper.

For the generic block-system command (only `/root:create` lives there, since
new blocks are only ever created at the root level), see
`.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/terminalgen:setup`     | Bootstrap: init `repos/terminal-lego` submodule, build the venv, verify env vars (OPENAI_API_*, MODEL_NAME, SO_API_KEY). |
| `/terminalgen:check`     | Preflight: schema + env vars + StackExchange key reachability + LLM endpoint + docker daemon + dryrun. Read-only. |
| `/terminalgen:dashboard` | Show progress per domain: questions scraped, tasks generated, verifiable rate. |
| `/terminalgen:run`       | Launch the pipeline (`scripts/start.sh`) — SO scrape → task generation → Docker verification. Modes: smoke / single-domain / full. |

Per the block plugin guidelines, **no `/terminalgen:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/terminalgen/`.

## Layout

```
terminalgen-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                  # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md          # /terminalgen:setup
    ├── check/SKILL.md          # /terminalgen:check
    ├── dashboard/SKILL.md      # /terminalgen:dashboard
    └── run/SKILL.md            # /terminalgen:run
```
