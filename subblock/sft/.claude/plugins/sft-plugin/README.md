# sft — block-local skills for the supervised fine-tuning block

Slash commands tailored to `subblock/sft/`. They wrap LLaMA-Factory +
DeepSpeed ZeRO-3 training on Qwen3-8B with the trajectory conversion
provided by `swe_data_process`.

For the generic block-system command (only `/root:create` lives there),
see `.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/sft:setup`     | Bootstrap: install `repos/LLaMA-Factory/`, prepare `swe_data_process` PYTHONPATH, register the SFT dataset (from trajgen's `lf.json`), fill in `runtime_info.input` (model path, training hyperparams, WandB). |
| `/sft:check`     | Preflight: schema + GPU availability (8× expected) + deepspeed config + dataset registration + WandB credentials + base model path + dryrun. Read-only. |
| `/sft:dashboard` | Show training progress: current step, loss curve from the latest log, WandB run URL if configured. |
| `/sft:run`       | Preflight, then launch `scripts/start.sh` (8× GPU, long-running). Stamps live state into `artifacts/index.yaml`. |

Per the block plugin guidelines, **no `/sft:create`** — new blocks are only
created via `/root:create`.

Run them from inside `subblock/sft/`.

## Layout

```
sft-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                  # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md          # /sft:setup
    ├── check/SKILL.md          # /sft:check
    ├── dashboard/SKILL.md      # /sft:dashboard
    └── run/SKILL.md            # /sft:run
```
