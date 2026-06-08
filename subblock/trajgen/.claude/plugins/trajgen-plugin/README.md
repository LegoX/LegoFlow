# trajgen — block-local operating plugin

Packages the trajgen block's detailed operating procedures into slash
commands. These complement the repo-wide [`root-plugin`](../../../../../../.claude/plugins/root-plugin/)
plugin (`/root:create`, `/root:check`, `/root:run`): `root` handles the
generic block contract (preflight, execute `start.sh`, archive); this plugin
holds the trajgen-specific know-how that the generic commands cannot infer.

The block's `CLAUDE.md` references these commands instead of inlining every
procedure, so the contract stays short and the detail lives here.

## Commands

| Command | What it does |
| --- | --- |
| `/trajgen:setup` | Bootstrap: clone or update the read-only `harbor` and `swe_data_process` repos at their pinned commits, build the uv/venv environments, and copy verified tasks into `artifacts/tasks/<dataset>/` (filtered by swegen's `verifiable_tasks.txt`). |
| `/trajgen:check` | Preflight: schema + uv envs + LLM endpoint `/models` + swegen task source + LiteLLM port free + consumption ledger sanity. Read-only. |
| `/trajgen:run-job` | Run one Harbor trajectory job: dryrun preflight, generate the per-job LiteLLM proxy config and start the proxy, launch Harbor with `--exclude-task-name` flags from `HARBOR_EXCLUDE_TASKS`, then stop the proxy, inspect `artifacts/jobs/<job>/`, and update `consumption_ledger.yaml` + `HARBOR_EXCLUDE_TASKS` + `config.yaml`'s `status`. |
| `/trajgen:convert-sft` | Convert one Harbor job's trajectories into `artifacts/sft_data/<job>/im.jsonl` and `lf.json` (LLaMA-Factory ShareGPT) via the `swe_data_process` converter, with scaffold auto-detection and `--skip-unchanged` polling support. |
| `/trajgen:dashboard` | Generate or serve the local HTML progress board, or run/restart the Cloudflare Pages sync loop (`trajgen-cf` tmux session) that publishes it online. |
| `/trajgen:run` | Preflight via `/trajgen:check`, then `scripts/start.sh` — `prepare_tasks.sh` → harbor → `convert_trajectories.sh`. |

## Relationship to `/root:run`

`/root:run trajgen` runs the generic preflight then executes `scripts/start.sh`
and archives the result. `/trajgen:run-job` documents the trajgen-specific layer
that `start.sh` orchestrates (LiteLLM proxy lifecycle, task-exclusion wiring,
post-run ledger bookkeeping). Use `/root:run` to execute; consult
`/trajgen:run-job` for the operating detail and the manual post-run steps.

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
        ├── run-job/SKILL.md           # /trajgen:run-job
        ├── convert-sft/SKILL.md       # /trajgen:convert-sft
        ├── dashboard/SKILL.md       # /trajgen:dashboard
        └── run/SKILL.md               # /trajgen:run
```

Every skill wraps scripts under `subblock/trajgen/scripts/` (and
`dashboard/`); it documents the flags, ordering, and side effects rather than
reimplementing them. Run `/reload-plugins` after editing any file here.
