# rl — block-local skills for the RL training block

Slash commands tailored to `subblock/rl/`. They wrap `scripts/dryrun.sh`,
`scripts/start.sh`, and `config.yaml` with RL-specific knowledge (vLLM
KV-head divisibility, Harbor k8s reachability, venv editable-install
verification, LiteLLM/Ray port conflicts, long-running background launch).

For the generic block-system skills (only `/root:create` lives there, since
new blocks are only ever created at the root level), see the root-level
`root` plugin under `.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/rl:setup`     | One-shot environment bootstrap: bring up the venv, sync `repos/`, and fill in `config.yaml` (API keys, paths, dashboard wiring). |
| `/rl:check`     | RL-specific preflight: schema + dryrun.sh + kubectl reachability + port conflicts + venv editable-install verification + running-job sanity. Read-only. |
| `/rl:dashboard` | Launch the RL training dashboard (webui) and open it in the browser. |
| `/rl:run`       | Preflight, then launch `scripts/start.sh` (background by default with `nohup setsid`, since training runs for hours). Stamps `status.phase: running` and writes the launch metadata into `config.yaml`. |

Per the block plugin guidelines, **no `/rl:create`** — new blocks are only
created via `/root:create`. Per-run experiment slots (the legacy meaning of
`/rl:create`) are handled inside `/rl:setup` or by hand.

Run them from inside `subblock/rl/`.

## Layout

```
rl-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md        # /rl:setup
    ├── check/SKILL.md        # /rl:check
    ├── dashboard/SKILL.md    # /rl:dashboard
    └── run/SKILL.md          # /rl:run
```
