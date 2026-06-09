# trajgen — block-local operating plugin

Packages the trajgen block's detailed operating procedures into slash
commands. These complement the repo-wide [`root-plugin`](../../../../../.claude/plugins/root-plugin/)
plugin (`/root:create`, `/root:check`, `/root:run`): `root` handles the
generic block contract (preflight, execute `start.sh`, archive); this plugin
holds the trajgen-specific know-how that the generic commands cannot infer.

The block's `CLAUDE.md` references these commands instead of inlining every
procedure, so the contract stays short and the detail lives here.

## Commands

| Command | What it does |
| --- | --- |
| `/trajgen:setup` | Bootstrap: clone or update the read-only `harbor` and `swe_data_process` repos at their pinned commits, build the uv/venv environments, initialise the ledger if needed, and run dryrun. Task staging is handled by `scripts/start.sh` or an explicit `prepare_tasks.sh` request. |
| `/trajgen:check` | Preflight: schema + uv envs + LLM endpoint `/models` + swegen task source + LiteLLM port free + consumption ledger sanity. Read-only. |
| `/trajgen:dashboard` | Generate or serve the local HTML progress board, run/restart the Cloudflare Pages sync loop (`trajgen-cf` tmux session), and manually refresh one job's SFT data/stats via `scripts/convert_trajectories.sh`. |
| `/trajgen:run` | Preflight via `/trajgen:check`, then `scripts/start.sh` — `prepare_tasks.sh` → Harbor → optional conversion — plus post-run ledger / exclude-list / status bookkeeping. |

## Relationship to `/root:run`

`/root:run trajgen` runs the generic preflight then executes `scripts/start.sh`
and archives the result. `/trajgen:run` documents the trajgen-specific layer
that `start.sh` orchestrates (LiteLLM proxy lifecycle, task-exclusion wiring,
post-run ledger bookkeeping). Use `/root:run` to execute; consult
`/trajgen:run` for the operating detail and the manual post-run steps.

Per the block plugin guidelines, **no `/trajgen:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/trajgen/`.

## Layout

This plugin lives inside the block-local marketplace at
`subblock/trajgen/.claude/plugins/` (manifest at
`.claude/plugins/.claude-plugin/marketplace.json`, registered by
`.claude/settings.json`) — the same layout as the root block's `.claude/plugins/`:

```
.claude/plugins/
├── .claude-plugin/marketplace.json    # marketplace catalog (name: "trajgen-block")
└── trajgen-plugin/
    ├── .claude-plugin/plugin.json     # manifest (name: "trajgen")
    ├── README.md                      # this file
    └── skills/
        ├── setup/SKILL.md             # /trajgen:setup
        ├── check/SKILL.md             # /trajgen:check
        ├── dashboard/SKILL.md         # /trajgen:dashboard
        └── run/SKILL.md               # /trajgen:run
```

Every skill wraps scripts under `subblock/trajgen/scripts/` (and
`dashboard/`); it documents the flags, ordering, and side effects rather than
reimplementing them. Run `/reload-plugins` after editing any file here.
