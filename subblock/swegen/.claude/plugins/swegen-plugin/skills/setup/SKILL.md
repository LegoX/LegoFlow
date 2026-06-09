---
name: setup
description: >
  Bootstrap the swegen block from a fresh clone: initialise the swegen
  submodule under `repos/swegen/` (`git submodule update --init`),
  create + activate the Python venv, `pip install -e repos/swegen/`, and
  ensure the cross-provider LLM env (`OPENAI_API_KEY`,
  `OPENAI_API_BASE_URL`, `OPENAI_MODEL`, mirrored to `ANTHROPIC_API_KEY` /
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`), `GITHUB_TOKENS`,
  `DOCKER_HOST`, and `CLAUDE_CONFIG_DIR` are set in the user's shell —
  prompting for any that are missing. Stage `gh_token.txt` (one token per
  line) if the user prefers the file channel. Idempotent. Triggers on
  phrases like "set up swegen", "bootstrap swegen", "install swegen",
  "prepare swegen before running".
---

# /swegen:setup

Prepare `subblock/swegen/` so `/swegen:check` can pass. The operation is
idempotent: repeat it freely, but never overwrite secrets or runtime outputs
without explicit user consent.

## Step 0 - Orient

Run from inside the swegen block. Verify:

1. `./config.yaml` exists and `meta_info.name == "swegen"`.
2. `./CLAUDE.md` exists.
3. `./repos/swegen/pyproject.toml` exists after submodule init.

If the current directory is not `subblock/swegen/`, stop and tell the user
to rerun the command from the block directory.

## Step 1 - Initialize the SWEgen source checkout

From the repository root, ensure the submodule exists:

```bash
git submodule update --init subblock/swegen/repos/swegen
```

Then return to `subblock/swegen/`. If `config.yaml` declares a non-null
`meta_info.repos.swegen.commit_id`, compare it with
`git -C repos/swegen rev-parse HEAD` and report any drift. Do not change the
submodule pin unless the user explicitly asks.

## Step 2 - Build or refresh the Python environment

Read `meta_info.environment.venv_path` from `config.yaml` (currently
`artifacts/envs/swegen-env2`) and create it if missing:

```bash
python3 -m venv artifacts/envs/swegen-env2
source artifacts/envs/swegen-env2/bin/activate
pip install -e repos/swegen/
```

The editable install exposes `swegen` and `harbor` console scripts from
`repos/swegen/pyproject.toml`.

## Step 3 - Prepare runtime environment variables

SWEgen uses both direct OpenAI-compatible calls and Claude Code SDK based
task generation. Keep these in the shell or in `.env`; never write real
secrets into `config.yaml`.

| Variable | Purpose |
| --- | --- |
| `GITHUB_TOKENS` | Comma-separated GitHub API tokens for `swegen create` and general API use. |
| `GITHUB_TOKEN` | Optional first-token alias; `scripts/load_runtime_env.sh` derives it from `GITHUB_TOKENS` when absent. |
| `OPENAI_API_KEY` | LLM key used by `swegen.llm_env.get_openai_compatible_config()`. |
| `OPENAI_API_BASE_URL` | OpenAI-compatible base URL. |
| `OPENAI_MODEL` | Model for PR evaluation, instruction generation, and analysis calls. |
| `ANTHROPIC_API_KEY` | Usually the same value as `OPENAI_API_KEY` for the cross-provider shim. |
| `ANTHROPIC_BASE_URL` | Endpoint used by Claude Code SDK task completion. |
| `ANTHROPIC_MODEL` | Model used by Claude Code SDK task completion. |
| `DOCKER_HOST` | Prefer `unix:///var/run/docker.sock` so Harbor does not probe stale Podman sockets. |
| `CLAUDE_CONFIG_DIR` | Per-run Claude config dir, for example `$PWD/artifacts/claude-config/swegen-clean`. |

If a required value is missing, ask once and show the exact `export` line.
For GitHub collection, `repos/swegen/tools/collect_prs_wo_image.py` reads
tokens from `repos/swegen/gh_token.txt` by default; set
`COLLECT_GITHUB_TOKEN_FILE` when using a different token file.

## Step 4 - Create expected local directories

Create directories used by scripts and smoke tests:

```bash
mkdir -p artifacts/collected_prs artifacts/swe_tasks artifacts/logs/swegen-create artifacts/claude-config
```

If the user wants the quick verification path, confirm that the submodule
sample data exists at:

- `repos/swegen/artifacts/collected_prs/python_pr_ids.txt`
- `repos/swegen/artifacts/swe_tasks/py-cc/verifiable_tasks.txt`
- `repos/swegen/artifacts/swe_tasks/py-cc/tox-dev__tox-3813/`

## Step 5 - Hand off to `/swegen:check`

Finish by running the check logic. Report setup as complete only if the
environment is ready for `scripts/dryrun.sh` and the user has enough
credentials to run either a smoke test or a real batch.

## Guardrails

- Do not commit `.env`, `gh_token.txt`, API keys, or generated task outputs.
- Do not edit `config.yaml` unless the user asks for a specific config
  change.
- Do not run `swegen create`, `swegen validate`, or Docker cleanup from
  setup; leave execution to `/swegen:check` or `/swegen:run`.
