# rl — block-local skills for the RL training block

Slash commands tailored to `subblock/rl/`. They wrap `scripts/dryrun.sh`,
`scripts/start.sh`, and `config.yaml` with RL-specific knowledge (vLLM
KV-head divisibility, Harbor k8s reachability, venv editable-install
verification, LiteLLM/Ray port conflicts, long-running background launch).

For the generic block-system equivalents, see the root-level `block` plugin
under `.claude/plugins/block-plugin/` — those work on any block, while
these are scoped to `rl` and know its plumbing.

## Commands

| Command | What it does |
| --- | --- |
| `/block:create` | Scaffold a new RL experiment slot under `artifacts/runs/<exp_name>/` (config snapshot, hypothesis, notes). Does not launch. |
| `/block:check`  | RL-specific preflight: schema + dryrun.sh + kubectl reachability + port conflicts + venv editable-install verification + running-job sanity. Read-only. |
| `/block:run`    | Preflight, then launch `scripts/start.sh` (background by default with `nohup setsid`, since training runs for hours). Stamps `status.phase: running` and writes the launch metadata into `config.yaml`. |

Run them from inside `subblock/rl/`.

## Layout

```
block-plugin/
├── .claude-plugin/plugin.json
├── README.md
└── skills/
    ├── create/SKILL.md   # /block:create
    ├── check/SKILL.md    # /block:check
    └── run/SKILL.md      # /block:run
```
