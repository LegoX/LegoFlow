# tracer — block-local operating plugin

Packages the tracer block's detailed operating procedures into slash
commands. These complement the repo-wide [`root-plugin`](../../../../../.claude/plugins/root-plugin/)
plugin (`/root:create`, `/root:check`, `/root:run`): `root` resolves and
delegates block targets; this plugin owns Tracer preflight, execution,
archiving, and tracer-specific operating details.

The block's `CLAUDE.md` references these commands instead of inlining every
procedure, so the contract stays short and the detail lives here.

## Commands

| Command | What it does |
| --- | --- |
| `/tracer:setup` | Bootstrap: clone or update the read-only `harbor` and `swe_data_process` repos at their pinned commits, build the uv/venv environments, initialise the ledger if needed, and run dryrun. Task staging is handled by `scripts/start.sh` or an explicit `prepare_tasks.sh` request. |
| `/tracer:check` | Preflight: schema + uv envs + LLM endpoint `/models` + curator task source + LiteLLM port free + consumption ledger sanity. Read-only. |
| `/tracer:dashboard` | Generate or serve the local HTML progress board, run/restart the Cloudflare Pages sync loop (`tracer-cf` tmux session), and manually refresh one job's SFT data/stats via `scripts/convert_trajectories.sh`. |
| `/tracer:run` | Preflight via `/tracer:check`, then `scripts/start.sh` — `prepare_tasks.sh` → Harbor → optional conversion — plus post-run ledger / exclude-list / status bookkeeping. |

## Relationship to `/root:run`

`/root:run tracer` delegates to `/tracer:run` and waits for it. The block-owned
skill performs preflight, executes and archives `scripts/start.sh`, and handles
the tracer-specific layer (LiteLLM proxy lifecycle, task-exclusion wiring, and
post-run ledger bookkeeping).

Per the block plugin guidelines, **no `/tracer:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/tracer/`.

## Layout

This plugin lives inside the block-local marketplace at
`subblock/tracer/.claude/plugins/` (manifest at
`.claude/plugins/.claude-plugin/marketplace.json`, registered by
`.claude/settings.json`) — the same layout as the root block's `.claude/plugins/`:

```
.claude/plugins/
├── .claude-plugin/marketplace.json    # marketplace catalog (name: "tracer")
└── tracer-plugin/
    ├── .claude-plugin/plugin.json     # manifest (name: "tracer")
    ├── README.md                      # this file
    └── skills/
        ├── setup/SKILL.md             # /tracer:setup
        ├── check/SKILL.md             # /tracer:check
        ├── dashboard/SKILL.md         # /tracer:dashboard
        └── run/SKILL.md               # /tracer:run
```

Every skill wraps scripts under `subblock/tracer/scripts/` (and
`dashboard/`); it documents the flags, ordering, and side effects rather than
reimplementing them. Run `/reload-plugins` after editing any file here.
