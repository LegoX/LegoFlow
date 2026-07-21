# curator — block-local skills for the SWE task-generation block

Slash commands tailored to `subblock/curator/`. They wrap the swegen CLI,
GitHub PR collection, and per-language task generation/verification.

For the generic block-system command (only `/root:create` lives there, since
new blocks are only ever created at the root level), see
`.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/curator:setup`     | Bootstrap: install `repos/swegen/` editable, verify GitHub/LLM env vars, prepare an optional `gh_token.txt`. |
| `/curator:check`     | Preflight: schema + env vars + GitHub API reachability + a real LLM completion + docker daemon + dryrun. Read-only. |
| `/curator:collect-prs` | Start the separate GitHub PR collector, which writes `artifacts/collected_prs/{lang}_pr_ids.txt`; wait for it to finish before generation. |
| `/curator:create-tasks` | Launch task generation and NOP/Oracle verification from existing PR ID files. It does not collect PRs. |
| `/curator:dashboard` | Show progress per language: PRs collected, tasks generated, verifiable rate. |

Per the block plugin guidelines, **no `/curator:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/curator/`.

## Layout

```
curator-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                  # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md          # /curator:setup
    ├── check/SKILL.md          # /curator:check
    ├── collect-prs/SKILL.md    # /curator:collect-prs
    ├── create-tasks/SKILL.md   # /curator:create-tasks
    ├── dashboard/SKILL.md      # /curator:dashboard
    └── run/SKILL.md            # root compatibility adapter
```
