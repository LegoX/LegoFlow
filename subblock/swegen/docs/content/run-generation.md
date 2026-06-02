---
title: Run Generation
description: Prepare PR pools, run create scripts, and monitor generation
---

# Run Generation

Prepare PR pools, run create scripts, and monitor generation

SWE-gen generation is driven by language-specific shell scripts in
`subblock/swegen/scripts/`. Each script activates the environment, loads local
runtime variables, configures Docker cache paths, and calls `swegen create`.

## Collect PRs

The PR collector writes language-specific input files:

```
python repos/swegen/tools/collect_prs_wo_image.py \
  --languages python \
  --repo_num 100 \
  --max_prs_per_repo 50 \
  --output_dir ./artifacts/collected_prs
```

The output file is:

```
artifacts/collected_prs/python_pr_ids.txt
```

Repeat or schedule collection for all enabled languages.

## Run one language

Start with one language when validating a new node:

```
N_CONCURRENT=1 bash scripts/create_py.sh
```

The script writes tasks to:

```
artifacts/swe_tasks/py-cc
```

and logs to:

```
artifacts/logs/swegen-create/cc_py_March.txt
```

## Run all languages

After the smoke test is healthy:

```
bash scripts/create_all_bg.sh
```

The script launches the configured language create scripts in the background.
Use `tmux` or a process supervisor for long production runs.

## Tuned parameters

Each language has its own defaults:

- `N_CONCURRENT` controls parallel PR cases.
- `--timeout` controls the whole case timeout.
- `--cc-timeout` controls the task completion model timeout.
- `--min-source-files` and `--max-source-files` filter PR scope.
- `--docker-prune-batch` controls Docker cleanup cadence.

You can override `N_CONCURRENT` at launch:

```
N_CONCURRENT=8 bash scripts/create_rust.sh
```

## Docker and local caches

Generation builds and validates Docker images. The scripts route Docker config,
buildx state, and cloned repo cache away from shared filesystems when possible.
These caches are performance state, not dataset state. They do not need to be
committed or copied between nodes.

## Resume behavior

Re-running a create script resumes from:

- `artifacts/swe_tasks/<lang>-cc/verifiable_tasks.txt`
- `artifacts/swe_tasks/<lang>-cc/.swegen-create-batch/*.json`
- the optional `.swegen-*` task state directory

The resume logic reconciles successful batch entries against the verified task
manifest, so stale success flags do not count unless the task files are present
and the manifest lists the task ID.

## Operational loop

A typical production loop is:

1. Keep PR pools full.
2. Run language create scripts.
3. Monitor `verifiable_tasks.txt` growth and batch-state failures.
4. Tune concurrency and timeouts.
5. Export verified tasks or let downstream blocks read manifests directly.

Open the [Dashboard](/docs/dashboard/) for the live progress view.
