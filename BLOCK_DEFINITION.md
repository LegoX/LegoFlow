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
main: dashboard/overview.mdx

repos: []
inputs: {}
outputs: {}
artifacts: []
memory:
  path: memory/
scripts:
  path: scripts/
subblock: {}
meta_info: metainfo.yaml
status: status.yaml
```

### `name`
- Stable identifier of the current block
- Used for naming, references, and documentation

### `main`
- Main human-readable entry document
- In practice this is `dashboard/overview.mdx`
- This is the first document a user or agent should read for current context

### `repos`
- Associated code repositories for the current block
- Only create the `repos/` directory if the current block actually has repo attachments
- This may contain one repo or several related repos

### `inputs`
- Runtime input values required before the block can run: secrets, paths, experiment hyperparameters
- In practice represented by `inputs.yaml` at the block root
- Human-filled before each run; agent reads at execution time
- Answers: what values does this block need right now?

### `outputs`
- Runtime output values written by the agent after a run completes
- In practice represented by `outputs.yaml` at the block root
- Not edited manually; agent writes after execution
- Answers: what did this run produce?

### `artifacts`
- Per-experiment records: params, metrics, and logs for every run
- In practice represented by `artifacts/index.yaml` (run index) and `artifacts/files/run_NNN/` (per-run files)
- **Every run must be archived.** After each run completes, the agent must:
  1. Write `params.yaml`, `metrics.yaml`, and `run.log` into a new `artifacts/files/run_NNN/` directory
  2. Append a corresponding entry to `artifacts/index.yaml` with the run id, timestamps, status, and file paths
  3. Copy or reference the `inputs.yaml` values used for that run inside the run directory so the exact settings are preserved alongside the results
- Answers: what happened in each experiment, and what settings produced it?

### `memory`
- Long-form notes, decisions, reports, postmortems, and observations, which should be constantly updated by the long-running agent
- This is the deeper context behind the short summary in `dashboard/overview.mdx`

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

### `meta_info`
- Machine-readable block manifest: identity, role, resource allocation, and input/output dependency wiring with other blocks
- In practice represented by `metainfo.yaml`
- Answers: what is this block, what does it need, and what does it produce for others?

### `status`
- Live operational state of the block: current job progress, results so far, next steps, blockers, and metrics
- In practice represented by `status.yaml`
- Updated continuously by the running agent
- Associated log files live in `logs/` using the naming convention `logs/YYYY-MM-DD_<job_slug>.log`

## Recommended Directory Layout

```text
<block_dir>/
├── CLAUDE.md
├── metainfo.yaml
├── status.yaml
├── inputs.yaml
├── outputs.yaml
├── dashboard/
│   ├── overview.mdx
│   ├── status.mdx
│   ├── io.mdx
│   └── memory.mdx
├── repos/
├── artifacts/
│   ├── index.yaml
│   └── files/
│       └── run_NNN/
│           ├── params.yaml
│           ├── metrics.yaml
│           └── run.log
├── memory/
│   └── notes.md
├── scripts/
│   ├── start.sh
│   ├── dryrun.sh
│   └── clean.sh
└── subblock/
```

## Design Principles

### 1. One block, one readable entry
Every block should have one obvious place for people to start reading: `dashboard/overview.mdx`.

### 2. Logical result and stored evidence are different
- `outputs/` tells you the logical results
- `artifacts/` tells you where the raw files and evidence live

These should stay parallel.

### 3. Scripts are the operational interface
If a block can be run, sanity-checked, or cleaned, those actions should be exposed through `scripts/`.

### 4. Memory is not summary
- `dashboard/overview.mdx` is short and current
- `memory/` is long and deep

### 5. Recursion should stay simple
A child block should look structurally like its parent. That is why `subblock/` exists.

## Practical Rule

When in doubt:
- put block identity, resources, and dependency wiring in `metainfo.yaml`
- put live job progress and growing history in `status.yaml`
- put current human-readable state in `dashboard/overview.mdx`
- put runtime input values in `inputs.yaml`
- put runtime output values in `outputs.yaml`
- put per-experiment params, metrics, and logs in `artifacts/files/run_NNN/`
- put the run index in `artifacts/index.yaml`
- put long-form context in `memory/`
- put execution entrypoints in `scripts/`
- put child units in `subblock/`

That is the default block contract for this repo.

## File Roles

| File | Audience | Updated by | Content |
|---|---|---|---|
| `README.md` | External humans | Human only | Project motivation, one-paragraph orientation |
| `CLAUDE.md` | Agents (primary) | Agent or human | This block's contract: inputs, outputs, scripts, parent/child relationships |
| `BLOCK_DEFINITION.md` | Global reference | Human only | Canonical definition of what a block is — field semantics, directory layout |
| `dashboard/overview.mdx` | Humans checking state | Agent (primary) | Current state narrative: what is done, in progress, and next |
| `metainfo.yaml` | Agents + tooling | Agent or human | Machine-readable identity, resources, input/output dependency wiring |
| `status.yaml` | Agents + tooling | Agent (primary) | Live job progress, results, next steps, blockers, metrics |
| `inputs.yaml` | Agent (primary) | Human | Runtime input values filled before each run |
| `outputs.yaml` | Agent (primary) | Agent | Runtime output values written after each run completes |
