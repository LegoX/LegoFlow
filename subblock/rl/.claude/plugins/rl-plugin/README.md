# rl — block-local skills for the RL training block

Slash commands tailored to `subblock/rl/`. They wrap `scripts/dryrun.sh`,
`scripts/start.sh`, and `config.yaml` with RL-specific knowledge (vLLM
KV-head divisibility, Harbor k8s reachability, venv editable-install
verification, LiteLLM/Ray port conflicts, long-running background launch).

For the generic block-system commands (only `/root:create` lives there),
see the root plugin under `<repo_root>/.claude/plugins/`.

## Commands

| Command | What it does |
| --- | --- |
| `/rl:setup`     | Bootstrap a fresh clone to "`/rl:check` passes": tooling preflight, submodules at pinned commits (harbor-verl-train / harbor / verl + patch), build or verify the venv via `setup_env.sh`, fill `runtime_info.input` gaps. Idempotent; never trains. |
| `/rl:check`     | RL-specific preflight: schema + dryrun.sh + backend reachability + port conflicts + venv editable-install verification + live job/GPU probes. Read-only. |
| `/rl:run`       | Preflight, then launch `scripts/start.sh` (background by default with `nohup setsid`, since training runs for hours). Run archiving (`artifacts/index.yaml`) is owned by `start.sh`'s EXIT trap → `archive_run.sh`, not by this skill. |
| `/rl:dashboard` | Surface training state — textual summary by default (live job, latest run, log tails, wandb); optionally start/stop the vendored webui (`dashboard/serve.sh`) or publish to Cloudflare Pages. Read-only monitoring. |
| `/rl:experiment` | Scaffold a new RL experiment slot under `artifacts/runs/<exp_name>/` (config snapshot, hypothesis, notes). Does not launch. rl-specific extra beyond the uniform skill set. |

Per the block plugin guidelines, **no `/rl:create`** — new blocks are only
created via `/root:create`. (`/rl:experiment` scaffolds *experiment slots*,
not blocks.)

Run them from inside `subblock/rl/`.

## Layout

```
rl-plugin/
├── .claude-plugin/plugin.json
├── README.md
└── skills/
    ├── setup/SKILL.md       # /rl:setup
    ├── check/SKILL.md       # /rl:check
    ├── run/SKILL.md         # /rl:run
    ├── dashboard/SKILL.md   # /rl:dashboard
    └── experiment/SKILL.md  # /rl:experiment
```
