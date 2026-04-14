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

### `artifacts`
- Archived file or object references
- Use this to record the stored evidence for outputs
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
├── inputs.yaml
├── config.yaml
├── status.yaml
├── outputs.yaml
├── artifacts/
│   ├── index.yaml
│   └── files/
├── memory/
│   ├── README.md
│   ├── notes.md
│   ├── decisions.md
│   └── reports/
└── subspace/
```

### Default Directory Meaning
- `associated_repo/`: optional associated repo directory for the current `WorkSpace`; create it only if the repo actually exists
- `program.md`: the local rule document for this `WorkSpace`
- `summary.md`: the short human-facing summary
- `inputs.yaml`: dependency inputs for the current node
- `config.yaml`: editable configuration owned by the node
- `status.yaml`: current runtime state
- `outputs.yaml`: structured outputs
- `artifacts/`: archived evidence and artifact indexes
- `memory/`: deeper long-form context behind the current node
- `subspace/`: nested child `WorkSpace` directories

## Default Root WorkSpace Layout For SWE
For the current SWE setup, the root `WorkSpace` can be organized like this:

```text
SWE-Lego-Live/
├── associated_repo/
├── program.md
├── summary.md
├── inputs.yaml
├── config.yaml
├── status.yaml
├── outputs.yaml
├── artifacts/
│   ├── index.yaml
│   └── files/
├── memory/
│   ├── README.md
│   ├── notes.md
│   ├── decisions.md
│   └── reports/
└── subspace/
    ├── SWE-gen/
    │   ├── associated_repo/
    │   ├── program.md
    │   ├── summary.md
    │   ├── inputs.yaml
    │   ├── config.yaml
    │   ├── status.yaml
    │   ├── outputs.yaml
    │   ├── artifacts/
    │   │   ├── index.yaml
    │   │   └── files/
    │   ├── memory/
    │   │   ├── README.md
    │   │   ├── notes.md
    │   │   ├── decisions.md
    │   │   └── reports/
    │   └── subspace/
    ├── data_composer/
    │   ├── associated_repo/
    │   ├── program.md
    │   ├── summary.md
    │   ├── inputs.yaml
    │   ├── config.yaml
    │   ├── status.yaml
    │   ├── outputs.yaml
    │   ├── artifacts/
    │   │   ├── index.yaml
    │   │   └── files/
    │   ├── memory/
    │   │   ├── README.md
    │   │   ├── notes.md
    │   │   ├── decisions.md
    │   │   └── reports/
    │   └── subspace/
    ├── training/
    │   ├── associated_repo/
    │   ├── program.md
    │   ├── summary.md
    │   ├── inputs.yaml
    │   ├── config.yaml
    │   ├── status.yaml
    │   ├── outputs.yaml
    │   ├── artifacts/
    │   │   ├── index.yaml
    │   │   └── files/
    │   ├── memory/
    │   │   ├── README.md
    │   │   ├── notes.md
    │   │   ├── decisions.md
    │   │   └── reports/
    │   └── subspace/
    └── harbor/
        ├── associated_repo/
        ├── program.md
        ├── summary.md
        ├── inputs.yaml
        ├── config.yaml
        ├── status.yaml
        ├── outputs.yaml
        ├── artifacts/
        │   ├── index.yaml
        │   └── files/
        ├── memory/
        │   ├── README.md
        │   ├── notes.md
        │   ├── decisions.md
        │   └── reports/
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
11. Optional `subspace`

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
9. What should appear in `summary` so a human can understand the node quickly?

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
  path: /mnt/haoli/code/SWE-Lego-Live/subspace/data_composer

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
- its shape is still a valid `WorkSpace`
