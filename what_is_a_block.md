# What Is A Block

## Definition

A `block` is the basic collaboration unit in a block-structured project.

It is the unit that:
- humans read and edit
- agents inspect and update
- scripts operate on
- subblocks inherit from

A block is not just a folder. It is a structured boundary that defines:
- what the current unit is responsible for
- what it depends on
- what it produces
- what evidence it stores
- what long-term memory it keeps
- how it is started and maintained

## Core Idea

The repo is organized as a tree of blocks.

- The root directory is the root block.
- Every directory under `subblock/` is also a block.
- Every block follows the same conceptual contract.

This gives the project one reusable collaboration model instead of many unrelated per-module conventions.

## Why Blocks Exist

Blocks exist to solve three problems at once:

1. Human readability  
Users and teammates need a stable place to read current state, outputs, evidence, and notes.

2. Agent operability  
Agents need predictable directories and file names so they can update state without guessing.

3. Recursive composition  
A large system should be decomposable into smaller units that behave the same way as the parent.

## Block Fields

Every block should be understandable through the following fields:

```yaml
name: block_name
main: docs/main.md

repos: []
inputs: {}
outputs: {}
artifacts: []
memory:
  path: memory/
scripts:
  path: scripts/
subblock: {}
```

### `name`
- Stable identifier of the current block
- Used for naming, references, and documentation

### `main`
- Main human-readable entry document
- In practice this is `docs/main.md`
- This is the first document a user or agent should read for current context

### `repos`
- Associated code repositories for the current block
- Only create the `repos/` directory if the current block actually has repo attachments
- This may contain one repo or several related repos

### `inputs`
- Structured upstream dependencies required before the block can run
- In practice represented by `inputs/index.yaml`
- Answers: what does this block need?

### `outputs`
- Structured logical results produced by the block
- In practice represented by `outputs/index.yaml`
- Answers: what did this block produce logically?

### `artifacts`
- Stored evidence, raw files, manifests, logs, and exportable output payloads
- In practice represented by `artifacts/index.yaml` plus `artifacts/files/`
- Answers: where is the concrete evidence?

### `memory`
- Long-form notes, decisions, reports, postmortems, and observations, which should be constantly updated by the long-running agent
- This is the deeper context behind the short summary in `docs/main.md`

### `scripts`
- Executable interface of the current block
- Typical entrypoints:
  - `scripts/start.sh`
  - `scripts/dryrun.sh`
  - `scripts/clean.sh`
- `scripts/dryrun.sh` is the default health-check sample script for a block

### `subblock`
- Nested child blocks
- Each child under `subblock/` is itself a full block
- This is the recursive expansion point of the system

## Recommended Directory Layout

```text
<block_dir>/
├── CLAUDE.md
├── docs/
│   └── main.md
├── repos/
├── inputs/
│   └── index.yaml
├── outputs/
│   └── index.yaml
├── artifacts/
│   ├── index.yaml
│   └── files/
├── memory/
│   ├── README.md
│   ├── notes.md
│   ├── decisions.md
│   └── reports/
├── scripts/
│   ├── start.sh
│   ├── dryrun.sh
│   └── clean.sh
└── subblock/
```

## Design Principles

### 1. One block, one readable entry
Every block should have one obvious place for people to start reading: `docs/main.md`.

### 2. Logical result and stored evidence are different
- `outputs/` tells you the logical results
- `artifacts/` tells you where the raw files and evidence live

These should stay parallel.

### 3. Scripts are the operational interface
If a block can be run, sanity-checked, or cleaned, those actions should be exposed through `scripts/`.

### 4. Memory is not summary
- `docs/main.md` is short and current
- `memory/` is long and deep

### 5. Recursion should stay simple
A child block should look structurally like its parent. That is why `subblock/` exists.

## Practical Rule

When in doubt:
- put current human-readable state in `docs/main.md`
- put dependency structure in `inputs/index.yaml`
- put logical results in `outputs/index.yaml`
- put raw evidence in `artifacts/`
- put long-form context in `memory/`
- put execution entrypoints in `scripts/`
- put child units in `subblock/`

That is the default block contract for this repo.
