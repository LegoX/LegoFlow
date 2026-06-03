# trajgen — block-local skills for the trajectory-generation block

Slash commands tailored to `subblock/trajgen/`. They wrap Harbor (job
launch + sandbox management), the per-job LiteLLM proxy, and the
swe_data_process trajectory → SFT-data converter.

For the generic block-system command (only `/root:create` lives there),
see `.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/trajgen:setup`     | Bootstrap: build the Harbor uv env + LiteLLM venv + swe_data_process uv env, register the task source, wire the LLM API. |
| `/trajgen:check`     | Preflight: schema + uv envs + LLM endpoint `/models` + swegen task source (`verifiable_tasks.txt` present) + LiteLLM port free + consumption ledger sanity. Read-only. |
| `/trajgen:dashboard` | Show per-job status: tasks consumed, in-flight, done/failed, trajectories produced, SFT conversion progress. |
| `/trajgen:run`       | Preflight, then `scripts/start.sh` — `prepare_tasks.sh` → harbor → `convert_trajectories.sh`. Per the plugin guidelines this may later split into `/trajgen:rollout` and `/trajgen:convert-sft` substeps. |

Per the block plugin guidelines, **no `/trajgen:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/trajgen/`.

## Layout

```
trajgen-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                  # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md          # /trajgen:setup
    ├── check/SKILL.md          # /trajgen:check
    ├── dashboard/SKILL.md      # /trajgen:dashboard
    └── run/SKILL.md            # /trajgen:run
```
