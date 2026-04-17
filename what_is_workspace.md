# WorkSpace Program

## Purpose
This file is the single program contract for every `WorkSpace` node in `SWE-Lego-Live`.

Each team member should use this file to define the rule document for a concrete `WorkSpace`. A `WorkSpace` is not only a state container. It is also a programmable module with:
- a shared object shape
- optional attached code repository context
- a user-specified rule document

This file is that rule document template.

## Core Rule
Every node in the system should be describable as a `WorkSpace` module.

The project has:
- one outer `WorkSpace` for the whole project
- one child `WorkSpace` per submodule

All nodes use the same top-level shape.

## Unified WorkSpace Shape

```yaml
name: workspace_name
summary: >
  One short human-readable summary of the current state,
  the main result, and the suggested next action.

repo: null
program: program.md

inputs: {}
config: {}
status: {}
outputs: {}
artifacts: []
memory: {}
subspace: {}
```

## Field Semantics

### `name`
- Stable `WorkSpace` name
- Should match the architecture naming used across the project
- Example: `project`, `swe_gen`, `data_composer`, `training_engine`

### `summary`
- Human-facing content only
- This is the main quick view for users and agents
- It should answer:
  - what happened
  - what matters
  - what should happen next

### `repo`
- Optional attached code repository for this `WorkSpace`
- Use this when a node is directly associated with a codebase
- This can be:
  - a local path
  - a repo identifier
  - a structured repo object
- If the node has no direct codebase attachment, leave it empty

### `program`
- The rule document for this `WorkSpace`
- This defines how the node should be understood, updated, and extended
- In practice this is a `program.md` file owned by the current `WorkSpace`
- Different `WorkSpace` nodes may have different `program.md` files derived from the same template

### `inputs`
- Dependency inputs required before this `WorkSpace` can run
- Focus on upstream dependencies and prerequisite artifacts
- In the filesystem, `inputs` is usually represented as a directory with `inputs/index.yaml` as the entry point
- Examples:
  - seed issue list
  - dataset version
  - prior checkpoint
  - evaluation baseline

### `config`
- Editable runtime settings and node-local policy
- This is where the `WorkSpace` declares how it should run

### `status`
- Current process state
- Source of truth for whether the node is idle, running, blocked, failed, or completed

### `outputs`
- Structured results produced by this `WorkSpace`
- This should contain direct logical results, not the explanation of those results
- In the filesystem, `outputs` is usually represented as a directory with `outputs/index.yaml` as the entry point

### `artifacts`
- Archived file or object references
- Use this to record the stored evidence for outputs
- `artifacts` should stay parallel to `outputs`, not nested under it
- `outputs` answers what the result is; `artifacts` answers where the evidence or raw files are stored
- Each artifact entry should ideally include:
  - `type`
  - `path` or `uri`
  - `producer`
  - `run_id`
  - `description`

### `memory`
- Long-form memory for this `WorkSpace`
- This is the deeper description behind `summary`
- Use this for detailed notes, decisions, reports, postmortems, design records, and accumulated context
- `summary` is the short surface; `memory` is the expanded internal explanation
- In the actual filesystem, `memory` may be implemented as a directory containing multiple markdown files

### `scripts/`
- `scripts/` is the executable interface of the current `WorkSpace`
- Use this directory for startup, status checking, archival, and cleanup scripts
- Typical entrypoints are:
  - `scripts/start.sh`
  - `scripts/status.sh`
  - `scripts/archive.sh`
  - `scripts/clean.sh`

## Standard Index Templates

### `inputs/index.yaml`
Use `inputs/index.yaml` as the single entry point for dependency inputs. A recommended shape is:

```yaml
version: 1

primary_inputs: []

inputs: {}

notes: []
```

Recommended meaning:
- `version`: schema version for the index file
- `primary_inputs`: the most important input keys for this node
- `inputs`: structured map of named inputs
- `notes`: optional human notes about missing, optional, or delayed inputs

Example:

```yaml
version: 1

primary_inputs:
  - generated_tasks
  - external_datasets

inputs:
  generated_tasks:
    type: workspace_output
    source: swe_gen.outputs.generated_tasks
    required: true
    description: generated SWE tasks from SWE-gen

  external_datasets:
    type: local_file
    path: inputs/external_datasets.yaml
    required: false
    description: optional external datasets for composition

notes:
  - generated_tasks is the required input for the next run
```

### `outputs/index.yaml`
Use `outputs/index.yaml` as the single entry point for logical outputs. A recommended shape is:

```yaml
version: 1

primary_outputs: []

outputs: {}

notes: []
```

Recommended meaning:
- `version`: schema version for the index file
- `primary_outputs`: the most important output keys for downstream readers
- `outputs`: structured map of logical outputs
- `notes`: optional human notes about output quality, limitations, or follow-up actions

Example:

```yaml
version: 1

primary_outputs:
  - dataset_version
  - run_summary

outputs:
  dataset_version:
    type: logical_result
    path: outputs/dataset_version.yaml
    description: current curated dataset version

  run_summary:
    type: logical_result
    path: outputs/run_summary.yaml
    description: summary of the latest successful run

notes:
  - raw evidence for these outputs should be stored in artifacts/
```

### `artifacts/index.yaml`
Use `artifacts/index.yaml` as the single entry point for archived evidence and stored files. A recommended shape is:

```yaml
version: 1

primary_artifacts: []

artifacts: {}

notes: []
```

Recommended meaning:
- `version`: schema version for the index file
- `primary_artifacts`: the most important artifact keys for debugging, audit, or export
- `artifacts`: structured map of archived files, directories, or object references
- `notes`: optional human notes about retention, provenance, or cleanup

Example:

```yaml
version: 1

primary_artifacts:
  - dataset_manifest
  - raw_log

artifacts:
  dataset_manifest:
    type: file
    path: artifacts/files/manifest.json
    producer: data_composer
    description: dataset manifest for the latest successful run

  raw_log:
    type: file
    path: artifacts/files/run.log
    producer: data_composer
    description: raw execution log for the latest run

notes:
  - keep logs for debugging even if they are not part of logical outputs
```

### `scripts/`
Use `scripts/` as the executable interface of the current node. A recommended minimal contract is:

```text
scripts/
├── start.sh
├── status.sh
├── archive.sh
└── clean.sh
```

Recommended meaning:
- `start.sh`: start or trigger the current node
- `status.sh`: report or refresh current status
- `archive.sh`: archive outputs and evidence into `artifacts/`
- `clean.sh`: clean temporary or transient state without removing canonical records

Recommended minimal script header:

```bash
#!/usr/bin/env bash
set -euo pipefail
```

### `subspace`
- Nested child `WorkSpace` nodes
- Use this when a module exposes internal submodules with the same shape
- `subspace` is the recursive expansion point of the current `WorkSpace`

## Default WorkSpace Directory
Each `WorkSpace` should have a default directory layout so humans and agents always know where to read and write information.

Recommended default layout:

```text
<workspace_dir>/
├── associated_repo/
├── program.md
├── summary.md
├── config.yaml
├── status.yaml
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
│   ├── status.sh
│   ├── archive.sh
│   └── clean.sh
└── subspace/
```

### Default Directory Meaning
- `associated_repo/`: optional associated repo directory for the current `WorkSpace`; create it only if the repo actually exists
- `program.md`: the local rule document for this `WorkSpace`
- `summary.md`: the short human-facing summary
- `inputs/`: dependency inputs for the current node; `inputs/index.yaml` is the required entry point
- `config.yaml`: editable configuration owned by the node
- `status.yaml`: current runtime state
- `outputs/`: structured logical outputs; `outputs/index.yaml` is the required entry point
- `artifacts/`: archived evidence and artifact indexes, kept parallel to `outputs/`
- `memory/`: deeper long-form context behind the current node
- `scripts/`: execution hooks for launching, checking, archiving, and cleaning the current node
- `subspace/`: nested child `WorkSpace` directories

## Default Root WorkSpace Layout For SWE
For the current SWE setup, the root `WorkSpace` can be organized like this:

```text
SWE-Lego-Live/
├── associated_repo/
├── program.md
├── summary.md
├── config.yaml
├── status.yaml
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
│   ├── status.sh
│   ├── archive.sh
│   └── clean.sh
└── subspace/
    ├── SWE-gen/
    │   ├── associated_repo/
    │   ├── program.md
    │   ├── summary.md
    │   ├── config.yaml
    │   ├── status.yaml
    │   ├── inputs/
    │   │   └── index.yaml
    │   ├── outputs/
    │   │   └── index.yaml
    │   ├── artifacts/
    │   │   ├── index.yaml
    │   │   └── files/
    │   ├── memory/
    │   │   ├── README.md
    │   │   ├── notes.md
    │   │   ├── decisions.md
    │   │   └── reports/
    │   ├── scripts/
    │   │   ├── start.sh
    │   │   ├── status.sh
    │   │   ├── archive.sh
    │   │   └── clean.sh
    │   └── subspace/
    ├── data_composer/
    │   ├── associated_repo/
    │   ├── program.md
    │   ├── summary.md
    │   ├── config.yaml
    │   ├── status.yaml
    │   ├── inputs/
    │   │   └── index.yaml
    │   ├── outputs/
    │   │   └── index.yaml
    │   ├── artifacts/
    │   │   ├── index.yaml
    │   │   └── files/
    │   ├── memory/
    │   │   ├── README.md
    │   │   ├── notes.md
    │   │   ├── decisions.md
    │   │   └── reports/
    │   ├── scripts/
    │   │   ├── start.sh
    │   │   ├── status.sh
    │   │   ├── archive.sh
    │   │   └── clean.sh
    │   └── subspace/
    ├── training/
    │   ├── associated_repo/
    │   ├── program.md
    │   ├── summary.md
    │   ├── config.yaml
    │   ├── status.yaml
    │   ├── inputs/
    │   │   └── index.yaml
    │   ├── outputs/
    │   │   └── index.yaml
    │   ├── artifacts/
    │   │   ├── index.yaml
    │   │   └── files/
    │   ├── memory/
    │   │   ├── README.md
    │   │   ├── notes.md
    │   │   ├── decisions.md
    │   │   └── reports/
    │   ├── scripts/
    │   │   ├── start.sh
    │   │   ├── status.sh
    │   │   ├── archive.sh
    │   │   └── clean.sh
    │   └── subspace/
    └── harbor/
        ├── associated_repo/
        ├── program.md
        ├── summary.md
        ├── config.yaml
        ├── status.yaml
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
        │   ├── status.sh
        │   ├── archive.sh
        │   └── clean.sh
        └── subspace/
```

The key rule is recursive consistency:
- every node under `subspace/` is also a full `WorkSpace`
- every node can have its own `subspace/`
- every child follows the same directory contract

## What Each WorkSpace Owner Must Define
When a team member defines a `WorkSpace`, they should provide:

1. `name`
2. `summary`
3. Optional `repo`
4. `program`
5. Exact `inputs`
6. Editable `config`
7. Runtime `status`
8. `outputs`
9. `artifacts`
10. `memory`
11. `scripts`
12. Optional `subspace`

## Required Design Questions
Each `WorkSpace` design should explicitly answer:

1. What upstream dependencies must exist before this node can run?
2. Does this node have an attached code repository?
3. What does this node's `program.md` need to specify?
4. Which fields are editable by humans?
5. Which fields are editable by the agent?
6. Which outputs are authoritative versus derived?
7. What should be preserved in `artifacts` for debugging or audit?
8. What should be preserved in `memory` for long-term context?
9. Which script entrypoints does this node need to expose?
10. What should appear in `summary` so a human can understand the node quickly?

## Recommended Design Format
Each module owner should write their `WorkSpace` program using the following format.

````md
# <WorkSpaceName> Program

## Purpose
<What this WorkSpace does and why it exists>

## WorkSpace Definition
```yaml
name: <workspace_name>
summary: >
  <human-readable summary>

repo:
  path: <optional_local_repo_path>

program: program.md

inputs:
  <input_name>:
    description: <what this input means>
    source: <where it comes from>

config:
  <config_name>:
    description: <what this config controls>
    editable_by:
      - human
      - agent

status:
  <status_name>:
    description: <what this status field means>

outputs:
  <output_name>:
    description: <what this output represents>

artifacts:
  - type: <artifact_type>
    path: <artifact_path_or_uri>
    producer: <workspace_name>
    description: <why this artifact matters>

memory:
  path: memory/
  description: <long-form notes, reports, and decisions for this node>

scripts:
  start: scripts/start.sh
  status: scripts/status.sh
  archive: scripts/archive.sh
  clean: scripts/clean.sh

subspace: {}
```

## Notes
- Upstream dependencies:
- Failure modes:
- Open questions:
````

## Example Skeleton

```yaml
name: data_composer
summary: >
  Data composition finished for the current iteration.
  Version v0.3 was produced from 3 sources.
  The next step is to launch training on the new mixture.

repo:
  path: ./subspace/data_composer

program: program.md

inputs:
  generated_tasks:
    description: SWE tasks produced by SWEGen
    source: swe_gen.outputs.generated_tasks
  external_datasets:
    description: external datasets selected by the project
    source: workspace.config.data_sources

config:
  mixture_policy:
    description: how different sources are weighted
    editable_by:
      - human
      - agent
  quality_threshold:
    description: minimum quality score for keeping a sample
    editable_by:
      - human
      - agent

status:
  phase:
    description: current stage of data composition
  health:
    description: whether the module is healthy, blocked, or failed
  progress:
    description: completion ratio for the current run

outputs:
  dataset_version:
    description: curated dataset version for downstream training
  rejected_samples:
    description: samples excluded during filtering

artifacts:
  - type: dataset_manifest
    path: artifacts/data_composer/v0.3/manifest.json
    producer: data_composer
    description: manifest for the curated dataset version

memory:
  path: memory/
  description: long-form notes and reports for data composition

scripts:
  start: scripts/start.sh
  status: scripts/status.sh
  archive: scripts/archive.sh
  clean: scripts/clean.sh

subspace: {}
```

## Design Constraints
- Do not invent a custom top-level structure for a `WorkSpace`.
- Do not move human-facing content out of `summary`.
- Do not mix dependency prerequisites into `config` if they are actually `inputs`.
- Do not mix storage references into `outputs` if they are actually `artifacts`.
- Use `repo` only when the node truly owns or depends on a concrete code repository.
- Treat `program.md` as the rule document for the current node, not as a generic project note.
- Use `memory` for deep context, not for short status updates that belong in `summary`.
- Use `subspace` as the only recursive child field name; do not reintroduce `children`.
- Keep `artifacts/` parallel to `outputs/`; do not hide artifact storage under logical outputs.
- Use `scripts/` as the executable interface of the node, not as a miscellaneous dump of helper files.

## Merge Standard
A `WorkSpace` design is ready to merge only if:
- its `inputs` are clearly defined
- its `repo` usage is clear or explicitly empty
- its `program` is defined
- its `config` ownership is clear
- its `status` can track real execution progress
- its `outputs` can be consumed by downstream nodes
- its `summary` is readable by humans
- its `artifacts` are sufficient for audit and debugging
- its `memory` strategy is clear
- its `scripts` entrypoints are clear
- its shape is still a valid `WorkSpace`
