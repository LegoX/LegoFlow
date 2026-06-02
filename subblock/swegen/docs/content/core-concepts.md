---
title: Core Concepts
description: Core concepts and terminology in SWE-gen
---

# Core Concepts

Core concepts and terminology in SWE-gen

SWE-gen has the following core concepts.

## PR pool

A PR pool is a language-specific input file containing GitHub pull requests in
the form:

```
owner/repo:pr-123
```

The block stores PR pools under `artifacts/collected_prs/`. The main language
scripts pass these files to `swegen create` through `--input-ids-file`.

## Task skeleton

A task skeleton is the Harbor task directory that SWE-gen builds from a PR.
It contains the problem instruction, Docker environment, bug patch, solution
patch, and verification tests.

The stable structure is:

```
<task_id>/
├── task.toml
├── instruction.md
├── environment/
│   ├── Dockerfile
│   └── bug.patch
├── solution/
│   ├── fix.patch
│   └── solve.sh
└── tests/
    └── test.sh
```

The task ID is derived from the repository and PR number, for example
`owner__repo-123`.

## NOP and Oracle validation

SWE-gen does not expose every generated skeleton downstream. It validates a
candidate task with two checks:

- NOP - the unmodified buggy environment should fail the task test.
- Oracle - applying the ground-truth solution should pass the task test.

Only tasks that pass validation are considered verified.

## Verified task manifest

Each language output directory has a manifest:

```
artifacts/swe_tasks/<lang>-cc/verifiable_tasks.txt
```

This file is the authoritative downstream contract. A task may exist on disk
because it is in progress, failed, or partially generated, but it is only safe
for trajgen or other consumers when its task ID appears in
`verifiable_tasks.txt`.

## Batch state

Long runs resume through per-output batch state:

```
artifacts/swe_tasks/<lang>-cc/.swegen-create-batch/<hash>.json
```

The hash is based on the resolved absolute path of the input PR file. That
means moving a restored run to a different clone path requires relocating or
regenerating the batch state filename before continuing.

Batch state records each PR case, attempts, status, errors, model fingerprint,
elapsed time, and selected task ID.

## Language outputs

SWE-gen uses one output directory per language:

| Language | Output |
| --- | --- |
| Python | `artifacts/swe_tasks/py-cc` |
| JavaScript | `artifacts/swe_tasks/js-cc` |
| TypeScript | `artifacts/swe_tasks/ts-cc` |
| Go | `artifacts/swe_tasks/go-cc` |
| C | `artifacts/swe_tasks/c-cc` |
| C++ | `artifacts/swe_tasks/cpp-cc` |
| Java | `artifacts/swe_tasks/java-cc` |
| Rust | `artifacts/swe_tasks/rust-cc` |

## Difficulty scoring

SWE-gen can add static difficulty metadata to `task.toml`. The score is derived
from the patch, tests, instruction, and file scope. The resulting fields are
used for dataset analysis and sampling:

- `difficulty_score`
- `difficulty_label`
- `difficulty`
- `category`
- `tags`

## Adaptive tuning

The block can be operated as an adaptive agent. It monitors per-language
success rates and PR pool depth, then adjusts generation parameters within
bounds:

- `timeout`
- `cc_timeout`
- `n_concurrent`

The active values and status live in `config.yaml` under
`runtime_info.input.languages`.
