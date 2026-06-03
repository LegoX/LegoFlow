---
name: setup
description: >
  Prepare the trajgen block before a Harbor run: clone or update the managed
  read-only repos (harbor, swe_data_process) to their pinned commits, build the
  uv environments, and copy verified SWE tasks into artifacts/tasks/<dataset>/
  filtered by swegen's verifiable_tasks.txt. Idempotent — reuses valid existing
  checkouts, envs, and task dirs. Run this on a fresh clone, after bumping a repo
  commit in config.yaml, or when /block:check / scripts/dryrun.sh reports a
  missing repo, env, or task directory. Triggers on "set up trajgen", "prepare
  trajgen tasks", "update harbor", "install the trajgen environments",
  "/trajgen:setup".
---

# /trajgen:setup

Bring the trajgen block to a runnable state. All commands run from the block
root `subblock/trajgen/`. This skill wraps three scripts; it does not launch a
job (see `/trajgen:run-job`) and writes nothing into `repos/` sources.

## Step 0 — Orient

Read `config.yaml`:
- `meta_info.repositories.{harbor,swe_data_process}` — url, branch, pinned `commit`, `path`, `readonly`.
- `meta_info.environment` — `harbor_uv`, `litellm_uv`, `swe_data_process_uv`, `swe_data_process_extras`.
- `runtime_info.input.task_source` — `provider`, `dataset_name` (for `provider: local` this points at swegen's `swe_tasks/<lang>-cc`).

Both repos are **managed local-only dependencies** and are gitignored. Never edit
their sources here; they are set read-only after checkout when `readonly: true`,
so any uv environment for them must live outside the checkout (under `artifacts/env/`).

## Step 1 — Update repos

```bash
scripts/update_repos.sh                       # all repos under meta_info.repositories
scripts/update_repos.sh --repo harbor         # just one
scripts/update_repos.sh --repo swe_data_process --ref <branch-or-sha>   # override the configured ref for one repo
```

The script clones if missing, otherwise fetches and checks out the pinned
`commit`. It **refuses to update a repo whose worktree has local modifications** —
resolve those first rather than forcing. After checkout it re-applies the
read-only bit when `readonly: true`.

## Step 2 — Build the swe_data_process uv env

```bash
scripts/setup_swe_data_process_env.sh
```

Creates/refreshes the uv project environment at
`artifacts/env/swe-data-process-uv` (outside the read-only repo). It reads
`meta_info.environment.swe_data_process_extras` and runs
`UV_PROJECT_ENVIRONMENT=… uv sync --extra <each>` from inside the repo. The
Harbor uv env (`harbor_uv`) and the LiteLLM venv (`litellm_uv`) are provisioned
by their own tooling / `uv run`; `scripts/dryrun.sh` validates all three.

## Step 3 — Prepare tasks

```bash
scripts/prepare_tasks.sh                       # uses config.yaml task_source
scripts/prepare_tasks.sh --config config.some-variant.yaml
scripts/prepare_tasks.sh --overwrite           # rebuild existing (invalid) task dirs
```

Copies task directories into `artifacts/tasks/<dataset>/`. When
`<source>/verifiable_tasks.txt` exists it copies **only** the listed task IDs
(manifest-filtered copy). If the manifest is missing it falls back to copying
every task dir — keep a manifest in the source. Idempotent: it skips when the
target already holds valid Harbor task dirs; use `--overwrite` to replace invalid ones.

## Step 4 — Validate

```bash
scripts/dryrun.sh
```

Confirms config, both repos' pinned state, all three environments
(`harbor_uv`, `swe_data_process_uv`, LiteLLM), task directories, and the
`sft_conversion` block. Resolve every failure before `/trajgen:run-job`.

## Notes

- Trajgen scripts need PyYAML in the runtime Python (inline `python3 -` config
  readers). On `ERROR: PyYAML is required`, `pip install pyyaml` into the active interpreter.
- Run inside a named tmux session on the host named by `meta_info.resources.ip`
  (currently `local` → this host) so long clones/syncs survive disconnects.
