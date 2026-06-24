# terminalgen — verified task examples

Committed, known-good **reference examples** so an agent operating the pipeline
can see exactly what a verified task looks like in both output formats — without
re-running scrape → generate → validate. These are real tasks produced by this
block's pipeline and validated reward=1.0 (see `dashboard/memory/scaled-run-20260623.md`).

This directory is the terminalgen analog of the sample tasks swegen ships in its
submodule — terminal-lego is read-only and ships no verified task dirs, so the
examples live here instead.

## Layout

```
artifacts/examples/
├── terminal_tasks/file-text-processing-tl/task_00004/   # terminal-lego v1.0 (in-place format)
│   └── verifiable_tasks.txt
└── merged_terminal_tasks/                               # harbor 1.1 (downstream format)
    ├── file-text-processing__task_00004/   # SAME task as above, after conversion
    ├── python-ecosystem__task_00003/       # CSV → multiline JSON
    ├── build-editor-tooling__task_00011/   # fix OpenMP linker error in a Makefile
    └── verifiable_tasks.txt
```

`file-text-processing/task_00004` is shown in **both** formats so you can diff the
`task.toml` and see the v1.0 → harbor 1.1 conversion (`version="1.0"` →
`schema_version="1.1"`, new `[task]` block, `memory "1G"` → `memory_mb 1024`, etc.;
all sub-files identical). See `docs/content/docs/outputs.mdx` for the full mapping.

## What each task contains

| File | Purpose |
|------|---------|
| `instruction.md` | problem statement (input to the solving agent) |
| `task.toml` | metadata (v1.0 in-place; harbor 1.1 in merged) |
| `environment/Dockerfile` + `environment/task_file/` | build env + seed files |
| `solution/solve.sh` | reference solution (applied during validation) |
| `tests/test.sh` + `tests/test_outputs.py` | verifier; writes reward to `/logs/verifier/reward.txt` |

## Re-validate an example (sanity check on a new host)

```bash
# stage as task_00000 (the validator only discovers task_*-prefixed dirs) and run it:
mkdir -p /tmp/ex/in && cp -r artifacts/examples/terminal_tasks/file-text-processing-tl/task_00004 /tmp/ex/in/task_00000
python repos/terminal-lego/validator/validate_tasks.py --input /tmp/ex/in --output /tmp/ex/out --workers 1 --timeout 900
# expect validation_report.json → passed: 1
```

> These are illustrative references, not the authoritative output. Real runs write
> to `artifacts/terminal_tasks/{domain}-tl/` (v1.0) and
> `artifacts/merged_terminal_tasks/` (harbor 1.1), gated by each `verifiable_tasks.txt`.

## Harbor compatibility

Both formats load into harbor's task config (`harbor.models.task.config.TaskConfig`)
— harbor renames `version`→`schema_version` and auto-converts `memory`/`storage`
strings. v1.0 loads with deprecation warnings and no `[task]` package info; harbor
1.1 is the clean registry-ready form. Quick check against harbor's real model:

```python
from harbor.models.task.config import TaskConfig   # from a harbor checkout (PYTHONPATH=src)
TaskConfig.model_validate_toml(open("<task>/task.toml").read())   # raises if invalid
```
