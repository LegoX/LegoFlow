# swegen — block-local skills for the SWE task-generation block

Slash commands tailored to `subblock/swegen/`. They wrap the swegen CLI,
GitHub PR collection, and per-language task generation/verification.

For the generic block-system command (only `/root:create` lives there, since
new blocks are only ever created at the root level), see
`.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/swegen:setup`     | Bootstrap: install `repos/swegen/` editable, verify env vars (GITHUB_TOKENS, OPENAI_API_*), prepare `gh_token.txt`. |
| `/swegen:check`     | Preflight: schema + env vars + GitHub API reachability + LLM endpoint `/models` + docker daemon + dryrun. Read-only. |
| `/swegen:dashboard` | Show progress per language: PRs collected, tasks generated, verifiable rate. |
| `/swegen:run`       | Launch the full pipeline (`scripts/start.sh`) — PR fetch → task generation → NOP/Oracle verification. |

Per the block plugin guidelines, **no `/swegen:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/swegen/`.

## Layout

```
swegen-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                  # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md          # /swegen:setup
    ├── check/SKILL.md          # /swegen:check
    ├── dashboard/SKILL.md      # /swegen:dashboard
    └── run/SKILL.md            # /swegen:run
```
