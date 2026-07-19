# evaluator — block-local skills for the evaluator block

Slash commands tailored to `subblock/evaluator/`. They wrap Harbor (benchmark
execution against registered datasets in `repos/harbor/registry.json`)
and the per-job LiteLLM proxy used to route the agent's API calls to the
configured upstream model.

For the generic block-system command (only `/root:create` lives there),
see `.claude/plugins/root-plugin/`.

## Commands

| Command | What it does |
| --- | --- |
| `/evaluator:setup`     | Bootstrap: clone `repos/harbor/` at the pinned commit, build the Harbor uv env + LiteLLM venv, wire `runtime_info.input` (`llm_api`, `litellm_proxy`, `task_source`, `harbor_job`, `agent`). |
| `/evaluator:check`     | Preflight: schema + Harbor repo pin + uv/venv envs + LLM endpoint `/models` + `(task_source.dataset_name, version)` resolves in `registry.json` + LiteLLM port availability. Read-only. |
| `/evaluator:dashboard` | Inspect results: quick textual per-job summary (resolved/unresolved/in-flight, accuracy, trajectory counts), drive the analysis pipeline (`analyze_job.sh` → `<job>/analysis/`, `prepare_dataset.sh` for gold), and serve the web dashboard under `dashboard/` (`bash dashboard/start.sh`, :8092). |
| `/evaluator:run`       | Preflight, then `scripts/start.sh` — generate LiteLLM config, start the proxy, launch the Harbor job against the configured benchmark. |

Per the block plugin guidelines, **no `/evaluator:create`** — new blocks are
only created via `/root:create`.

Run them from inside `subblock/evaluator/`. Per `CLAUDE.md`, evaluator is
configured to execute on its declared remote IP — `:run` will SSH +
tmux per BLOCK_DEFINITION.md §2.3 unless `meta_info.resources.ip` is
set to `local`.

## Layout

```
evaluator-plugin/
├── .claude-plugin/plugin.json
├── README.md
├── resources/                  # block-specific reference docs (optional)
└── skills/
    ├── setup/SKILL.md          # /evaluator:setup
    ├── check/SKILL.md          # /evaluator:check
    ├── dashboard/SKILL.md      # /evaluator:dashboard
    └── run/SKILL.md            # /evaluator:run
```
