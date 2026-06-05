# eval — block-local skills for the evaluation block

Slash commands tailored to `subblock/eval/`. They wrap Harbor (benchmark
execution against registered datasets in `repos/harbor/registry.json`)
and the per-job LiteLLM proxy used to route the agent's API calls to the
configured upstream model.

For the generic block-system command (only `/root:create` lives there),
see `.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/eval:setup`     | Bootstrap: clone `repos/harbor/` at the pinned commit, build the Harbor uv env + LiteLLM venv, wire `runtime_info.input` (`llm_api`, `litellm_proxy`, `task_source`, `harbor_job`, `agent`). |
| `/eval:check`     | Preflight: schema + Harbor repo pin + uv/venv envs + LLM endpoint `/models` + `(task_source.dataset_name, version)` resolves in `registry.json` + LiteLLM port availability. Read-only. |
| `/eval:dashboard` | Show per-job status: tasks resolved/unresolved/in-flight, accuracy aggregate, LiteLLM trajectory counts. |
| `/eval:run`       | Preflight, then `scripts/start.sh` — generate LiteLLM config, start the proxy, launch the Harbor job against the configured benchmark. |

Per the block plugin guidelines, **no `/eval:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/eval/`. Per `CLAUDE.md`, eval is
configured to execute on its declared remote IP — `:run` will SSH +
tmux per BLOCK_DEFINITION.md §2.3 unless `meta_info.resources.ip` is
set to `local`.

## Layout

```
eval-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                  # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md          # /eval:setup
    ├── check/SKILL.md          # /eval:check
    ├── dashboard/SKILL.md      # /eval:dashboard
    └── run/SKILL.md            # /eval:run
```
