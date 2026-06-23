---
name: setup
description: >
  Bootstrap the terminalgen block from a fresh clone: initialise the
  terminal-lego submodule under `repos/terminal-lego/`
  (`git submodule update --init`), create + activate the Python venv,
  `pip install -r repos/terminal-lego/requirements.txt`, and ensure the
  OpenAI-compatible LLM env (`OPENAI_API_KEY`, `OPENAI_API_BASE_URL`,
  `MODEL_NAME`), the StackExchange key (`SO_API_KEY`), and `DOCKER_HOST`
  are set in the user's shell — prompting for any that are missing.
  Idempotent. Triggers on phrases like "set up terminalgen", "bootstrap
  terminalgen", "install terminalgen", "prepare terminalgen before running".
---

# /terminalgen:setup

Prepare `subblock/terminalgen/` so `/terminalgen:check` can pass. The operation
is idempotent: repeat it freely, but never overwrite secrets or runtime outputs
without explicit user consent.

## Step 0 - Orient

Run from inside the terminalgen block. Verify:

1. `./config.yaml` exists and `meta_info.name == "terminalgen"`.
2. `./CLAUDE.md` exists.
3. `./repos/terminal-lego/requirements.txt` exists after submodule init.

If the current directory is not `subblock/terminalgen/`, stop and tell the user
to rerun the command from the block directory.

## Step 1 - Initialize the terminal-lego source checkout

From the repository root, ensure the submodule exists:

```bash
git submodule update --init subblock/terminalgen/repos/terminal-lego
```

Then return to `subblock/terminalgen/`. Compare the declared pin
`meta_info.repos.terminal-lego.commit_id` with
`git -C repos/terminal-lego rev-parse HEAD` and report any drift. Do not change
the submodule pin unless the user explicitly asks. **Never modify files under
`repos/terminal-lego/`** — it is a read-only upstream dependency.

## Step 2 - Build or refresh the Python environment

Read `meta_info.environment.venv_path` from `config.yaml` (currently
`artifacts/envs/terminalgen-env`) and create it if missing:

```bash
python3 -m venv artifacts/envs/terminalgen-env
source artifacts/envs/terminalgen-env/bin/activate
pip install -r repos/terminal-lego/requirements.txt
```

The pipeline's only runtime dependency is `requests`.

## Step 3 - Prepare runtime environment variables

terminal-lego's generator calls an OpenAI-compatible endpoint; the scraper
calls the StackExchange API. Keep these in the shell or in `.env`; never write
real secrets into `config.yaml`.

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | LLM key used by `repos/terminal-lego/generator/task_generator.py`. |
| `OPENAI_API_BASE_URL` | OpenAI-compatible base URL. **The generator reads this via `--api-base`** (NOT `OPENAI_API_BASE`). |
| `MODEL_NAME` | Model for task generation (instruction → environment → solution → tests → dockerfile). |
| `SO_API_KEY` | StackExchange API key (10000 req/day with key, 300 without). |
| `DOCKER_HOST` | Prefer `unix:///var/run/docker.sock` so the validator does not probe stale Podman sockets. |

If a required value is missing, ask once and show the exact `export` line.
`scripts/load_runtime_env.sh` hydrates these from the shell, then `.env`, then
`config.yaml.runtime_info.input.llm_api`.

## Step 4 - Create expected local directories

Create directories used by scripts and smoke tests:

```bash
mkdir -p artifacts/collected_questions artifacts/terminal_tasks \
  artifacts/merged_terminal_tasks artifacts/logs/terminalgen-create
```

If the user wants the quick verification path, confirm that the smoke fixture
exists at:

- `tests/smoke/fixtures/https-nginx-cert-setup/` (known-good harbor-1.1 task)
- `tests/smoke/fixtures/so_data_sample.json` (2 sample SO questions)

## Step 5 - Hand off to `/terminalgen:check`

Finish by running the check logic. Report setup as complete only if the
environment is ready for `scripts/dryrun.sh` and the user has enough
credentials to run either a smoke test or a real batch.

## Guardrails

- Do not commit `.env`, API keys, or generated task outputs.
- Do not edit `config.yaml` unless the user asks for a specific config change.
- **Never modify `repos/terminal-lego/`** — it is a pinned read-only dependency.
- Do not run the generator, validator, or Docker cleanup from setup; leave
  execution to `/terminalgen:check` or `/terminalgen:run`.
