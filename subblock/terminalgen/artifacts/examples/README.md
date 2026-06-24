# terminalgen — verified task examples

Committed, known-good **reference examples** so an agent operating the pipeline
can see exactly what a verified task looks like — without re-running scrape →
generate → validate. These are real tasks produced by this block's pipeline and
validated reward=1.0 (see `dashboard/memory/scaled-run-20260623.md`).

This directory is the terminalgen analog of the sample tasks swegen ships in its
submodule — terminal-lego is read-only and ships no verified task dirs, so the
examples live here instead.

All tasks are in terminal-lego's native **v1.0** schema — the format the pipeline
produces and that downstream consumes (no conversion step).

## Layout

```
artifacts/examples/
├── terminal_tasks/                         # in-place per-domain layout (primary output)
│   ├── file-text-processing-tl/task_00004/   # extract JSON key names with jq
│   ├── python-ecosystem-tl/task_00003/       # CSV → multiline JSON
│   └── build-editor-tooling-tl/task_00011/   # fix OpenMP linker error in a Makefile
└── merged_terminal_tasks/                  # optional flat export (same tasks, domain-prefixed ids)
    ├── file-text-processing__task_00004/
    ├── python-ecosystem__task_00003/
    └── build-editor-tooling__task_00011/
```

`terminal_tasks/` and `merged_terminal_tasks/` hold the **same tasks in the same
v1.0 format** — the only difference is layout: per-domain dirs vs. a flat root
with `{domain}__{task_id}` ids (what `scripts/extract_verified_tasks.py` produces).

## What each task contains

| File | Purpose |
|------|---------|
| `instruction.md` | problem statement (input to the solving agent) |
| `task.toml` | terminal-lego v1.0 metadata |
| `environment/Dockerfile` + `environment/task_file/` | build env + seed files |
| `solution/solve.sh` | reference solution (applied during validation) |
| `tests/test.sh` + `tests/test_outputs.py` | verifier; writes reward to `/logs/verifier/reward.txt` |

## Re-validate an example (sanity check on a new host)

```bash
# the validator only discovers task_*-prefixed dirs; stage as task_00000 and run:
mkdir -p /tmp/ex/in && cp -r artifacts/examples/terminal_tasks/file-text-processing-tl/task_00004 /tmp/ex/in/task_00000
python repos/terminal-lego/validator/validate_tasks.py --input /tmp/ex/in --output /tmp/ex/out --workers 1 --timeout 900
# expect validation_report.json → passed: 1
```

## Harbor compatibility

These v1.0 tasks load directly into harbor's task config
(`harbor.models.task.config.TaskConfig`) — harbor renames `version`→`schema_version`
and auto-converts `memory`/`storage`. No v1.0→harbor-1.1 rewrite is needed to run
them. (The `[task]` package block harbor uses for its *registry* is optional and
not required to run/verify a task.)

> These are illustrative references, not the authoritative output. Real runs write
> to `artifacts/terminal_tasks/{domain}-tl/`, gated by each `verifiable_tasks.txt`.
