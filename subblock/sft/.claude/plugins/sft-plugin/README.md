# sft — block-local skills for the supervised fine-tuning block

Slash commands tailored to `subblock/sft/`. They wrap LLaMA-Factory +
DeepSpeed ZeRO-3 training on config-selected models (currently Qwen3.5-35B)
with Harbor conversion or ready-made Hugging Face/local LF datasets.

For the generic block-system command (only `/root:create` lives there),
see `.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/sft:setup`     | Bootstrap the repos/environment and fill source-aware `runtime_info.input` fields (dataset source, model, training hyperparameters, WandB). |
| `/sft:check`     | Preflight: schema + GPU availability (8× expected) + deepspeed config + dataset registration + WandB credentials + base model path + dryrun. Read-only. |
| `/sft:dashboard` | Show training progress: current step, loss curve from the latest log, WandB run URL if configured. |
| `/sft:run`       | Preflight, then launch `scripts/start.sh` (8× GPU, long-running). Live progress comes from the output directory/dashboard; the terminal run is archived on exit. |

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
