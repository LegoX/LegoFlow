# curator — block-local skills for the SWE task-generation block

Slash commands tailored to `subblock/curator/`. They wrap the swegen CLI,
GitHub PR collection, and per-language task generation/verification.

For the generic block-system command (only `/root:create` lives there, since
new blocks are only ever created at the root level), see
`.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/curator:setup`     | Bootstrap: install `repos/swegen/` editable, verify env vars (GITHUB_TOKENS, OPENAI_API_*), prepare `gh_token.txt`. |
| `/curator:check`     | Preflight: schema + env vars + GitHub API reachability + LLM endpoint `/models` + docker daemon + dryrun. Read-only. |
| `/curator:collect-prs` | Collect GitHub PRs per `config.yaml -> runtime_info.input.pr_collection` into `artifacts/collected_prs/{lang}_pr_ids.txt`. |
| `/curator:dashboard` | Show progress per language: PRs collected, tasks generated, verifiable rate. |
| `/curator:run`       | Launch the full pipeline (`scripts/start.sh`) — PR fetch → task generation → NOP/Oracle verification. |

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
    ├── dashboard/SKILL.md      # /curator:dashboard
    └── run/SKILL.md            # /curator:run
```
