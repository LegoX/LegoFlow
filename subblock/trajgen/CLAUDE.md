# trajgen

This file declares that the current directory is a `block`.

For the canonical definition of a block, the field semantics, and the default
directory contract, see `BLOCK_DEFINITION.md` in the root block.

## Block Summary

```md
Name: trajgen
Type: data
Main doc: `dashboard/overview.mdx`
Meta info: `metainfo.yaml`
Status: `status.yaml`
Definition reference: `BLOCK_DEFINITION.md`
```

## Functional Positioning

This block is responsible for generating raw agent trajectories with Harbor.

It exists to bridge verified SWE task sources and downstream SFT data
conversion. The block owns the Live-side shell, configs, status, and handoff
metadata; Harbor itself remains a managed local dependency under `repos/`.

Its boundary is:
- in scope: Harbor repo acquisition, Harbor rollout configuration, trajectory
  job output tracking, SFT handoff metadata
- out of scope: editing Harbor source code inside Live, SFT conversion/training,
  RL training

## Inputs

This block depends on the following input categories:
- `repositories`: Git URL/ref/path/read-only policy for the local Harbor dependency
- `environment`: Harbor uv environment path and LiteLLM runtime version expectations
- `model_api`: Raw upstream API config used to generate the per-job LiteLLM config
- `litellm_proxy`: LiteLLM config template, port, and master key
- `task_source`: SWE task source to feed to Harbor
- `harbor_job`: jobs directory, concurrency, retry, timeout, and smoke-test knobs
- `agent`: Harbor agent name, version, runtime image, and sampling controls

Input readiness rule:
- this block can start when `config.yaml` is filled, `repos/harbor` exists, and
  `scripts/dryrun.sh` passes. `scripts/start.sh` builds the default Harbor
  command from structured config.
- this block is blocked when Harbor cannot be fetched, the local Harbor worktree
  has uncommitted changes, the local checkout does not match the pinned
  branch/commit in `config.yaml`, or the environment checks fail

## Outputs

This block produces:
- Harbor job outputs under `config.yaml` -> `harbor_job.jobs_dir`
- LiteLLM trajectory logs at `{job_dir}/<task-id>/agent/litellm-trajectory.jsonl`
- SFT handoff paths derived from `harbor_job.jobs_dir`

## Artifacts And Memory

Artifacts stored by this block include:
- `artifacts/env/`: local uv-managed environments for Harbor and LiteLLM
- `artifacts/tasks/`: downloaded or prepared Harbor task directories; default path is derived from `task_source.dataset_name`
- `artifacts/jobs/`: Harbor job outputs; configure Harbor to write jobs here
- `artifacts/litellm/`: generated per-job LiteLLM proxy configs
- `artifacts/logs/`: stdout/stderr logs from local trajgen scripts

Long-form memory maintained by this block includes:
- `memory/notes.md`: repo policy, handoff notes, operational gotchas

## Parent And Child Relationships

Parent relationship:
- parent block: `swe_lego_live`
- this block receives verified SWE tasks from `swegen` or another prepared task source
- this block reports raw trajectories for `sft_training`

Child relationship:
- child blocks under `subblock/`: none

## Repository Policy

Harbor is a managed local dependency:
- path: `repos/harbor`
- source: `https://github.com/SWE-Lego/harbor.git`
- tracked by Live through `config.yaml`
- ignored by git through `.gitignore` and the root `*repos/` rule

Do not edit Harbor source inside this Live block. Use `scripts/update_repos.sh`
to clone/fetch/checkout the configured ref. The script refuses to update if the
Harbor worktree has local modifications and restores read-only permissions when
`repositories.harbor.readonly` is true.

## Environment

This block uses two local uv-managed environments under `artifacts/env/`:

- `environment.harbor_uv`: runs the pinned Harbor checkout
- `environment.litellm_uv`: runs the per-job LiteLLM proxy

Keep both environments outside `repos/harbor` so the managed Harbor checkout can
remain read-only.

### Install Harbor

After updating `repos/harbor`, install or refresh the Harbor environment from
the Harbor root:

```bash
cd subblock/trajgen/repos/harbor
UV_PROJECT_ENVIRONMENT=../../artifacts/env/harbor-uv uv sync --all-extras
```

### Install LiteLLM

Create or refresh the LiteLLM proxy environment from the trajgen block root:

```bash
cd subblock/trajgen
uv venv artifacts/env/litellm-uv --python 3.13
uv pip install --python artifacts/env/litellm-uv/bin/python 'litellm[proxy]==1.83.9'
```

### Validate

`scripts/dryrun.sh` validates installed environments only; it should not install
packages or sync dependencies. It checks:

- Harbor env: `artifacts/env/harbor-uv/bin/python`, `bin/harbor`, and editable
  import from `repos/harbor`
- LiteLLM env: `artifacts/env/litellm-uv/bin/python`, `bin/litellm`, Python
  version, and LiteLLM package version
- Raw upstream model API metadata from `config.yaml`
- Prepared Harbor task directories under `artifacts/tasks`

The job is responsible for starting LiteLLM from that upstream config and
checking the proxy after it starts.

`scripts/start.sh` generates a local LiteLLM config from `model_api` and
`litellm_proxy`, writes it under `artifacts/litellm/<job>/`, passes it to
Harbor's `scripts/serve_llm/serve_litellm.sh` through `LITELLM_CONFIG`, starts
the proxy with the configured LiteLLM uv env, then launches the Harbor job. Use
`bash scripts/start.sh --dry-run-command` to print the generated Harbor command
and LiteLLM config path without launching a job.

### Prepare Tasks

Prepare task directories from the source declared in `config.yaml` before
launching jobs:

```bash
cd subblock/trajgen
bash scripts/prepare_tasks.sh
```

`scripts/prepare_tasks.sh` reads `task_source.provider`,
`task_source.dataset_name`, and `task_source.split`, derives the local output
directory under `artifacts/tasks/<dataset>`, and validates that the result looks
like Harbor task directories (`task.toml`, `instruction.md`, `environment/`,
and `tests/`). For private Hugging Face datasets, authenticate the Harbor uv
environment before running it.

### When Harbor Changes

If `config.yaml` changes the pinned Harbor branch or commit, the required
dependencies may also change. Version checks in `scripts/dryrun.sh` only catch
known expectations; they cannot prove that an existing env matches a new Harbor
lockfile or dependency graph.

After a substantial Harbor update, refresh the Harbor env from `repos/harbor`:

```bash
cd subblock/trajgen/repos/harbor
UV_PROJECT_ENVIRONMENT=../../artifacts/env/harbor-uv uv sync --all-extras
```

If LiteLLM proxy startup or import checks fail after an update, recreate or
refresh `artifacts/env/litellm-uv` as well.

`config.yaml` intentionally keeps only experiment knobs and reproducibility
pins. Stable defaults are derived by the scripts, including the Harbor CLI
checks, `harbor/litellm/datasets` package checks, the custom Claude Code import
path, Anthropic proxy protocol, runtime mount paths, and the LiteLLM trajectory
header.

This block intentionally uses `config.yaml` instead of `inputs.yaml` because it
is an execution wrapper around Harbor: repo pins, environments, model API,
LiteLLM proxy settings, agent runtime, and rollout knobs form one run profile.
It also does not maintain `outputs.yaml`; actual outputs live under
`artifacts/jobs`, and each job should carry its own `config.yaml` snapshot.

## Execution Interface

Available scripts:
- `scripts/update_repos.sh`: clone or update `repos/harbor` on demand
- `scripts/prepare_tasks.sh`: prepare or validate Harbor task directories under `artifacts/tasks`
- `scripts/dryrun.sh`: validate block files, YAML, Harbor repo state, environments, model API config, and Harbor run config
- `scripts/start.sh`: generate LiteLLM config, start the proxy, then build and run the Harbor command from `config.yaml`
- `scripts/clean.sh`: remove gitignored runtime outputs; `--repos` also removes local repos

Dryrun expectation:
- `scripts/dryrun.sh` checks that `repos/harbor` matches the URL and pinned
  branch/commit in `config.yaml`, that the worktree is clean and read-only, and
  that the configured runtime environment and task directories are ready.
- `scripts/start.sh` always runs `scripts/dryrun.sh` as a preflight before
  launching Harbor. Set `TRAJGEN_PREPARE_TASKS=1` to run
  `scripts/prepare_tasks.sh` before dryrun. A `command_override` field can be
  added for special experiments, but the normal path uses the generated command.

## Collaboration Rules

When updating this block:
- read `dashboard/overview.mdx` first for current state
- read `metainfo.yaml` for identity, resources, and dependency wiring
- use `config.yaml` for all Harbor repo and rollout configuration
- use `artifacts/` for raw evidence, logs, and copied rollout outputs
- use `memory/` for long-form context and decisions
- never commit `repos/harbor` or generated rollout data to the Live repository
